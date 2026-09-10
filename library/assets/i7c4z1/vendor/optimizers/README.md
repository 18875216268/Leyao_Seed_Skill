# 如何接入一个「板块优化 skill」（vendor/optimizers/）

`vendor/optimizers/<包名>/` 是 Bi_智能取数_login 的**集团包优化 skill 槽位**：
针对某个 BI 业务板块提供更精简、更贴合实际取数的优化指引（常用卡片、典型 payload
模板、易错点、强制规则），是基础取数文档包（`vendor/bi-cookie/`）特定板块的优化方案。

## 放置规则（重要）

- **原样落盘、不改名**：优化包以子目录形式整体放入 `vendor/optimizers/`，目录名即包名，
  内部文档、脚本、payload 模板一律原样保留，框架零解析、零转述。
- **不实现登录**：优化 skill 不内置登录/鉴权，凭证统一由父 skill 自有登录组件
  （`scripts/bi_login.py` CLI → 同目录 `login_bi.py` 本体）提供或用户给定（见
  `vendor/SUBSKILL_ROUTING.md` §6 第 5 条）。
- **结构自定**：包内可含 `SKILL.md` / `references/` / `scripts/` / `payloads/` 等，
  具体形态由该板块的优化文档决定（参考 Pms 同款 `optimizers/Pms_促销毛利v1.08` 的
  `agents/ references/ scripts/ SKILL.md` 布局）。

## 接入三步

1. 把优化包目录放入 `vendor/optimizers/<包名>/`（如 `vendor/optimizers/Bi_销售复盘v1.0/`）。
2. 编辑 `vendor/SUBSKILL_ROUTING.md` §5 板块优化路由表，追加一行：
   板块 / 优化 skill 目录 / 适用场景 / 不适用·回退条件 / 优先级。
3. 无需同步配置：优化包随 skill 内置交付，AI 直接按路由表读取（框架无同步器）。

## 路由优先级

同板块命中多个优化 skill 时，按路由表「优先级」数字从小到大选用；优化 skill 未覆盖的
板块或无法解决的任务，回退 `vendor/bi-cookie/` 基础取数文档包。
