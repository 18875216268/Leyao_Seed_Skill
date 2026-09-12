# 自我进化层 · 五环自举法

**变 → 择 → 行 → 证 → 藏**，循环往复。阈值本身同受此环约束（自举/元层），目标与红线永不变异（恒常层）。

## 五环（每环一句话 + 命令）

| 环 | 命令 | 做什么 | 护栏 |
| --- | --- | --- | --- |
| 变 | `grow.py reflect` | 轨迹/用户纠正 → 经验候选（每次重建，防 stale 堆积）；库体检与探索信号 → `status` 实时诊断（不入库，修复即自动消解） | 统计类候选 support≥min_support；诊断类为实时条件；候选不影响任何行为 |
| 择 | `grow.py evolve` | 经验候选（route/avoid）→ 自动档 memory 直写激活；动作类变异（路由 / 资产内容 / 阈值 / 降级）由 AI 用 `grow.py propose` 构造提案，经你 `apply` 批准或 `reject` 否决（**两个终态**） | 每个变异带 evidence（support/success_rate）；提案 payload 走契约校验 |
| 行 | `grow.py apply --id <p_x>` | 执行**你已批准**的提案 | 先快照、后写入、跑评分、失败即回滚 |
| 证 | `grow.py review` | 观察期结算：促进/降级/淘汰/回滚 | `evolution/tests` 一票否决（结构自检 `run_checks.py` + 触发评测 `run_trigger_eval.py` + 更新链路回归 `run_update_sandbox.py`）；分数降自动回滚 |
| 藏 | 自动 | 精华升 memory「有效做法」/ 提案提炼进正文；糟粕入墓碑 | 用户区 `data/state` 不膨胀：提炼后移除、轨迹滚动 200 条 |

## 变异分级（易之三义）

| 变异层级 | 内容 | 是否变异 |
| --- | --- | --- |
| 内容层（易） | 资产内容（`library/assets/` 内）· 路由（`routes.json`/`ROUTES.md`）· 规则状态 | 自由，走五环 |
| 阈值层（简易） | 阈值：用户区 `data/meta.json`（默认值 `evolution/templates/meta.json`），含 **`max_active_rules` = 库宽上限 C**（默认 200；超限即按"贡献最低"退役——依据 Ratchet：上限是非发散的必要条件） | 元变异：数据充足 + 你批准 |
| 恒常层（不易） | 目标 g + 本红线节 | **永不** |

> **防分叉**：内容层里的**资产与路由**（`asset_write` / `route_update`）只在**维护者实例**可用（用户区 `config.json` 的 `maintainer: true`；非维护者实例 propose 即拒收）——它们的产物要能被维护者吸收进正式版。使用者需要改资产/路由时，让 AI 起管理台（`python library/admin/console.py`）显式维护：产出为标准格式，维护者可直接收编；规则状态属运行态，任何实例照常。
>
> **版本与更新**：包的版本检测与更新属**版本维护层**（`version/VERSION.md`，准则与流程）；其落地经本层唯一落地器（提案 + apply），不受维护者限制——经你批准即可。

## 数据落点（用户区）

运行态只写**用户区**：与 skill 同级 `.leyao-data/`（`LEYAO_SEED_HOME` 可覆盖；同级不可写回落 `~/.leyao-data/`；首次使用自动初始化）：

- ⚠️ **路径基准**：以下 `data/…` 一律相对用户区根 `.leyao-data/`（完整写法如 `.leyao-data/data/memory.md`）
- `data/memory.md`（L0 记忆；首用从 `evolution/templates/memory.md` 播种）
- `data/meta.json`（阈值**变更集**：只存与模板不同的键；读取 = 模板 ⊕ 变更集，用户优先）
- `data/versions.json`（版本记录：当前版本 / 历史（≤10 条）/ 基线哈希；落地器唯一维护，见 `version/VERSION.md`）
- `data/state/`（轨迹 / 规则 / 棘轮 / 审计 / 提案 / 评测记录）
- `data/assets/<资产id>/`（**各资产私有数据区**：如知识库的 cache / memory / feedback。**归该资产自己读写**；框架只登记与统计（`grow.py status` 的 `assets_data` 显示足迹），**不解析内容**——避免跨层格式耦合）
- `data/README.md`（**用户区索引**：各区用途 / 清理策略（缓存可删·证据类迁移·审计永不清理）/ 落点规则；首次初始化时从 `evolution/templates/user-area.md` 播种）

包内只有只读交付物与模板（`evolution/templates/`）；`run_checks.py` 的 `paths_external` 守住"包内零运行态"。

## 规则状态机（去糟粕取精华的载体）

```text
candidate ──批准──► active ──命中5次无反例──► core（升 memory「有效做法」）
    │                  │反例≥2                    │反例≥3+批准
    │反例≥2            ▼                          ▼
    └──────────► demoted ──观察10次无恢复──► retired（墓碑，指纹拦截再蒸馏）
```

## CLI

```text
python evolution/grow.py trace --task "<任务>" --routed "<走了哪条路>" --outcome success|partial|fail [--reason R] [--override O] [--no-auto]
                                        # ★ trace 默认**自动**跑 reflect + evolve（变→择 自动闭环，经验立即生效）；
                                        #   --no-auto 关闭；输出含 auto_evolve 字段（不静默）
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
| `meta_update` | 阈值层（用户区 `data/meta.json`，需元变异已开启） | `{"key": "thresholds.min_support", "value": 3}` |
| `core_demote` | 规则 core → demoted | `{"rule": "<规则 id>"}` |
| `framework_update` | 整包更新（版本维护层：staging 对齐全包；快照 / 证环 / 整体回滚） | `{"staging": "<新版包目录>", "version": "x.y.z", "summary": "…"}` |

契约由 `contract_error` 唯一校验：`propose` 时不合规**直接拒收**（不造无法执行的提案）；`apply` 时再守一道（旧 / 手写残次提案也能安全收口）。
失败路径一律留痕：永久无效（越权 / 状态不符 / 契约不符）→ `apply.rejected` + 提案作废；条件暂不满足（元变异未开启）→ `apply.blocked` + 提案保留；落地中断或证环不过 → `apply.rollback` + 整体回滚（`framework_update` 同样守此例）。

## 记忆文件（用户区 `data/memory.md` 四段 ↔ 状态机）

`待验证`=active(route) ｜ `失效模式`=active(avoid) ｜ `有效做法`=core ｜ `墓碑`=retired（指纹+否定理由，防糟粕复活）。

## 自动开启（无需人工干预）

`traces ≥ 40` 且 `active+core ≥ 2` 时，引擎自动开启**主动探索**（`status` 持续给出命中率最低资产的信号，AI 审查后构造变异提案）与**元变异**（阈值可被挑战）——翻转写审计（用户区 `data/state/` 下）。

## 红线（道，永不参与变异）

1. **不静默改**：用户区 `data/memory.md` 是唯一自动写区；其余变更必须提案 + 用户批准（内容类仅维护者实例）——静默改会绕过审计留痕与回滚窗口。
2. **不删审计**：用户区 `data/state/` 下的审计日志与墓碑记录永不清理。
3. **不改道**：目标 g 与本红线节不进变异池。
4. **记忆边界**：用户偏好/环境事实 → 宿主记忆；**影响框架行为的经验 → 本层**。
