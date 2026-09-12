# BI-出库统计Ultra查询 — API 查询文档（自包含 · 全参数 · 可抄样本）

> 本文件随本板走，**移植到任何环境无需父 skill 即可读懂并复现全部请求**。
> 范围：本板使用的 4 组端点（元数据 / 取数 / 校验 / 导出链）。集团其它页面与端点不在此列。
> 格式：参数目录用表格（穷尽字段/类型/必填），请求体用 JSON（可抄即跑）。
> 板块锚点（pageId/cardId/datasetId）已固化于 `resources/profile.json`，字段字典见 `references/parameters.md`。

## 0. 通用约定

- 主机：`https://bi.leyopharm.com`；鉴权：`Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>`（本板从凭证窗口读取，见 §5）。
- 基础头（本板 `resources/profile.json` 已固化）：`x-dom-id: Z3VhbmJp`、`raw-backend-response: TRUE`、`cache-control: no-cache`、`Content-Type: application/json`。
- **响应信封**：带 `raw-backend-response: TRUE` 时为 `{"result":"ok","response":{...}}`；裸调用为扁平结构（顶层即数据）。失败：HTTP 401/403/429/500 + `error.status`。
- 错误速查：

| 错误 | 含义 | 恢复 |
|---|---|---|
| 40002 TASK.cancelTimeout | 全量透视计算超时 | 缩日期范围 / 减少维度指标 / limit≤50 |
| 14001 | 数据量超 120MB | 加日期过滤 / 减少维度 |
| 5001 JsResultException / `None.get` | 键名或方法错 | 树筛选用 `fields`+`values`；filters 用完整键组（§2.1） |
| 401 / 1018 | 登录过期 | 换新凭证（§5 凭证窗口） |
| 403 | 无权限 | 换有权账号 |
| 429 | 限流 | 退避重试 |

## 1. 页面元数据 — `GET /api/page/{pageId}`

**用途**：取本板筛选器声明与主卡运行时字段声明。无查询参数。

**响应关键路径**（`response` 内）：

| 路径 | 内容 |
|---|---|
| `response.cards[]` | 页面上全部组件；本板主卡 = `cdId=={cardId}` 且 `cdType=="CHART"` |
| `主卡.content.meta.chartMain.zoneData.row[]` | 运行时维度声明（50 项，`key/fdId/name/fdType/isAggregated/calculationType`） |
| `主卡.content.meta.chartMain.zoneData.metric[]` | 运行时指标声明（33 项） |
| `主卡.content.meta.chartMain.zoneData.column[]` | 度量名占位（MPH，克隆 zoneFilter 时必须原样带上） |
| `筛选器.settings.asFilter.columnMappings[].targetFields[]` | 筛选器到本卡的映射（`cdId=={cardId}` 者为本板可用筛选） |

## 2. 卡片取数（核心） — `POST /api/card/{cardId}/data`

### 2.1 请求体全参数

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| offset | int | 否 | 分页起点 =(page-1)*limit |
| limit | int | 是 | 行数上限（≤10000；数据量超限 14001） |
| view | string | 是 | 固定 `GRID`（其它值被拒） |
| filters | array | 否 | 普通/日期筛选，元素见 §2.2 |
| treeFilters | array | 否 | 区域树筛选，元素见 §2.3 |
| zoneFilter | object | 否 | **透视布局重选**，规则见 §2.4；不传 = 卡片保存布局 |
| dynamicFieldFilters | array | 否 | 动态维度切换（本板未用） |
| dynamicParams / combinationFilters / layerTreeFilters | array | 否 | 传空数组即可 |
| headerSortings | array | 否 | 表头排序 |
| sorting / rowExpand / name / taskRequestId | – | 否 | 固定值/占位，本板固定传 `[]` / `null` / 卡名 / 随机串 |

### 2.2 filters[] 元素（完整键组，缺一即 5001）

| 键 | 说明 | 本板出库日期示例 |
|---|---|---|
| name | 字段显示名 | `出库日期` |
| fdId / dsId / cdId / fdType | 字段身份 | `t4115ca5bb1574cb39287216` / `ebc37edf77c8540cdad40b62` / `{cardId}` / `DATE` |
| filterType | 算子 | `BT`（区间）/ `IN`（枚举） |
| filterValue | 值数组 | `["2026-09-01","2026-09-08"]` |

> ⚠ 官方开放平台文档的 3 键简化写法（`name/filterType/filterValue`）**在本通道不可用**（实测 5001）。

### 2.3 treeFilters[] 元素（区域树）

```json
{
  "name": "省份-城市-区", "dsId": "<datasetId>", "cdId": "<cardId>", "sourceCdId": "<selectorCdId>",
  "filterType": "IN", "withPath": true,
  "fields": [{"name":"省","fdId":"…","dsId":"…","fdType":"STRING","metaType":"DIM"}, "…层级序列"],
  "values": [["重庆市","重庆市","渝中区"]]
}
```

> ⚠ 键名必须是 `fields`+`values`；`fieldSeq`/`filterValue` 实测 5001。`fields` 来自页面元数据中该树筛选器的声明。
>
> 树候选（treeSelector）**空体会截断**（`count=1000, exceedLimit=true`），但**支持条件返回**：
> 请求体带 `{"search":"<关键字>"}` 或 `{"filters":[…简化 3 键…]}` → 返回完整匹配子树（`exceedLimit=false`，
> 如 `search=重庆` → 重庆市完整区县级子树 count=51）。传 `values` 用完整行政区路径即可，路径无需出现在候选中。

### 2.4 zoneFilter —— 自选维度/指标（本板核心能力）

规则：**完整克隆**主卡 `zoneData`（`column` 原样保留），仅替换 `row`（维度）与 `metric`（指标）为所选字段声明。

```json
{
  "offset": 0, "limit": 100, "view": "GRID",
  "filters": [ "…§2.2…" ],
  "zoneFilter": {
    "zoneData": {
      "row": [{"key":"…","fdId":"…","name":"省份","fdType":"STRING","metaType":"DIM","isAggregated":false,"calculationType":"normal"}],
      "column": [{"key":"uozZlpKtFkgCEzfBmCMTIPkE","metaType":"MPH","name":"度量名"}],
      "metric": [{"key":"…","fdId":"…","name":"含税金额","fdType":"DOUBLE","metaType":"METRIC","isAggregated":false,"calculationType":"normal"}],
      "sorting": []
    }
  }
}
```

⚠ 手拼部分字段会让动态维度卡崩 `None.get`；必须整字段对象克隆。字段声明来源：§1 页面元数据（本板已固化到 `resources/catalog.json`，引擎自动校验一致性）。

### 2.5 最小可抄样本

```json
{"offset": 0, "limit": 50, "view": "GRID"}
```

```bash
curl -s -X POST "https://bi.leyopharm.com/api/card/<cardId>/data" \
  -H "Cookie: uIdToken=<UID_TOKEN>; uIdToken.sig=<UID_TOKEN_SIG>" \
  -H "x-dom-id: Z3VhbmJp" -H "raw-backend-response: TRUE" -H "Content-Type: application/json" \
  -d '{"offset":0,"limit":50,"view":"GRID","filters":[{"name":"出库日期","fdId":"t4115ca5bb1574cb39287216","dsId":"ebc37edf77c8540cdad40b62","cdId":"<cardId>","fdType":"DATE","filterType":"BT","filterValue":["2026-09-01","2026-09-08"]}]}'
```

**响应**：`response.chartMain`：`row.values`（维度行）、`column.values`（指标列）、`data[]`（数值）、`count/offset/limit/hasMoreData`、`summary`（总计）。取数用 `raw`，展示用 `display`。官方单次上限：20000 行 / 100 列。

## 3. 登录态校验 — `GET /api/validate-token`

无参数；有效返回 `{"result":"ok",...}`。401/1018 = 凭证过期，走 §5 换凭证。

## 4. 导出链（筛选聚合后导 xlsx）

**红线：本卡导出必须带筛选**——无筛选时服务端全量 50×33 透视需 25 分钟+，UI/脚本都会超时。

| 步骤 | 端点 | 说明 |
|---|---|---|
| ① 提交 | `POST /api/write/file/{cardId}?typeOp=EXCEL` | **请求体 = §2 取数体**（filters 必带；可带 §2.4 zoneFilter 克隆 → 导出自选聚合视图，实测 KB 级小表） |
| ② 轮询 | `GET /api/task/{taskId}` | 至 `status=="FINISHED"`（失败 `FAILED`）；`result.exportPath` |
| ③ 下载 | `POST /api/export/file/common/{taskId}` | 体 `{"time":"<ISO+08:00>","fileNameWithTime":true,"downloadFileName":"<名>"}`；响应为 xlsx 二进制流 |

辅助：`GET /api/task-offline/guandata/history?offset=0&limit=20&exportAsyncResourceType=CARD`（导出中心任务列表，找回 taskId）；`GET /api/export/file/excel/isFullData/{cardId}_{graphId}`（UI 预检用，脚本不需要）。

**自动化入口（本板自包含）**：`python scripts/export.py`——stdin 传与 query.py 同款 DSL 批次
（取首个查询构造请求体，filters/zoneFilter 一体），自动执行三步链并落盘 xlsx；
`--task <taskId>` 续传、`--list N` 查任务列表、`--timeout/--poll-interval` 调等待。

## 5. 凭证窗口（本板不实现登录，只消费凭证）

优先级：

1. 环境变量：`BI_UID_TOKEN` + `BI_UID_TOKEN_SIG`（可选 `BI_UID_EXP`，unix 秒）；
2. 凭证文件：`BI_CREDENTIAL_FILE` 指向的 JSON，或 `resources/credential.local.json`：`{"token":"…","tokenSig":"…","exp":1789…}`；
3. 宿主父 skill 回退（可选）：向上存在 `scripts/login_bi.py` 时自动取其登录仓；不存在则跳过。

三级全空时脚本返回 `AUTH_REQUIRED`。凭证获取：宿主父 skill 登录器扫码（若有），或用户从已登录会话直接提供。
