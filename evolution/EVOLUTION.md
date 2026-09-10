# 自我进化层 · 五环自举法

**变 → 择 → 行 → 证 → 藏**，循环往复。阈值本身同受此环约束（自举/元层），目标与红线永不变异（恒常层）。

## 五环（每环一句话 + 命令）

| 环 | 命令 | 做什么 | 护栏 |
| --- | --- | --- | --- |
| 变 | `grow.py reflect` | 轨迹/用户纠正 → 经验候选（每次重建，防 stale 堆积）；库体检与探索信号 → `status` 实时诊断（不入库，修复即自动消解） | 统计类候选 support≥min_support；诊断类为实时条件；候选不影响任何行为 |
| 择 | `grow.py evolve` | 经验候选（route/avoid）→ 自动档 memory 直写激活；动作类变异（路由 / 资产内容 / 阈值 / 降级）由 AI 用 `grow.py propose` 构造提案，经你 `apply` 批准或 `reject` 否决（**两个终态**） | 每个变异带 evidence（support/success_rate）；提案 payload 走契约校验 |
| 行 | `grow.py apply --id <p_x>` | 执行**你已批准**的提案 | 先快照、后写入、跑评分、失败即回滚 |
| 证 | `grow.py review` | 观察期结算：促进/降级/淘汰/回滚 | `evolution/tests` 一票否决（结构自检 `run_checks.py` + 触发评测 `run_trigger_eval.py`）；分数降自动回滚 |
| 藏 | 自动 | 精华升 memory「有效做法」/ 提案提炼进正文；糟粕入墓碑 | `evolution/state` 不膨胀：提炼后移除、轨迹滚动 200 条 |

## 变异分级（易之三义）

| 变异层级 | 内容 | 是否变异 |
| --- | --- | --- |
| 内容层（易） | 资产内容（`library/assets/` 内）· 路由（`routes.json`/`ROUTES.md`）· 规则状态 | 自由，走五环 |
| 阈值层（简易） | 阈值：`evolution/meta.json` | 元变异：数据充足 + 你批准 |
| 恒常层（不易） | 目标 g + 本红线节 | **永不** |

## 规则状态机（去糟粕取精华的载体）

```text
candidate ──批准──► active ──命中5次无反例──► core（升 memory「有效做法」）
    │                  │反例≥2                    │反例≥3+批准
    │反例≥2            ▼                          ▼
    └──────────► demoted ──观察10次无恢复──► retired（墓碑，指纹拦截再蒸馏）
```

## CLI

```text
python evolution/grow.py trace --task "<任务>" --routed "<走了哪条路>" --outcome success|partial|fail [--reason R] [--override O]
python evolution/grow.py reflect        # 轨迹 → 候选规则
python evolution/grow.py evolve         # 经验候选 → 自动档落地（memory 直写激活）
python evolution/grow.py propose --kind K --payload '<JSON>'   # 构造动作类变异提案（待批准）
python evolution/grow.py apply --id p_x # 执行已批准提案
python evolution/grow.py reject --id p_x [--reason R]   # 否决提案（关闭 pending，留审计）
python evolution/grow.py review         # 观察期结算（促进/降级/淘汰/回滚）
python evolution/grow.py status         # 全景：轨迹/规则/提案/棘轮/自动开启进度
```

## 提案：唯一高风险通道（payload 契约）

提案是状态机：`pending` 只有两个终态——`apply`（批准并落地）、`reject`（否决并关闭）。
**只批准不否决，没人认领的提案会一直悬着，队列终成噪音**；理由经 `--reason` 写入审计。

| kind | 改什么 | payload |
| --- | --- | --- |
| `route_update` | 路由表（`routes.json` → `ROUTES.md`） | `{"cmd": "add\|update\|remove", "args": [引擎同名参数...]}` |
| `asset_write` | 资产内容（仅 `library/assets/` 内，追加写入） | `{"file": "library/assets/<id>/SKILL.md", "text": "…"}` |
| `meta_update` | 阈值层（`evolution/meta.json`，需元变异已开启） | `{"key": "thresholds.min_support", "value": 3}` |
| `core_demote` | 规则 core → demoted | `{"rule": "<规则 id>"}` |

契约由 `contract_error` 唯一校验：`propose` 时不合规**直接拒收**（不造无法执行的提案）；`apply` 时再守一道（旧 / 手写残次提案也能安全收口）。
失败路径一律留痕：永久无效（越权 / 状态不符 / 契约不符）→ `apply.rejected` + 提案作废；条件暂不满足（元变异未开启）→ `apply.blocked` + 提案保留。

## 记忆文件（library/.memory.md 四段 ↔ 状态机）

`待验证`=active(route) ｜ `失效模式`=active(avoid) ｜ `有效做法`=core ｜ `墓碑`=retired（指纹+否定理由，防糟粕复活）。

## 自动开启（无需人工干预）

`traces ≥ 40` 且 `active+core ≥ 2` 时，引擎自动开启**主动探索**（`status` 持续给出命中率最低资产的信号，AI 审查后构造变异提案）与**元变异**（阈值可被挑战）——翻转写审计（`evolution/state/` 下）。

## 红线（道，永不参与变异）

1. **不静默改**：`library/.memory.md` 是唯一自动写区；其余文档/路由变更必须提案 + 用户批准——静默改会绕过审计留痕与回滚窗口。
2. **不删审计**：`evolution/state/` 下的审计日志与墓碑记录永不清理。
3. **不改道**：目标 g 与本红线节不进变异池。
4. **记忆边界**：用户偏好/环境事实 → 宿主记忆；**影响框架行为的经验 → 本层**。
