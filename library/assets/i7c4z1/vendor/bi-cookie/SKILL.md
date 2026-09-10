---
name: bi-cookie
description: "观远 BI Cookie 卡片通道（集团级通道1）：企微扫码会话鉴权，任何登录用户可用。覆盖页面/卡片元数据、卡片取数（全参数）、候选值、导出 xlsx、卡片索引。执行入口：scripts/ 三工具；API 契约：references/ 全参数文档。"
---

# bi-cookie —— 观远 BI Cookie 卡片通道

**通道定位**：集团级通道1（主通道），与 PAT SQL 通道（`vendor/bi-pat/`）平行。
通道选择与权限分流见 `vendor/SUBSKILL_ROUTING.md` §1/§3。
本通道自包含：契约文档（references/）+ 执行工具（scripts/）+ 索引数据（data/）。

## 凭证（通道不实现登录，消费框架凭证）

登录统一由父 skill 登录器提供：`python scripts/bi_login.py`（企微扫码，落库 `accounts/<loginId>.json`）。
下方三工具自动从凭证仓库注入 Cookie（本地检查，绝不弹窗；`--relogin` 强制重扫）。
换新凭证后重试一次原请求即可，不要因参数、权限、限流、网络错误触发登录。

## API 契约（AI 直读，接口事实源）

- [references/总文档.md](references/总文档.md)：流程、术语、错误恢复、已知限制。
- [references/api查询文档.md](references/api查询文档.md)：全部端点全参数 + 可抄样本（取数 / 元数据 / 候选值 / 导出链 / 任务中心）。

## 工具入口

### 发送器 `scripts/bi_call.py` —— 文档驱动通用请求壳

AI 读契约文档后给出 URL/路径/请求体，本脚本只负责发送并原样返回响应：

- 按路径：`python vendor/bi-cookie/scripts/bi_call.py --path /api/card/<cardId>/data --payload-file payload.json`
- 按完整 URL：`... --url https://bi.leyopharm.com/api/validate-token`
- `--host-key biHost`（默认，取自根 `sync_config.json` 的 `host_endpoints`）；`--method GET|POST`（默认有请求体用 POST）
- `--output resp.json` 落盘原始响应；`--relogin` / `--no-ui` / `--no-remote` / `--insecure` / `--no-proxy`
- 请求体文件支持带 BOM 与不带 BOM 的 UTF-8 JSON；响应信封见契约文档 §1

### 卡片索引 `scripts/bi_index.py` —— 每卡可传参数的离线缓存

回答「某张卡能传哪些筛选参数」「哪些卡含某字段」，无需联网逐卡查。事实缓存非事实源，
权威来源仍是 `GET /api/card/{cardId}`；读时自动补新，网络失败降级旧值并标注 `_stale`。

- `--build` 全量构建（目录 → 页面 → 卡片，实测 39 页 / 558 卡）；`--search <关键词>` 离线检索
- `--card <cardId>` 查单卡可传参数（缺失或超 TTL 自动实时刷新）；`--refresh` / `--stats`
- `--ttl-days N`（默认 7）/ `--force`；缓存落盘 `data/card_index.json`（运行时数据，勿手工编辑、勿整体读入上下文）

### 导出器 `scripts/bi_export.py` —— 卡片数据导出 Excel

服务端异步任务，导出全量数据，不受取数 limit 限制。三步链自动化：
`POST /api/write/file/{cardId}?typeOp=EXCEL` → `GET /api/task/{taskId}` 轮询至 FINISHED → `POST /api/export/file/common/{taskId}` 流式下载。

- `--card <cardId>` 导出（默认 `./<卡名>_<时间戳>.xlsx`）；`--payload-file body.json` 带筛选导出（请求体同取数体）
- `--out <路径.xlsx>`；`--timeout` / `--poll-interval` 调整等待；超时后 `--task <taskId>` 直接继续下载
- `--list <N>` 查看导出中心任务列表（找回 taskId）；大文件流式落盘；stdout 输出 JSON

## 边界

- 本通道只访问 `https://bi.leyopharm.com`（host 外置于根 `sync_config.json` 的 `host_endpoints.biHost`）。
- 取数类故障（超时/键名/限流/数据量）以 [references/api查询文档.md](references/api查询文档.md) 错误表为准；
  登录类故障（401/1017）回父 skill `scripts/bi_login.py`。
- 板块级优化走 `vendor/optimizers/`（所属通道为本通道的优化板，路由见 `vendor/SUBSKILL_ROUTING.md` §5）。
