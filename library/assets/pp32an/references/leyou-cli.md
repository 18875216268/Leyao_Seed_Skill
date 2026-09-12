# 乐药云智库 CLI 速查（兜底源）

> 客户端：`scripts/sources/leyou/leyou_cloud.py`（原生集成，原样复用）；**登录态只落用户数据区** `leyou_token.json`
> （客户端经全局参数 `--token-file` 指定；包内零写入）。
> **本 skill 只查不弹窗**：`status` 预检（scan=False 语义）→ 失效即返回 `LOGIN_REQUIRED` + 手动指引。

## 常用子命令

> 直接手调客户端时必须带 `--token-file`（否则默认读写**包内**同目录 ✗）：桥接（`leyou_bridge.py`）与
> 自动登录桥（`leyou_firebase_login.py`）已自动带上。

```text
python scripts/sources/leyou/leyou_cloud.py status           # 登录态预检（只复用本地/库凭证）
python scripts/sources/leyou/leyou_cloud.py search <关键词>   # 搜索（本 skill 兜底调用）
python scripts/sources/leyou/leyou_cloud.py summary <slug>    # 摘要
python scripts/sources/leyou/leyou_cloud.py detail <slug>     # 详情
python scripts/sources/leyou/leyou_cloud.py collect <关键词>   # 全库采集（批量）
```

## 登录（人工，一次性）

凭证失效时**由人**在云智库目录执行其登录流程（扫码）；成功后凭证写入 `leyou_token.json`，之后本 skill 常态复用、无感。
> 本 skill 不实现登录、不代扫、不保存额外凭证；如需多人共享登录态，见文末「可选：多凭证自动登录桥」。
> ⚠️ `leyou_token.json` 与数据区 `config.local.json` 都是**真实运行凭证**：切勿入包/入仓；发布或提交前确认包内不存在。

## 桥接行为（`scripts/sources/leyou_bridge.py`）

- `status()`：子进程调用 `status`；输出含登录失效关键词 → `{ok:false, reason:"LOGIN_REQUIRED", next:<指引>}`；
- `search(problem)`：先 `status()` 预检，未登录**直接返回**（不浪费 12s 超时）；
- 输出容错：JSON → 结构化 items；纯文本 → 折成 1 条（`evidence: leyou#cli`），**不臆造字段**。

## 可选：多凭证自动登录桥（`scripts/sources/leyou/leyou_firebase_login.py`）

多人共享登录态（opt-in）。**参数只存本地**（包内零秘钥）：

```text
python scripts/sources/leyou/leyou_firebase_login.py auto            # 本地→库凭证→扫码
python scripts/sources/leyou/leyou_firebase_login.py auto --no-scan  # 只试现有凭证（全失效退出码3）
python scripts/sources/leyou/leyou_firebase_login.py check-db        # 只读查看库内凭证（token 打码）
python scripts/sources/leyou/leyou_firebase_login.py clear-db --key <key>   # 删单条（--all 清空）
python scripts/sources/leyou/leyou_firebase_login.py push-local      # 本地凭证写入库
```

- 配置：数据区 `config.local.json` 的 `leyou_firebase` 段——`database_url` 与 `fangwen_miyue` 必需，
  其余项目参数（`api_key` / `auth_domain` / `project_id` / `storage_bucket` / `messaging_sender_id` /
  `app_id` / `measurement_id`）备查；
- 未配置：`auto` 自动降级为「本地凭证 + 扫码」并标注 `db=unconfigured`；库命令返回 `CONFIG_MISSING`（码3）。
