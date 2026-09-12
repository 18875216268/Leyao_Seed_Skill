# 设计说明（自包含 · 供独立分发）

> 本文件是 `leyao-knowledge` 的自包含设计摘要；调研出处与取舍依据见实现注释中的一句式引用（Anthropic Contextual Retrieval / Generative Agents / LongMemEval / MCP Design Patterns / GPT Semantic Cache）。

## 一、定位

只做"查知识"：**运营知识库（公共池）优先 → 乐药云智库兜底**；返回可解释答案 + 分层审计；无命中如实拒答。

## 二、优先级链（resolve.py）

```
精确缓存(≈0ms) → 语义缓存(相似度≥0.85) → 本地记忆(三因子) → 公共池(★) → 云智库 → 拒答
```
- **早停**：公共池命中即返回（默认不查云智库）；`--expand` 关闭早停（两库都取）；
- **口径例外**：`need_type=caliber` 只查注入库（`tier=inject`）——口径必须权威（对齐池侧语义）；
- **预算**：`registry.budget_seconds`（默认 20s）+ 各源 `timeout_s`（池 6s / 云智库 12s）；
- `path[]` 逐层审计（命中/跳过/耗时/原因），失败**不静默**。

## 三、三条目标对应的机制

| 目标 | 机制 | 依据（一句式） |
| --- | --- | --- |
| **准确** | 查询规范化+同义词（`ALIASES`）+时间感知；两路召回融合；**单一 authority**；冲突显式并列；拒答；两段分测（Recall@k 与 rubric） | LongMemEval：查询侧扩展 +11.3% recall；检索增益≠端到端增益；Anthropic：混合+重排失败率 −67% |
| **快速** | 精确缓存 + 语义缓存（无模型相似度，阈值可校准）+ 早停 + 单请求全取 + 预算 | GPT Semantic Cache：0.8 阈值命中 61.6–68.8%、阳性命中 92.5–97.3% |
| **越用越聪明** | 记忆对象（importance/evidence/adopt/fail）+ 三因子检索 + feedback 闭环（晋升/存疑 + 池侧采纳上报）+ reflect（带证据洞察）+ 冷存不删 + 沉淀上传（显式 `contribute`：本地质量闸 → 池侧三层闸+帕累托） | Generative Agents：recency(0.995^h)+importance+relevance 等权；反思=带证据的高层洞察 |

## 四、协议 1.0（返回字段）

`ok / plugin / protocol / problem / need_type / need_type_why / answer / best / possibilities[] / path[] / resolved / early_stop / elapsed_ms / query_id / has_more / next_offset / total_count / suggestions / time_hint`
未命中额外含 `reason`（`no_match` / `no_authority`）。

## 五、边界

- 不弹窗（云智库未登录→`LOGIN_REQUIRED`+指引）；不编造（拒答）；运行数据不写包内（`.leyao-kb` / `LEYAO_KB_HOME`）；
- 语义缓存为**无模型降级版**（阈值默认 0.85，需按真实语料校准；embedding 版为可选增强）；
- **缓存有界陈旧**：桶级 TTL（口径/制度/术语/课程 24h、搜索 1h）+ **拒答即失效**（防"错答被语义缓存复利"）+ 命中透出 `cached_at`/`version`；漂移检测 / embedding 版本键控为可选增强——依据：Tian Pan《Cache Invalidation for AI》(2026) · GPT Semantic Cache；
- 不做向量库、多智能体；知识写入（submit/inject）必须显式（`contribute`），仅采纳价值信号随 feedback 自动上报（失败静默）。
