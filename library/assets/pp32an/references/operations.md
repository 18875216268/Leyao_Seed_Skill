# 运维与排障

## 退出码

| 码 | 含义 | 处理 |
| --- | --- | --- |
| 0 | 成功（或口径校验找到 authority） | 正常 |
| 2 | 参数/用法错（含 `feedback` 未知 query_id） | 按提示修正 |
| 3 | 未命中（含"未找到权威口径"） | 看 `suggestions`：换说法 / `--expand` / 换 `--need-type` / 请维护者补池 |
| 4 | 依赖/凭证缺失（云智库未登录、客户端缺失等） | 未登录 → 按提示人工扫码一次；客户端缺失 → 检查 `scripts/sources/leyou/` |
| 5 | 网络失败（全部源不可达） | 稍后重试；`doctor` 看连通性 |

## 常见情形

| 现象 | 处理 |
| --- | --- |
| `path` 里 `pool` ok=false | `doctor` 验证连通；确认 `registry.endpoint` 可访问 |
| `path` 里 `leyou` reason=LOGIN_REQUIRED | 人工在云智库目录扫码登录一次（本 skill 绝不代扫） |
| 结果像是"旧口径" | `ask --no-cache`（跳过缓存）并核对 `version/freshness`；如确已过期 → `reflect` 会提示复核 |
| 语义缓存误命中 | 调低 `registry.semantic_threshold`（更严）或 `--no-cache` |
| `contribute` 被拒 `GATE_REJECTED` | 看 `detail`：未达 semantic（adopt≥3）/ 有否决（fail>0）/ 内容过短；`--dry-run` 先看 payload |
| 采纳上报失败（`adopt_reported.ok=false`） | 价值信号失败**不阻塞**反馈；检查 `registry.write_token` 与网络，或置 `report_adopt=false` 关闭 |
| 缓存想清空 | 删 `<数据区>/cache.jsonl`（派生层，可重建） |
| 数据区在哪 | `status` 输出 `file` 字段；**一律在用户数据区**：`LEYAO_KB_HOME` 优先；被框架挂载时 `<包父级>/.leyao-data/data/assets/<卡片id>/`；独立部署 `~/.leyao-kb/` |

## 数据区文件

| 文件 | 内容 | 可删？ |
| --- | --- | --- |
| `cache.jsonl` | 结果缓存（精确+语义） | ✅ 可重建 |
| `memory.jsonl` | 本地记忆（含 feedback 计数） | ⚠️ 删除=丢掉"越用越聪明"的积累 |
| `feedback.jsonl` | ask-log 与采纳/否决审计 | ⚠️ 建议保留（可审计） |
| `reflect.jsonl` | 反思历史 | ✅ 可重建 |

## 维护动作

```text
python scripts/hub.py doctor --warm                      # 连通 + 一次真实查询（端到端自检）
python tests/run_tests.py                                # 离线单测（不触网）
python tests/run_eval.py --online                        # 金标评测（Recall@k + 延迟；需网络）
python scripts/hub.py contribute --all-candidates --dry-run   # 沉淀上传预检（不写线上）
```
