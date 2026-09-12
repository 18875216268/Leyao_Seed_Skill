---
name: leyao-knowledge
description: "查询乐药业务知识：术语定义、指标口径、公司清单、制度规定、课程资料。当需要确认『某个词什么意思』『某指标怎么算』『制度怎么规定』『有没有相关培训/资料』，或任何业务判断前需要权威依据时使用。先查运营知识库（公共池，authority 优先），查不到再走乐药云智库兜底；无命中会如实说不知道，并给出下一步建议。触发词：术语、口径、定义、怎么算、制度、规定、流程、制度依据、课程、培训、知识库、查一下、有没有资料。"
compatibility: "Python 3.10+（标准库；云智库检索需 requests）；需访问 lyzsk.cfdaili.top（公共池）与乐药云智库（登录态）；运行数据写入 <skill 同级>/.leyao-kb 或 LEYAO_KB_HOME 指向的目录（不写包内）"
license: "MIT"
metadata:
  version: "1.0.0"
  architecture: "resolve(优先链: 精确缓存→语义缓存→本地记忆→公共池(口径只查注入库)→云智库→拒答) + memory(越用越聪明) + feedback/reflect/contribute(闭环+沉淀)"
  coverage: "term/caliber/policy/course/search（公共池优先；云智库兜底）"
  author: "Leyao"
  date: "2026-09-12"
---

# leyao-knowledge · 乐药业务知识查询

> 只做一件事：**把业务知识问清楚**——先查**运营知识库（公共池，权威优先）**，查不到再走**乐药云智库**；全程可解释、可反馈、越用越准。

## 1. 何时用（触发场景）

- 不确定**术语/名词**含义（"缺货率"是什么口径？）
- 需要**指标口径/算法**（成本优势率怎么算？P4 边际利润率？）
- 需要**制度/规定/流程依据**（审批流程怎么走？返利政策？）
- 需要**课程/培训资料**（有没有毛利分析的培训？）
- 任何**业务判断前的依据核对**（口径校验：`check`）

## 2. 优先级链（核心语义）

```
提问 → 精确缓存 → 语义缓存 → 本地记忆 → 运营知识库（公共池）★优先
                                        → 乐药云智库（仅前面全空）
                                        → 未命中：如实拒答 + 建议
```

- **早停**：公共池命中即返回（默认**不查**云智库）——保证"快"；
- `--expand`：不早停，两库都取（要交叉验证/多可能性时用）；
- 返回 `path[]` 能逐层看到：哪层命中、哪层被跳过、为什么。

## 3. 命令（唯一入口；8 个）

```bash
python scripts/hub.py status                 # 概览（注册表/缓存/记忆/反馈）
python scripts/hub.py doctor [--warm]        # 自检（公共池连通 + 云智库登录态 + 预算）
python scripts/hub.py ask --problem "成本优势率怎么算" [--need-type caliber] [--expand] [--no-cache] [--limit 20] [--full]
python scripts/hub.py check --problem "P4边际利润率"   # 口径校验：只认 authority（池侧只查注入库）
python scripts/hub.py search --q "毛利" [--kind fact|procedure]   # 关键词搜索（procedure=程序环）
python scripts/hub.py feedback --query-id <id> --verdict adopt|reject [--note "…"]   # 反馈闭环（采纳且 best 来自池 → 自动上报价值信号）
python scripts/hub.py reflect                 # 反思（带证据的改进建议）
python scripts/hub.py contribute --all-candidates [--dry-run]   # 沉淀上传（显式；dry-run 只预检）
```

**退出码**：`0` 成功 ｜ `2` 参数/用法 ｜ `3` 未命中（含无权威口径）｜ `4` 依赖/凭证缺失（含云智库需人工登录）｜ `5` 网络失败（全部源不可达）。
**返回协议 1.0**：`ok / need_type / answer / best / possibilities[] / path[] / resolved / early_stop / elapsed_ms / query_id / has_more / next_offset / total_count / suggestions`。

## 4. 越用越聪明（闭环怎么转）

1. 每次 `ask` 返回 `query_id`；**采纳/否决由调用方一行回写**：`feedback --query-id … --verdict adopt|reject`；
2. 有效答案自动进**本地记忆**（含 importance 1–10、evidence 指针）；`adopt ≥3` 且无否决 → **晋升**（candidate→semantic→pool-candidate）；**采纳且 `best` 来自公共池时自动上报价值信号**（失败静默，不阻塞反馈）；
3. `reflect` 把轨迹变成**带证据**的建议：反复未命中的主题（→补池/补别名）、被否决主题（→查口径）、待晋升项（**达标即标 pool-candidate**）、陈旧项；提交用 `contribute`（显式，含本地质量闸）；
4. 记忆检索为**三因子**（近因 0.995^小时 + 重要性 + 相关性，归一化等权）；低价值记忆**冷存不删**。

## 5. 边界与红线（如实）

- **不弹窗、不扫码**：云智库未登录 → 明确返回 `LOGIN_REQUIRED` + 手动登录指引（绝不代扫）；
- **无命中不编造**：`ok:false` + 建议（换说法 / `--expand` / 换 need-type / 请维护者补池）；
- **单一权威源**：口径只认 `authority`；多源冲突**显式并列**（`conflict` 字段），不静默择一；
- **运行数据一律落用户数据区（不写包内、不写 skill 同级）**：优先 `LEYAO_KB_HOME`；被框架挂载时自动归口 `<包父级>/.leyao-data/data/assets/<卡片id>/`（`LEYAO_SEED_HOME` 可覆盖）；独立部署/任意机器统一落 `~/.leyao-kb/`；缓存可随时清理（派生层）；
- **缓存可失效**：桶级 TTL + **拒答即失效**（防"错答被缓存复利"）+ 命中透出 `cached_at`/`version`（陈旧度可审计）；
- **不做**：向量库/embedding 依赖（语义缓存用无模型相似度，阈值可按语料校准）、多智能体；**知识写入（submit/inject）必须显式**（`contribute`）——仅"采纳价值信号"随 `feedback` 自动上报（失败静默）。

## 6. 维护（维护者）

- **换源/调超时/调 TTL**：只改 `registry.json`（地址、优先级、timeout_s、ttl_seconds、semantic_threshold、budget_seconds）；
- **补别名/同义词**：`scripts/query_norm.py` 的 `ALIASES`（按真实未命中案例扩充，`reflect` 会给建议）；
- **云智库客户端**：`scripts/sources/leyou/`（原生集成，原样复用；登录态落在其目录下）；
- **沉淀上传**：`contribute --memory-id <id>`（单条）/ `--all-candidates`（批量）/ `--inject --title … --content …`（权威注入，仅用户显式要求）；写 token 与开关在 `registry.json`（`write_token` / `report_adopt`）；
- **自检**：`python tests/run_tests.py`（离线）→ `hub.py doctor`（连通）→ `hub.py ask --problem 缺货率`（真实样例）→ `contribute --all-candidates --dry-run`（沉淀预检，不写线上）。
