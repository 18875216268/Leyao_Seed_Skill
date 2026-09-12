# 用户数据区（本文件是索引 · 首次初始化时播种 · 由框架维护）

> **定位**：这里**只放运行态**；框架包内只放只读交付物（代码 / 文档 / 模板 / 资产与路由）。
> **谁读**：AI 执行时读 `data/memory.md`（L0 经验）；AI 排障 / 清理时读本文件；人随时可读。
> **唯一自动写区**：`data/memory.md`（自我进化层自动档）；其余变更走提案 + 批准。

## 目录树与用途

```text
.leyao-data/
├── config.json          角色标记（maintainer true/false）——决定能否构造内容类提案
└── data/
    ├── README.md        ← 本索引（用途 / 清理策略 / 落点规则）
    ├── memory.md        L0 记忆（四段：失效模式 / 有效做法 / 待验证 / 墓碑）★ AI 每任务读
    ├── meta.json        阈值"变更集"（只存与模板不同的键；读取 = 模板 ⊕ 变更集）
    ├── versions.json    版本记录（当前 / 历史 ≤10 / 基线哈希；落地器唯一维护）
    ├── assets/<资产id>/  ★ 各资产**私有数据区**（框架只登记与统计，不解析内容）
    │                    例：知识库 → cache.jsonl（派生）/ memory.jsonl（经验）/ feedback.jsonl（用户反馈）
    └── state/           机器态（AI 经 `grow.py status` 读，不直接读原始文件）
        ├── traces.json 轨迹（滚动 200 条）
        ├── audit.log   审计（**永不清理**）
        ├── proposals/  提案队列（pending → apply / reject）
        ├── ratchet.json 棘轮（版本评分基线）
        └── *_results.*  评测 / 回归台账
```

## 清理策略（按类别，不按心情）

| 类别 | 例子 | 能否清理 |
| --- | --- | --- |
| **派生缓存** | `assets/<id>/cache.jsonl`（查询缓存） | ✅ 可随时清（会自动重建） |
| **证据类** | `assets/<id>/feedback.jsonl`、`memory.jsonl` | ⚠️ **迁移/备份，不要直接删**（是学习与审计依据） |
| **框架记忆** | `data/memory.md` | ⚠️ 只由框架维护；要清须走提案/显式确认 |
| **审计与台账** | `state/audit.log`、`*_results.*` | ❌ **永不清理**（红线） |
| **整个用户区** | `.leyao-data/` | 删 = 失忆（回到首次使用状态）；请先备份 |

## 落点规则（用户态只剩两处合法落点）

1. **框架用户区**：`.leyao-data/`（与 skill 同级；`LEYAO_SEED_HOME` 可覆盖）——框架自身状态 + 各资产私有数据 `data/assets/<id>/`；
2. **独立部署的资产**（未被框架挂载，如单独安装的知识库）：统一落 `~/.leyao-kb/`（`LEYAO_KB_HOME` 可覆盖）。
> **框架包内永不出现运行态**：`run_checks` 会拦截（`library/` 下点目录），并提示"迁移到用户区"。
