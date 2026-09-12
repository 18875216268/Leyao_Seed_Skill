---
name: bi-pat
description: "观远 BI PAT SQL 通道（集团级第 2 通道）：用 X-Personal-Token 静态令牌对数据集直接执行 SQL（guancli ds execute-sql / REST），支持跨板块自由聚合与临时 ETL。仅限已领取 PAT（gdpat_）的特定权限用户；触发词：SQL 查数、数据集直查、跨板块分析、guancli、PAT。"
---

# bi-pat —— 观远 BI PAT SQL 通道

**通道定位**：集团级第 2 通道，与 Cookie 卡片通道（`vendor/bi-cookie/`）平行。
通道选择、权限分流、重大数字验证规则见 `vendor/SUBSKILL_ROUTING.md` §3/§6。
本包自包含：凭证窗口 + 手册 + 纪律，可独立移植。

## 何时使用本通道

- 需要跨板块/跨数据集的自由聚合，或卡片/页面视角覆盖不了的 SQL 级分析。
- 重大数字需要与 Cookie 通道交叉验证（四道闸第 4 条）。
- **前提：用户持有 PAT**（`gdpat_` 开头令牌）。无 PAT 用户直接引导其领令牌或回退 Cookie 通道，不试探。

## 凭证窗口（本包不实现登录，只消费令牌）

PAT 由用户在 BI 页面 `https://bi.leyopharm.com/personal-access-token` 自助领取（只显示一次）。来源优先级：

1. 命令行 `--token <gdpat_…>` 显式传入；
2. 环境变量 `BI_PAT_TOKEN`；
3. 凭证文件：环境变量 `BI_PAT_CREDENTIAL_FILE` 或本包 `resources/credential.local.json`（`{"pat":"gdpat_…"}`）。

三级全空返回 `AUTH_REQUIRED` 并引导领取。令牌即账号权限，**绝不写入文档、日志或输出**。

## 查数

1. 读取 [references/业务手册.md](references/业务手册.md)（问数方式、技巧、可信度五问）。
2. 读取 [references/api与cli.md](references/api与cli.md)（guancli 命令、鉴权头、数据集字典、四道闸）。
3. 执行 `python scripts/pat_call.py --sql "<SQL>" --ds <dsId>`（自动完成 guancli 登录态检查）；
   REST 端点可用时优先 `**（`--rest` 未实现，勿用 ✗）**`（免 Node 依赖，见 api与cli.md §3）。

## 强制规则（四道闸，SQL 直查亿级表的护栏）

1. **维度探名**：查不到先 `SELECT DISTINCT <维度列> … LIKE '%关键字%'` 探实际注册名，禁止直接报"没有数据"。
2. **口径拆解**：含销售类型的表先 GROUP BY 拆开，防内部调拨混入（问"线上"必须显式过滤）。
3. **时效检查**：聚合带 MIN/MAX 日期列；关注 isTruncated 截断标记；月度数据 T+1。
4. **重大数字双路径验证**：`execute-sql` 与临时 ETL（或与 Cookie 卡片通道）交叉一致才采信；跨通道验证按路由文档 §6 第 5 条——路径不确定先询问用户，耗时长提醒用户是否自助验证。

其它红线：所有查数走线上数据集，禁止本地静态文件；临时 ETL 产物用完即清；
亿级表（如 `ebc37edf…` 出库宽表）禁止全表扫描，必须带聚合与过滤。

## 边界

- 本包只消费 PAT，不处理 Cookie/企微登录；Cookie 事务回退 Cookie 卡片通道。
- PAT 权限与用户在 BI 页面的数据权限一致，拿不到的数据说明权限边界，不绕过。
