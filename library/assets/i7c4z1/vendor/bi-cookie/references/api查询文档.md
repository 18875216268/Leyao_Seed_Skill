# BI API 查询文档（全端点 · 全参数 · 可抄样本）

> 本文件是「构造成功请求」的接口事实源；概念、流程、错误恢复、已知限制见 `总文档.md`。
> 覆盖：登录校验 / 页面目录与元数据 / 卡片取数（全参数）/ 候选值 / 批量 / 动态参数 / 权限 / 用户 / **导出链与任务中心**（§15）。
> 格式约定：**参数目录用表格**（穷尽字段/类型/必填/枚举），**请求体与 curl 用 JSON**（可直接复制执行）。
> 版本：2026-09-09（新增 §15.4 巨卡导出实测、§15.5 UI 双路径与任务列表；§1 补官方限制与 public-api 差异）。

## 0. 起点地图

| 要做什么 | 看哪节 |
|---|---|
| 校验登录态 | §3 |
| 找页面 / 找卡片 | §4（目录）→ §5（页面）→ §6（卡片元信息） |
| **查某张卡能传哪些筛选参数** | **§6.1 / §6.2**（实时权威来源，无需静态清单） |
| 取数（核心） | §7 |
| 下拉候选值怎么取 | §8 |
| 树筛选候选值怎么取 | §9 |
| 筛选器怎么拼 | §2（filters / treeFilters / dynamicFieldFilters） |
| 值怎么传（单值/多值/区间/路径） | §2.7 |
| 动态维度怎么切 | §2.6 + §6.3 |
| 批量取数 | §10（实测不可用，逐张调 §7） |
| 动态参数 / 权限校验 / 当前用户 / PAT | §11 / §12 / §13 / §14 |
| **出库统计Ultra 板块聚合查询**（批量并发/分页/区域树） | 优先用优化板 `vendor/optimizers/BI-出库统计Ultra查询v1.08/`（路由见 `vendor/SUBSKILL_ROUTING.md` §5） |
| **导出 Excel 表格** | §15（三步异步链，自动化于 `vendor/bi-cookie/scripts/bi_export.py`） |
| 登录换证链 | §16（仅登录模块使用） |

## 1. 通用约定

- 主机：`https://bi.leyopharm.com`；鉴权：`Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>`（登录器自动注入）。
- **响应信封取决于是否带 `raw-backend-response: TRUE` 头**（实测两种都 200）：
  - **发送器（`bi_call.py`）带该头** → 信封 `{"result":"ok","response":{...}}`；成功判断 = `result=="ok"` 且 `response.chartMain` 存在。
  - **裸 curl 不带该头** → 扁平响应，顶层即 `chartMain`（文档旧述「扁平无 result」即指此形态）。
  - 失败一律：HTTP 500/401/403/429 + `error.status` / `error_code`。
- **方法纠偏（实测，必须遵守）**：

| 端点 | 正确方法 | 错用后果 |
|---|---|---|
| `/api/dynamic-parameter/query` | **GET** | POST → 5001 |
| `/api/selector/{id}/data` | **POST** | GET → 5001 |
| `/api/treeSelector/{id}/data` | **POST** | GET → 5001 |
| `/api/resource-authenticate/...` | POST，参数名 **`resources`** | 用 `resourceIds` 无效 |
| `/api/user/token`（登录换证） | **GET**，且必须先访问回调首页建会话 | 直接 POST → 401/1018 |

- **错误码与恢复**：

| 错误 | 含义 | 恢复 |
|---|---|---|
| 1017 loggedInOnOtherDevice | 单点被顶 | 重新企微扫码 |
| 401 / 1018 | 登录过期 | 重新企微扫码 |
| 5001 JsResultException | 键名/方法错 | 读错误体缺失路径反推；树筛选用 `fields`+`values` |
| 40002 TASK.cancelTimeout | 计算超时 | 日期缩到一周内 + `limit`≤50 |
| 14001 | 数据量超 120MB | 加日期过滤 / 减少维度 |
| 1008 | 行权限引用不存在字段 | 需管理员修复，跳过该卡 |
| 1002 | 找不到页面 | `/api/page/tree` 裸调必现，改用 `/api/page-v3` |
| 1004 | 无权访问 | 换有权账号 |
| 403 | PAT 端点用了 uIdToken | 需 `X-Personal-Token` |

- **官方限制（观远开放平台文档，2026-09 版）**：单次取数 `dataLimit 20000` 行、`colLimit 100` 列；`filterType` 为受控枚举（GT/LT/IN/BT…），`view` 取值 `GRID/GRAPH`。
- **官方公开文档 ≠ 内网通道（实测 2026-09-09）**：官方文档（`api.guandata.com` 开放平台）描述的是 `POST /public-api/card/{cardId}/data` + `X-Auth-Token` 鉴权的通道，其 `filters` 仅需 3 键 `{"name","filterType","filterValue"}`。**内网 `/api/card/{cardId}/data` 实测拒收该简化写法（5001 `None.get`），必须用完整键组（见 §2.1：name/fdId/dsId/cdId/fdType/filterType/filterValue）**。两通道不通用，勿混淆。
- **`/public-api` 通道（实测在线，暂不使用）**：租户开放平台鉴权层已激活（无效 token 返回 `1018`/`403 no token provided` 而非 404）。该通道用管理后台生成的静态 `X-Auth-Token`，不依赖企微扫码会话（可避开 1017 顶号 / Cookie 过期）。但需管理员签发 Token，且官方不开放自选维度/指标（body 仅有 `dynamicParams/filters/offset/limit/view`）——**即便走此通道，透视卡自选维度/指标仍需 zoneFilter 克隆引擎**。当前继续使用 Cookie 通道。

## 2. 筛选器通用结构（§7/§8/§9 共用）

### 2.1 filters[] 元素（普通 / 下拉筛选）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| name | string | 是 | 目标卡上的字段显示名（来自筛选器 columnMappings 的 targetField.name） |
| fdId | string | 是 | 目标字段 fdId |
| dsId | string | 是 | 目标字段所属数据集 dsId |
| cdId | string | 是 | 目标卡 cardId |
| fdType | string | 是 | 见 §2.2 |
| filterType | string | 是 | 算子，见 §2.4 |
| filterValue | array | 是 | 筛选值；`BT` 为 `[start,end]`；树为路径数组 |
| displayValue | array | 否 | 显示值（通常同 filterValue） |
| sourceCdId | string | 否 | 来源筛选器卡片 cardId（联动标识） |
| filterLevel | string | 否 | `DETAIL`（行级，最常用）/ `AGGREGATION` / `RESULT` |
| operator | string | 否 | `include` / `exclude` |
| granularity | string | 否 | 日期字段粒度 YEAR/MONTH/DAY |
| macroName | string | 否 | 时间宏名（走 `TIME_MACRO` → `BT` + [start,end]） |

### 2.2 fdType 枚举

`STRING` / `DOUBLE` / `LONG` / `INT` / `DATE` / `SUB_DATE` / `DECIMAL` / `BOOL` / `TIMESTAMP` / `FLOAT` / `SHORT`

### 2.3 filterLevel 枚举

`DETAIL`（明细行级，最常用）/ `AGGREGATION`（聚合后）/ `RESULT`（结果集上）

### 2.4 filterType 枚举（623 条内置筛选实测统计）

| filterType | 含义 | 实测次数 |
|---|---|---|
| `IN` | 在集合内（多选包含） | 374 |
| `NI` | 不在集合内（NOT IN） | 135 |
| `CUSTOM` | 自定义条件（日期区间/表达式） | 28 |
| `SPARK_EXPR` | Spark 表达式过滤 | 26 |
| `EQ` | 等于 | 22 |
| `GT` | 大于 | 17 |
| `GE` | 大于等于 | 8 |
| `LE` | 小于等于 | 4 |
| `LT` | 小于 | 4 |
| `NOT_CONTAINS` | 不包含（字符串） | 2 |
| `NOT_NULL` | 非空 | 2 |
| `NE` | 不等于 | 1 |
| `BT` | 区间 between（日期宏常用） | 日期宏请求体使用 |

### 2.5 treeFilters[] 元素（树筛选）

> ⚠️ **键名修正**：直连要求 `fields`（层级字段序列）+ `values`（路径数组）。照抄旧技能的 `fieldSeq`/`filterValue` 会返回 5001（错误体会明示缺失路径）。

| 字段 | 说明 |
|---|---|
| name | 筛选器名 |
| dsId / cdId / sourceCdId | 同 filters[] |
| filterType | 通常 `IN` |
| withPath | `true`（带层级路径） |
| **fields** | 层级字段序列；每项含 name/fdId/dsId/fdType/metaType(`DIM`) |
| **values** | 路径数组，如 `[["广东省","深圳市"]]`；省份用完整行政区名，城市须以“市/自治州/地区/盟”结尾 |

### 2.6 dynamicFieldFilters[] 元素（动态维度切换）

| 字段 | 说明 |
|---|---|
| dzId | 动态维度组 id（卡片 `content.dynamicZoneInfo.dzMappings`） |
| key | 要切换到的维度字段 key（同组另一个 key = 切到另一维度） |
| sourceCdId | **目标卡自身 cardId（自引用，实测确认）** |

### 2.7 传值类型速查（单值 / 多值 / 区间 / 路径）

所有筛选的「值」都放在 `filterValue`（树筛选为 `values`），**类型由 `filterType` 决定**：

| 场景 | filterType | 传值写法 | 类型 |
|---|---|---|---|
| 单选/等值 | `EQ` | `["成本不优"]` | 数组（单元素） |
| 多选包含 | `IN` | `["成本不优","成本优"]` | 数组（**多值直接并列**，实测 200） |
| 排除 | `NI` / `NE` | `["值"]` | 数组 |
| 区间 | `BT` | `["2026-09-01","2026-09-08"]` | 数组，`[start,end]` 两元素 |
| 比较 | `GT`/`GE`/`LT`/`LE` | `["100"]` | 数组（单元素） |
| 非空 / 空 | `NOT_NULL` | `[]` | 空数组 |
| 字符串不包含 | `NOT_CONTAINS` | `["关键字"]` | 数组 |
| 自定义表达式 | `CUSTOM` / `SPARK_EXPR` | 按卡片声明的 `filterValue` 原样传 | 数组 |
| **树路径（单值）** | treeFilters `IN` | `values: [["广东省","深圳市"]]` | **二维数组** |
| **树路径（多值）** | treeFilters `IN` | `values: [["广东省","深圳市"],["广东省","广州市"]]` | 二维数组，多路径并列 |
| 空值选项 | `IN` | `[null]` | 数组可含 `null` |

**要点**：`filterValue` 永远是**数组**；即使单值也要写成 `["值"]`。日期用字符串，数值用数字或数字字符串（以卡片 `fdType` 为准）。

### 2.8 日期筛选注意事项

- `DATE` 类型：用字符串区间 `["2026-08-25","2026-08-31"]`。
- `SUB_DATE` 类型：**存的是数值序列**（如 Excel 日期戳），`BT` 必须用数值区间，用字符串日期会匹配为空。
- 超大卡（如 50 维×33 度）宽日期区间（20 个月）会 `40002`，缩到一周内 + `limit` 50 即可。

---

## 3. 校验登录态 — `GET /api/validate-token`

**用途**：确认当前 Cookie 是否有效（取数前先跑）。

**请求**：无参数，仅 Cookie。

```bash
curl -s "https://bi.leyopharm.com/api/validate-token" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>"
```

**响应关键字段**：`{"response":"success"}` 即有效。

**常见错**：`1017` = 被其他设备顶掉 → 重新扫码（token 未过期也会发生）。

---

## 4. 目录树 — `GET /api/page-v3`

**用途**：取页面/文件夹目录，找 `pageId`（**推荐**，替代恒 1002 的 `/api/page/tree`）。

**参数**：可省略（实测带不带 `resourceOnly`/`pageId` 返回相同全树）。

```bash
curl -s "https://bi.leyopharm.com/api/page-v3" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>"
```

**响应关键字段**：顶层 `{"id","name","parentDirId","isPage","canUse","canManage","ctime","contents"}`；`contents[]` 为下级节点（实测顶层 6 项，随账号权限变化）。

---

## 5. 页面详情 — `GET /api/page/{pageId}`

**用途**：取某页全部卡片与配置（找 `cardId` 的权威入口）。

**路径参数**：`pageId` 来自 §4。

```bash
curl -s "https://bi.leyopharm.com/api/page/e1de5801838b341da999e58c" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>"
```

**响应关键字段**：`pgId` / `name` / `cards[]`。**只挑 `cdType:"CHART"` 的 `cdId`**（SELECTOR/TREE_SELECTOR 是筛选器控件，TEXT 是文本）。

---

## 6. 卡片元信息 — `GET /api/card/{cardId}`（**每卡查询参数的权威来源**）

**用途**：取单卡配置——**这张卡能传哪些筛选参数、有哪些维度/度量、是否可切维度**，全部由本接口实时给出。这是**权威来源**。

> **快速检索**：日常查询优先用 `python vendor/bi-cookie/scripts/bi_index.py --search <关键词>` / `--card <cardId>`
> （离线索引，命中即返回、过期自动补新）；本接口用于索引未覆盖或需要权威确认的场景。

```bash
curl -s "https://bi.leyopharm.com/api/card/{cardId}" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>"
```

### 6.1 响应关键路径（实测确认）

| 用途 | JSON 路径 | 说明 |
|---|---|---|
| 卡片名 / 类型 | `name` / `cdType` | `cdType=="CHART"` 才是数据卡 |
| 图表类型 | `content.chartType` | 如 `PIVOT_TABLE` / `DETAIL_TABLE` / `KPI_CARD` |
| 数据集 | `content.dsId` / `content.dsInfo` | 字段归属 |
| **每卡可传筛选（内置筛选声明）** | **`content.meta.chartMain.zoneData.filters[]`** | **每卡查询参数就在这里**（注意：不是 `content.builtInFilters`，该字段实测为 null） |
| 维度 / 度量分区 | `content.meta.chartMain.zoneData` 下的 `row[]` / `column[]` / `metric[]` / `sorting[]` | 分区随 `chartType` 不同（如 DETAIL_TABLE 只有 `metric`/`sorting`/`filters`） |
| **可切换的动态维度组** | `content.meta.chartMain.dynamicZoneInfo.dzMappings[]` | 非空即动态维度卡，可用 `dynamicFieldFilters` 切维度 |

### 6.2 `zoneData.filters[]` 元素（每卡可传筛选的声明形状）

| 字段 | 说明 |
|---|---|
| name | 筛选字段名（传 filters 时的 `name`） |
| fdId / dsId | 字段 id / 数据集 id |
| fdType | 字段类型（见 §2.2） |
| filterType | 卡片默认算子（见 §2.4） |
| filterValue | 卡片默认值（数组） |
| filterLevel | `DETAIL` / `AGGREGATION` / `RESULT` |
| operator / granularity | 可选，同 §2.1 |

> 用法：把这里的 `name/fdId/dsId/fdType` 原样填进取数请求体的 `filters[]`，再用 §2.4 选算子、`filterValue` 填值。
> 若 `filters[]` 为空数组，说明该卡没有内置筛选，只能整表取数（可用 `limit` 控制）。

### 6.3 `dynamicZoneInfo.dzMappings[]` 元素（动态维度）

| 字段 | 说明 |
|---|---|
| dzId | 动态维度组 id → 对应 `dynamicFieldFilters[].dzId` |
| name | 维度组名（如「日/周/月」） |
| defaultValue | 默认选中的 key 列表 |
| multiSelect | 是否可多选 |
| selectType | 控件类型（如 `SEARCHBOX`） |
| zoneId | 作用的分区（`row` / `metric` …） |

> 切换维度：取同组内另一个 key，按 §2.6 传 `dynamicFieldFilters`（`sourceCdId` = 该卡自身 cardId）。

---

## 7. 卡片取数（核心） — `POST /api/card/{cardId}/data`

**用途**：统一取数入口，所有 CHART 卡都走这里。

**必读前提**：`cardId` 来自 §5；要筛选时先 §6 取 `fdId`/`dsId` 与内置筛选，再按 §2 拼装。

### 7.1 请求体参数（全字段）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| offset | int | 否 | 分页起点 =(page-1)*limit；官方 CLI 实测可省 |
| limit | int | 是 | 本次取数行数上限（图表默认最多 10000；数据集超限 120MB 触发 14001） |
| view | string | 是 | 视图模式，**固定 `GRID`**（CHART/TABLE/PIVOT/DETAIL/RAW 均被校验拒绝） |
| filters | array | 否 | 普通/下拉筛选，元素见 §2.1 |
| treeFilters | array | 否 | 树筛选，直连键名 `fields`+`values`，见 §2.5 |
| dynamicFieldFilters | array | 否 | 动态维度切换，`sourceCdId`=自身 cardId，见 §2.6 |
| dynamicParams | array | 否 | 动态参数（旧卡回退 `/api/dynamic-parameter/query`） |
| combinationFilters | array | 否 | 组合筛选 |
| layerTreeFilters | array | 否 | 层级树筛选 |
| headerSortings | array | 否 | 表头排序 |
| rowExpand | null | 否 | 行展开（透视下钻） |
| sorting | array | 否 | 排序（按列排序由调用方侧执行，请求体仅放大 limit） |
| name | string | 否 | 卡片名（GUANCLI 已省略） |
| zoneFilter.zoneData.row/column/metric/sorting | object | 否 | ⚠ **默认一律省略**（服务端按卡片保存配置解析）。仅当需要在透视卡上重选维度/指标时，才允许**完整克隆卡片自身声明**（`GET /api/card/{cardId}` 的 `zoneData` 原样回传，参考优化板 `BI-出库统计Ultra查询` 的引擎做法）；手工拼部分字段会让动态维度卡崩 `None.get` |
| taskRequestId | string | 否 | 幂等请求 ID（GUANCLI 已省略） |

> **关键实证**：官方 CLI 实际只发 `limit` + `view` 两个字段；服务端按卡片保存的分区配置解析取数。**不要手工拼 zoneFilter**。

### 7.2 最小可抄请求体

```json
{ "offset": 0, "limit": 200, "view": "GRID" }
```

### 7.3 真实可抄 curl（示例卡「品种明细」）

```bash
curl -s -X POST "https://bi.leyopharm.com/api/card/s42cae449793240559e10b82/data" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>" \
  -H "Content-Type: application/json" \
  -d '{"offset":0,"limit":200,"view":"GRID"}'
```

### 7.4 带 IN 过滤的可抄 curl

```bash
curl -s -X POST "https://bi.leyopharm.com/api/card/s42cae449793240559e10b82/data" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>" \
  -H "Content-Type: application/json" \
  -d '{"offset":0,"limit":200,"view":"GRID","filters":[{"name":"是否成本不优","fdId":"h0e8ec788522c4126818fcc5","dsId":"k850db266879f4f62a9dd698","cdId":"s42cae449793240559e10b82","fdType":"STRING","filterType":"IN","sourceCdId":"a063f278a661e4bb2aa7b18b","filterValue":["成本不优"],"displayValue":["成本不优"]}]}'
```

### 7.5 响应关键字段

| 字段 | 说明 |
|---|---|
| response.chartMain | 图表主数据（成功必含；不带 raw 头时顶层即为 chartMain） |
| response.chartMain.row.meta[] / column.meta[] | 回显的维度/度量字段（title/fdId/fdType/metaType） |
| chartMain.data[] | 二维值矩阵 |
| chartMain.count | 总行数 |
| hasMoreData / limitInfo | 分页与限制信息 |
| summary（KPI 类） | KPI_CARD 的指标值 |
| chartType / cardType / view / performanceAnalysis | 卡片类型与执行信息 |

**常见错**：40002（缩区间/limit）、14001（缩数据量）、5001（键名错，读错误体反推）、1008（需管理员修复）。

### 7.6 超大/超时卡的实测可用方法（2026-09-09 复测）

| 卡片 | 极简请求 | 可用方法（已验证 200） |
|---|---|---|
| `v37695c5612944a7baa0c6fa`（40002，50 维×33 度） | ❌ 40002 | 「出库**日期**」BT 仍 40002；改用「出库**月份**」+ `limit` 50 → **成功** |
| `e1861ab0f632c475ba24474e`（14001，135MB） | ❌ 14001 | 近 7 天「出库日期」BT + `limit` 50 → **成功** |
| `m034961e8014c4e4f8b62a00`（14001，368MB） | ❌ 14001 | 元数据无 DATE 字段，无法用日期缩小 → 需管理员调整上限 |

> 经验：先加日期 BT + `limit` 50；仍 40002 时改用**月粒度字段**（聚合更省算力）。

---

## 8. 候选值-下拉筛选器 — `POST /api/selector/{selectorCardId}/data`

**用途**：取 SELECTOR 的下拉选项（候选值**不在**卡片取数响应里，必须单独调本接口）。

**请求体**：传 `{}` 即可。**实测**：`offset` / `limit` / `filters` / `keyword` 等入参**均被服务端忽略**
（无论传什么都返回同一份全量候选值，`limit` 恒为 1000），因此无需构造参数。

```bash
curl -s -X POST "https://bi.leyopharm.com/api/selector/a063f278a661e4bb2aa7b18b/data" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>" \
  -H "Content-Type: application/json" -d '{}'
```

**响应字段**（信封内 `response`）：

| 字段 | 说明 |
|---|---|
| count | 候选值总数 |
| exceedLimit | 是否超出上限（候选值被截断时排查用） |
| offset / limit | 服务端回显（恒 0 / 1000，不受入参影响） |
| result[] | 候选值数组，取 `result[].value` |

**示例**：`{"count":2,"exceedLimit":false,"offset":0,"limit":1000,"result":[{"value":null},{"value":"成本不优"}]}`
→ 候选值 `[null, "成本不优"]`（`null` 代表「空值」选项，传入时照传 null）。

**常见错**：用 GET → 5001（必须 POST）。

---

## 9. 候选值-树筛选器 — `POST /api/treeSelector/{treeSelectorCardId}/data`

**用途**：取 TREE_SELECTOR 的省→市树。

```bash
curl -s -X POST "https://bi.leyopharm.com/api/treeSelector/{treeSelectorCardId}/data" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>" \
  -H "Content-Type: application/json" -d '{}'
```

**响应字段**（信封内 `response`）：`count`、`exceedLimit`、`result[]`（树）。
树节点：`key`（如 `云南省_0`）、`children[]`（子级，可能再嵌套或为 null）、`displayValue`（显示名）、`dvt`（值）。
`offset` / `limit` 在树接口恒为 null（树不支持分页）。

> ⚠ **树候选：空体截断，支持条件返回（实测 2026-09-09）**：空体 `count=1000, exceedLimit=true`
> （仅部分省份，含 `None` 空值根与非标区名）；请求体带 `{"search":"<关键字>"}` 或
> `{"filters":[…]}` → 返回**完整匹配子树**（`exceedLimit=false`，如 search=重庆 → count=51 全区县）。
> 按完整行政区路径传值不受影响（路径无需出现在候选中），直辖市第 2 级与省级同名。

**示例**：`{"count":26,"exceedLimit":false,"offset":null,"limit":null,"result":[{"key":"云南省_0","children":[{"key":"临沧市_1","children":null,"displayValue":"临沧市","dvt":"临沧市"}]}]}`

**传入形态**：见 §2.5（用 `fields`+`values`，如 `[["广东省","深圳市"]]`）；
`values` 是**路径数组的数组**——可一次传多个路径实现多值，如 `[["广东省","深圳市"],["广东省","广州市"]]`。

---

## 10. 批量取数 — `POST /api/card/data/batch`

**用途**：一次取多卡。**实测确认**：返回 `{"result":"ok","response":[]}`（**恒为空数组**，不返回数据）。

| 请求体字段 | 类型 | 说明 |
|---|---|---|
| cards | array | 每项 `{"cardId": "<卡id>", "body": {取数请求体}}`；`body` 与 §7 单卡取数体完全一致（`offset`/`limit`/`view`/`filters`…） |

```bash
curl -s -X POST "https://bi.leyopharm.com/api/card/data/batch" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>" \
  -H "Content-Type: application/json" \
  -d '{"cards":[{"cardId":"<CARD_ID>","body":{"offset":0,"limit":100,"view":"GRID"}}]}'
```

> **结论**：本账号该端点不返回数据，**多卡取数请逐张调用 §7 单卡 `/data`**（循环即可），不要依赖批量端点。

---

## 11. 动态参数查询 — `GET /api/dynamic-parameter/query`

**用途**：查全局动态参数值（旧卡 fallback）。**实测确认**：GET 恒 `error.status 1004`「无权访问」（本账号无数据集参数权限）；POST 则 5001「method not supported」——**方法没错，是账号权限限制，不要重试**。

| 参数 | 位置 | 说明 |
|---|---|---|
| paramId | query | 动态参数 id |

```bash
curl -s "https://bi.leyopharm.com/api/dynamic-parameter/query?paramId=<ID>" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>"
```

**常见错**：POST → 5001；本账号 1004 无权限（换有权账号）。

---

## 12. 权限批量校验 — `POST /api/resource-authenticate/batch-get-resource-permission`

**用途**：批量校验资源权限；真实权限信号仍以单卡 `/data` 是否 403 为准。

| 参数 | 说明 |
|---|---|
| resources | **数组**（参数名不是 resourceIds）；元素 `{resourceId, resourceType}` |

```bash
curl -s -X POST "https://bi.leyopharm.com/api/resource-authenticate/batch-get-resource-permission" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>" \
  -H "Content-Type: application/json" \
  -d '{"resources":[{"resourceId":"<CARD_ID>","resourceType":"CARD"}]}'
```

**注意**：`CARD` 类型本账号返回 `TagRecordTypeNotSupport`。

---

## 13. 当前用户 — `GET /api/user/profile`

**用途**：确认“扫码登录的是谁”（token 本身不含账号字段）。

```bash
curl -s "https://bi.leyopharm.com/api/user/profile" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>"
```

**响应**：`{"id":3320,"uId":"xf727d902...","name":"刘蕊成",...}`（身份字段 uId/loginId/name/email/mobile/role）。

---

## 14. PAT 自检 — `GET /api/pat/introspect`

**用途**：校验 PAT 是否可用。**受限：需 `X-Personal-Token` 头，uIdToken 调用 403**。

```bash
curl -s "https://bi.leyopharm.com/api/pat/introspect" \
  -H "X-Personal-Token: <PAT>"
```

**响应（无 PAT 时）**：`{"error_code":403,"error_message":"Forbidden"}` → 本环境无 PAT，不要反复重试。

---

## 15. 卡片导出 Excel（三步异步链，自动化于 `vendor/bi-cookie/scripts/bi_export.py`）

> 业务侧**不要手工拼这三步**——用 `python vendor/bi-cookie/scripts/bi_export.py --card <cardId>` 一步完成。
> 本节记录契约，供理解与排障。

### 15.1 提交任务 — `POST /api/write/file/{cardId}?typeOp=EXCEL`

**请求体**：与取数请求体一致（§7 的 `offset/limit/view/filters/treeFilters/dynamicFieldFilters…`）。
**说明**：导出为**卡片全量数据**（不受取数 `limit` 限制）。

```bash
curl -s -X POST "https://bi.leyopharm.com/api/write/file/{cardId}?typeOp=EXCEL" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>" \
  -H "Content-Type: application/json" \
  -d '{"offset":0,"limit":1000,"view":"GRID"}'
```

**响应**（信封内 `response`）：

| 字段 | 说明 |
|---|---|
| taskId | 导出任务 id（后续轮询与下载的钥匙） |
| status | 初始 `PROCESSING` |
| fileName | 服务端内部文件名 |
| postBody.downloadFileName | 卡片显示名（用作下载文件名） |

### 15.2 轮询任务 — `GET /api/task/{taskId}`

**响应**（信封内 `response`）：`taskId` / `status` / `startTime` / `finishedTime` / `runningDuration`。
**终态**：成功为 **`FINISHED`**（注意不是 SUCCESS）；失败为 `FAILED`。轮询间隔建议 3 秒。

### 15.3 下载 — `POST /api/export/file/common/{taskId}`

**请求体**：`{"time":"<ISO时间+08:00>","fileNameWithTime":true,"downloadFileName":"<卡名>"}`（后两项可选）。
**响应**：**二进制 xlsx 流**（`application/octet-stream`，文件头 `PK`），非 JSON——需流式写盘。

**常见错**：任务未到 `FINISHED` 就下载会拿到空/错文件；等待超时可用 `--task <taskId>` 继续下载（无需重新提交）。

### 15.4 巨卡导出实测（2026-09-09，出库统计Ultra 主卡）

**导出请求体与取数请求体同协议**——因此取数侧的全部结论（筛选、zoneFilter 克隆）直接适用于导出：

| 实验 | 请求体 | 结果 |
|---|---|---|
| 无筛选直接导出 | `{offset,limit,view}` | ❌ 提交成功但任务 20+ 分钟持续 `PROCESSING` 不终态（全量 50×33 透视计算过载，与取数 40002 同源） |
| 带日期筛选 | 同上 + `filters[]`（完整键组） | ✅ 90s `FINISHED`，导出 106MB（卡片保存布局 × 筛选后数据） |
| **自选聚合透视** | 引擎同款体：`filters[]` + **`zoneFilter` 克隆（自选维度/指标）** | ✅ 90s `FINISHED`，导出 **5KB**——就是「省份 × 含税金额+出库条目数」定制聚合表，数值与在线取数交叉一致 |

**结论**：① 巨卡导出必须带筛选（日期收窄数据量）；② 导出体可携带 zoneFilter 克隆体——**「筛选+自选聚合后导出」完整可行**，自动入口：用优化板引擎构造请求体（`QueryService._build`）后交 `bi_export.py --payload-file`。

---

## 16. 登录换证（仅登录模块使用） — `GET /api/user/token`

> 业务取数不需要调用本节；这是 `scripts/bi_login.py` 的换证链，此处记录只为契约完整。

**正确顺序（实测 v1.02）**：

1. 企微扫码确认后拿到 `auth_code`；
2. **先访问 BI 回调首页**建立会话：`https://bi.leyopharm.com/?provider=wechatwork&agentId=1000305&corpId=wx8c9fab123dec4357&domain=guanbi&code=<AUTH_CODE>&state=loginState`；
3. 再**无参** `GET /api/user/token` → `{"uIdToken":"<JWT>"}`，同时 Set-Cookie 下发 `uIdToken` / `uIdToken.sig`。

**常见错**：跳过第 2 步、直接 POST `/api/user/token` 带 `{code,...}` → HTTP 401 + `error.status 1018`（Not Login or token expired）。
