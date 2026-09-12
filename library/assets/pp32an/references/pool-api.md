# 运营知识库（公共池）API 契约

> 实测基线：2026-09-12（HTTP 200 可读；免登录）。

## 端点

```text
GET https://lyzsk.cfdaili.top/api/pool
  ?q=<关键词>            # 关键词检索（留空 = 默认热度列表）
  &limit=<N>             # 返回条数（**服务端钳制 1–200**）
  &offset=<N>            # 分页偏移；响应顶层带 total → 可精确判定是否截断
  &fields=index          # **轻量只读索引**：不含 content/history、**零 UPDATE 不记 hit**，
                         #   顶层带 total + `pool_updated_at`（同步 / 版本探测 / 全池 diff 专用）
  &ids=<id,id,…>         # 按 id 批量取（≤50）；`fields=index&ids=…` = 零写存在性回验（指针抽检）
  [&tier=inject|session] # 可选：注入库（authority）/ 会话库（reference）；口径查询固定 inject
  [&category=term|caliber|method|experience]
  [&kind=fact|procedure] # 可选：程序环独立检索（工作流/工具模式）
```

> 响应顶层：`count`（本页条数）· **`total`**（同条件总数）· `pool_updated_at`（仅 `fields=index`）· `items[]`。
> 排序含稳定决胜键（`created_at DESC, id DESC`）→ 分页不漂移；全文模式保持"读取即命中"（1 次批量 `UPDATE`），
> **索引模式零写**（同步读不再自增热度——顺带解掉"零热度新条目永沉底"）。

## 响应

```json
{"ok": true, "count": 3, "items": [
  {"id":"5e5edca2-e48","category":"term","title":"术语：缺货率",
   "content":"缺货率：缺货品种数/考核品种数……","trust":"authority",
   "hit_count":74,"adopt_count":0,"quality_score":0.9,"freshness":1,
   "version":1,"similarity_hash":"8000b88e","contributor":"seed-import","status":"active"}
]}
```

| 字段 | 用途（本 skill） |
| --- | --- |
| `content` | 答案正文（→ `possibilities[].answer`） |
| `title` | 展示标题（用于冲突归并） |
| `trust` | 信任级：`authority` 优先（**口径校验只认它**） |
| `quality_score` | 置信度（排序参考） |
| `hit_count` / `adopt_count` | 热度/采纳（与本地反馈口径一致，可对齐） |
| `version` / `freshness` | 时效与版本（缓存失效与"陈旧"提示用） |
| `status` | 只取 `active`（非 active 过滤掉） |

## 写入（沉淀路径；需共享 token，写接口校验）

> 与读路径同源（同端点，`X-Contributor-Token` 头）；**令牌只在本地配置**：数据区 `config.local.json`
> → `pool.write_token`（不入包/不入仓）。未配置时：显式写动作返回 `NO_WRITE_TOKEN`（不发请求，含配置指引），
> 采纳上报静默跳过。
> **默认不写**：提交/注入仅 `contribute` 显式调用；**采纳价值信号**在 `feedback --verdict adopt` 时自动上报（失败静默；`registry.report_adopt=false` 可关）。

| 动作 | 端点 | 字段 | 说明 |
| --- | --- | --- | --- |
| 沉淀提交 | `POST /` | title · content · category · distill_type · trust · quality_score · contributor · kind | 服务端执行**三层闸 + 帕累托**；`kind=fact|procedure`（程序环与事实检索区分） |
| 权威注入 | `POST /inject` | title · content · category · kind · quality_score · contributor · distill_type | **仅用户显式要求注入**（authority；不经蒸馏门槛） |
| 采纳上报 | `POST /adopt` | id | 价值信号（需 token 防伪造；响应含最新 hit/adopt 计数） |

写前**本地质量闸**（`scripts/contribute.py`，对应原机制「验证门禁」的最小可用版）：
① 记忆已确认（semantic / pool-candidate）且 `fail=0`；② 内容非空且 ≥20 字；
③ `quality_score` 透明启发式（0.5 起，采纳 +0.1 / 否决 −0.15，钳制 0.3–0.95）。
`contribute --dry-run` 只打印将发送的 payload、不发起网络写请求。
**知识永不真删**：池侧废弃走状态标记（维护者操作）。

## 自建服务说明

公共池由用户方运营（Cloudflare Pages Functions 实现，源码曾随另一包分发：`lyzsk-pages/functions/api/pool/[[path]].js`）。换地址只改 `registry.json` 的 `endpoint`。
