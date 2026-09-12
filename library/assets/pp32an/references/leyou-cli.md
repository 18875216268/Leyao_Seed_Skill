# 乐药云智库 CLI 速查（兜底源）

> 客户端：`scripts/sources/leyou/leyou_cloud.py`（原生集成，原样复用）；登录态落同目录 `leyou_token.json`。
> **本 skill 只查不弹窗**：`status` 预检（scan=False 语义）→ 失效即返回 `LOGIN_REQUIRED` + 手动指引。

## 常用子命令

```text
python scripts/sources/leyou/leyou_cloud.py status           # 登录态预检（只复用本地/库凭证）
python scripts/sources/leyou/leyou_cloud.py search <关键词>   # 搜索（本 skill 兜底调用）
python scripts/sources/leyou/leyou_cloud.py summary <slug>    # 摘要
python scripts/sources/leyou/leyou_cloud.py detail <slug>     # 详情
python scripts/sources/leyou/leyou_cloud.py collect <关键词>   # 全库采集（批量）
```

## 登录（人工，一次性）

凭证失效时**由人**在云智库目录执行其登录流程（扫码）；成功后凭证写入 `leyou_token.json`，之后本 skill 常态复用、无感。
> 本 skill 不实现登录、不代扫、不保存额外凭证。

## 桥接行为（`scripts/sources/leyou_bridge.py`）

- `status()`：子进程调用 `status`；输出含登录失效关键词 → `{ok:false, reason:"LOGIN_REQUIRED", next:<指引>}`；
- `search(problem)`：先 `status()` 预检，未登录**直接返回**（不浪费 12s 超时）；
- 输出容错：JSON → 结构化 items；纯文本 → 折成 1 条（`evidence: leyou#cli`），**不臆造字段**。
