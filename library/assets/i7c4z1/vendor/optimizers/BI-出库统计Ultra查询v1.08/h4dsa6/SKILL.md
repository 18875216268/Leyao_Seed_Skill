---
name: BI-出库统计Ultra查询v1.08
description: "出库统计Ultra 板块优化 skill（自包含，可独立移植）。把「自助查询结果」巨卡（50维×33度）的查询抽象为高层 DSL（date/filters/dimensions/metrics/sort/page），支持批量并发、分页翻页、Top N 排序、区域树筛选与聚合导出。凭证经三级凭证窗口（环境变量/凭证文件/宿主登录器回退），本板不实现登录。触发词：出库统计、Ultra、自助查询结果、边际利润分析。"
---

# BI-出库统计Ultra查询 — 出库统计Ultra 板块优化 skill

> **框架生态提示**：本资产位于某包的 `library/assets/` 下 → 使用前**先回主框架**：上溯到含 `library/engine.py` 的包根，读其 `SKILL.md` 的〈最高准则〉→ `library/ROUTES.md` → 用户区记忆 / 默认资产卡 → 按 `processor/` 五步执行；**独立安装态**（本目录不在 `library/assets/` 下）不受此限——按本资产独立使用即可。

版本：v1.08（知识基线 2026-09-09；catalog **v4** 对齐运行时 **38** 筛选/50 维/33 指标——含 `sync_fields` 入册的「是否促销补贴」）

版本记录：
- v1.06（存档）：独立完整版（自带 DPAPI 登录栈，已废弃）
- v1.07：接入父框架——剥离登录栈改三级凭证窗口；树筛选键名修正 `fieldSeq→fields+values`；formula 退出一致性校验；自包含 API 契约；catalog 对齐运行时并实测新增字段（周/5W金额/L1~L3/省内单三/促销补贴/出库条目数）
- v1.08：凭证回退路径对齐纯登录框架（`scripts/login_bi.py`）；参数文档计数对齐（50 维/33 指标）；清理死代码；新增 `scripts/export.py` 板内导出器（引擎构造体 → UI 同款三步链，聚合导出实测通过并交叉验证）；**候选值去硬编码**——移除选项快照/刷新机制，改 `scripts/candidates.py` 实时获取 + 按当前登录用户缓存（TTL 24h，resources/candidate_cache.json）；树候选发现**条件返回**形态（`search`/`filters` 参数，空体才截断），`candidates.py --search` 支持树筛按关键字取完整子树；取数口径新增**查询/导出决策**准则（查询优先；并发查询+整理复杂度大于导表处理时优先导表分析）

## 何时使用

- 查询「出库统计Ultra」板块（页面 `a71ab51244ebd4a9296aa4de`，主卡 `v37695c5612944a7baa0c6fa`「自助查询结果-每天9点前更新至昨天数据」，50 维×33 度）的聚合数据。
- 需要批量并发、分页翻页、Top N 排序、区域树（省份-城市-区）筛选时**优先使用本板**。
- 其它板块或本板字段覆盖不了的字段：回退所属通道基础路径 `vendor/bi-cookie` 直读直查。

## 凭证（凭证窗口：本板不实现登录，只消费凭证）

本板可独立于任何父 skill 移植使用。执行查询前需要一份 BI 凭证，来源优先级：

1. 环境变量 `BI_UID_TOKEN` + `BI_UID_TOKEN_SIG`（可选 `BI_UID_EXP`，unix 秒）；
2. 凭证文件：环境变量 `BI_CREDENTIAL_FILE` 指向的 JSON，或本板
   `resources/credential.local.json`（`{"token":"…","tokenSig":"…","exp":1789…}`）；
3. 宿主父 skill 回退（可选）：向上存在 `scripts/login_bi.py` 时自动取其登录仓。

- 三级全空时返回 `AUTH_REQUIRED`，此时必须由用户直接传入凭证（窗口 1 或 2）。
- **获取方式**：宿主父 skill 登录器扫码（若有，skill 根 `python scripts/bi_login.py`），或由用户直接提供 token。
- 查询返回 `AUTH_*` 错误时先换新凭证再重试一次；不要因参数、权限、限流、网络错误触发换凭证。
- API 契约自包含于 [references/api查询文档.md](references/api查询文档.md)，移植无需父 skill 文档。

## 查询

先读 [references/parameters.md](references/parameters.md)，把用户需求转换为查询参数，
再将一个 UTF-8 JSON 对象通过 stdin 传入：

```text
python scripts/query.py
```

单查询也使用单元素 `queries`：

```json
{
  "concurrency": 10,
  "queries": [
    {
      "id": "query-1",
      "metrics": ["含税金额"]
    }
  ]
}
```

省份筛选和区域树必须使用完整行政区名称，例如 `重庆市`、`四川省`、`广西壮族自治区`。
标准透视模板见参数文档。

## 取数口径（AI 必遵守）

### 需求与规划

1. **用户需求至上**：先理解、再规划。需求不完全明确时，按用户方向给出完整建议——取数口径
   （筛选字段及筛选项、聚合维度、计算指标）、分析思路、预期结论、报告框架与风格；歧义点
   提供选项由用户决定，基于决定修正规划后继续。
2. **最小充分口径**：规划以能完全完成用户需求的最小取数口径为标准，不过度规划、也不减少实现。
3. **实施前自检**：自评"≥95% 把握完成任务、≥90% 满意度"，不足则先自行调整（最多 5 次）；
   仍不确定则与用户共同讨论，给出候选方案的优缺点与当前最合适解。

### 默认口径（除非用户明确要求）

4. **时间维度**：优先使用出库日期（未指定时默认出库日期本月第一天至昨天）；仅当用户明确要求，
   或分析明确涉及付款/回款时，才使用支付日期。
5. **地区口径**：涉及省份/地区时，默认指**当前子公司**在该省份/地区的销售数据，而非该地区
   本地公司或多子公司的数据；用户明确要求跨子公司对比或明细时才切换口径并说明。
6. **字段映射**：把"限定哪些数据"作为 `filters`、"按什么分组对比"作为 `dimensions`、
   "计算什么数值"作为 `metrics`；汇总用空 `dimensions`；Top N 用 `sort` + `desc/asc`。
   出库/支付日期、含税/不含税金额等会改变结果的真实歧义，只询问该项口径，不自行替换字段。

### 参数动态扩展（内置文档非固定、可扩展）

7. `references/parameters.md` 是**默认基础说明**，非固定清单——引擎每次查询实时拉取运行时
   元数据（按当前用户会话），语义字典外的新字段/筛选器**可直接使用**（结果 `warnings` 会提示
   "业务口径待补"）。
8. 内置参数不满足需求时，先经候选值/元数据接口动态查询更多参数（`candidates.py` / 页面元数据），
   确认可用后运行 `python scripts/sync_fields.py --write` 把动态发现的参数同步进内置文档
   （新字段业务口径需人工补写，见文档尾部"待补语义"）；发现内置参数不适用时同样同步更新。

### 执行规则

9. 相互独立的查询放入同一批次（`concurrency` 默认 10、最大 30）；结果按输入顺序返回，单项失败不影响其他项。
10. 全量取数：显式固定 `date` 并顺序翻页，仅修改 `page`，直到 `hasMoreData=false`；合并 `rows`，
    不重复累加各页 `summary`；同一查询的不同页不得并发。

### 查询与导出决策

11. **查询优先**：并发查询能快速得出较多结果时，优先使用并发查询获取数据。
12. **导表分析**：当所需数据的并发查询规模与后续整理复杂度**大于**直接导出表格处理的复杂度时
    （典型：大批量明细、多页翻页拼装、需在表格中再透视/整理的交付），优先用
    `scripts/export.py` 导表后本地分析，并向用户说明选择原因。
    两种路线都必须遵守导出红线（带筛选）与最小充分口径。

## 结果

顶层 `ok: false` 表示批次未执行；`ok: true` 时检查每个 `results` 项的 `ok`。成功项的
`data` 包含 `rows`、`summary`、`page`、`pageSize`、`totalCount`、`hasMoreData`、
`warnings` 和 `effectiveQuery`。

计算使用单元格的 `raw`，展示使用 `display`。不要把一页结果称为全量；只有
`hasMoreData` 为 `false` 才表示该查询已取完。

## 候选值（筛选项选项，按用户实时获取+缓存）

筛选项的具体选项**不内置**（随公司、账号权限与业务数据变化），一律实时获取并按当前登录用户缓存：

```text
python scripts/candidates.py --filter "业务类型"             # 取候选（缓存优先，TTL 默认 24h）
python scripts/candidates.py --filter "业务类型" --refresh   # 强制重新实测
python scripts/candidates.py --filter "省份-城市-区" --search 重庆   # 树筛按关键字取完整子树
python scripts/candidates.py --list                          # 当前用户的缓存概况
```

- 缓存按用户隔离（loginId），落盘 `resources/candidate_cache.json`（运行时数据，勿手工编辑）。
- 业务语义见 [references/parameters.md](references/parameters.md)「基础字段定义」；时间区间用 `date` 传值，区域树走 treeSelector。

## 导出（筛选聚合后导 Excel）

把「自选维度×指标 + 筛选」的查询结果导出为 xlsx，链路与观远 UI「离线导出」逐字节一致
（实测 2026-09-09，含内容交叉验证）：

```text
stdin 传与 query.py 同款 DSL 批次（取首个查询构造请求体）：
python scripts/export.py --out <输出.xlsx>
python scripts/export.py --task <taskId>      # 超时/中断续传
python scripts/export.py --list 20            # 导出中心任务列表（找回 taskId）
```

- 请求体由引擎构造（`QueryService._build`，与在线取数完全同体——filters + zoneFilter 克隆），
  导出的就是**筛选+聚合后的定制视图**（KB 级小表）；契约细节见
  [references/api查询文档.md](references/api查询文档.md) §4 三步链。
- **需要「明细 + 卡片默认布局」时（板材做不到 ✗）**：板内导出恒经 `QueryService._build`（**无条件带 `zoneFilter`**，结果只可能是自选透视体）→ 按 §2 板块降级，改用**通道导出器**（请求体**只带 `filters`、不带 `zoneFilter`** = 卡片保存布局 × 筛选后数据）：
  `python vendor/bi-cookie/scripts/bi_export.py --card v37695c5612944a7baa0c6fa --payload-file body.json`（`body.json` 至少含日期筛选；必须带筛选 ✗ 见 §红线；超时用 `--task <taskId>` 续传）
  （**两条路线分清**：**聚合路线**→可整体复用引擎构造体（含 `zoneFilter`）交 `bi_export.py --payload-file`；**明细+默认布局路线**→必须剥离 `zoneFilter`、只带 `filters` ✗）

⚠ 本卡**禁止无筛选直接导出**：服务端任务将长时间 `PROCESSING`（全量 50×33 透视过载）；请求体必须至少带日期筛选。需要明细大数据量时用筛选+卡片默认布局（结果可为百 MB 级）；需要聚合汇总时用引擎自选透视体（结果仅 KB 级）。

## 边界

- 本板自包含：API 契约（`references/api查询文档.md`）、字段字典（`references/parameters.md`）、
  板块锚点（`resources/profile.json` + `catalog.json`）、引擎（`scripts/`）全部随板携带；
  唯一外部输入是凭证（§凭证窗口）。
- `resources/` 三文件角色：`profile.json` = 板块锚点（页面/卡片/数据集 + 租户头兜底，手工定义）；
  `catalog.json` = 字段身份快照（防 BI 静默改口径的校验基准）+ 中文名/别名映射，由
  `sync_fields.py` 维护；`candidate_cache.json` = **运行时缓存**（按用户，可再生，交付/分享本板
  前删除）。请求头合并策略：凭证会话头优先，板块租户头兜底。
- 本板只访问 `https://bi.leyopharm.com` 的业务端点（页面元数据 / 卡片取数 / 导出链），
  端点白名单见 `scripts/bi_client/transport.py`。
- 本板不实现登录、不弹窗；不写入、不加密任何凭证（凭证文件由用户提供时自行负责保管）。
