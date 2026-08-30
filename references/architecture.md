# LeyaoSeedSkill · 架构

## 定位

通用套件，**独立可发布**。核心不绑定任何具体生态：子 skill 是原样目录，路由表是外置元数据，
生态差异只体现在子 skill 自己的 `SKILL.md` 里，不进内核。

## 分层

| 层 | 落点 | 职责 |
| --- | --- | --- |
| ① 总则 | `manifest.json` | 目录约定 / as-is 保证 / 统一契约 / 治理边界，可机校验 |
| ② skill 包 | `skills/` + `registry/skills.json` | 子 skill 原样目录 + 路由表（唯一事实源）+ 裁决 |
| ③ 进化层 | `evolution/` | 蒸馏（只读·生产者）→ 共享知识库 → 成长 / 建模 / 守门（读写·驱动者） |
| ④ 更新获取层 | `deploy/` | 只读消费：完整性校验 / 远端适配 / 取版本 / 启动或按需拉取；网络异常时经自建云函数直连兜底 |
| ⑤ 版本 | `manifest.json` | semver + 每 skill `content_hash` / `version_pin`（lockfile 防漂移） |

依赖方向单向：`evolution → core`、`deploy → core`、`core` 只依赖标准库。
`evolution` 与 `deploy` 之间无环——过去 `parse_frontmatter` 被放在 `evolution/distiller.py`，
导致 `deploy ↔ evolution` 双向依赖、且 `core` 反向依赖上层；把它提到 `core/frontmatter.py`
后环自然消解。这是归类问题，不是需要兼容层的问题。

## 子 skill 原样与关联

- **物理原样**：`skills/<id>/` 与其独立发布形态逐字节一致，套件零写入。
- **关联外置**：路由表以 `path` 指向目录、`mode` 声明调用方式。关联是元数据，不在 skill 内部。
- **双模式**：`llm`（把 SKILL.md 全文交 agent 执行，最原样）／ `native`（薄 `handler.py` 实现 `describe / can_handle / invoke / health`，更快更确定）。

## 路由：两段式 + 裁决 + 四策略

1. **廉价召回**：只读 `triggers` / `domain` / `negative_triggers` / `enabled` 等元数据。
2. **精确排序**：命中数 + `priority` + `scope` 具体度 + 经验加权（route 加分、avoid 减分；`candidate` 状态不参与）。
3. **裁决**：单命中直连；多命中取排序最优（排序已编码 priority↓ 与 scope 窄胜宽）；无命中兜底 llm。
4. **四策略**：直连 / 级联兜底 / 管道编排（按 `depends` 拓扑排序）/ 并行（请求 parallel 时按命中集并行执行，并列命中则全员并行，单命中则单点并行）。

全量 skill 描述**永不**每 query 入上下文。

## 进化层：蒸馏与成长分离

| | 蒸馏（只读·生产者） | 成长（读写·驱动者） |
| --- | --- | --- |
| 动作 | 读取并抽取，产出知识资产 | 消费知识资产与反馈，执行修改循环 |
| 不改 | 绝不修改受管资产 | 不做抽取 |
| 落点 | `evolution/distiller.py` | `evolution/growth.py` |

- **Lane A 注册派生**：frontmatter → 路由表字段（事件驱动，非持续）。
- **Lane B 轨迹蒸馏**：query→skill 映射、用户偏好、失败→修复，按 `candidate → validated → locked` 晋升。
- **Lane C 库级结构**：skill 间重叠 / 冲突 / 冗余检测。
- **共享知识库**（first-class）：`experience` / `user-model` / `library-map`，被路由、引导、部署、成长共同消费；除索引外均磁盘持久、按需加载。
- **守门**：棘轮（只升不降，劣化即回滚）＋ 评估门（test-prompts 量化）＋ 置信门控（candidate 永不进生产路径）。

> avoid 规则成功率恒为 0，晋升只看证据数（support），不看成功率——否则永不晋升。

## 权限矩阵（先于一切进化动作）

| 操作 | 权限 |
| --- | --- |
| 读子 skill | 自主 |
| 增 / 删子 skill | **需用户授权**，提案 → 批准 → 执行 |
| 改子 skill 内容 | **需用户授权**，提案 → 批准 → 执行 |
| 更新路由表 entry | 自主（变更后必须） |
| 触发部署 | 自主 |

事实源是 `evolution/permissions.py::MATRIX`（`AUTONOMOUS` / `REQUIRES_AUTH` 两类），
改权限只改那里，文档只是它的投影。

**为什么增删也要授权**：静默加一条，用户不知道套件里多了什么、会被哪些 query 命中；
静默删一条，等于让某些 query 的承接方凭空消失——两者与"改内容"同属对外部可见行为的变更。

**唯一豁免：`discover()`**。它扫的是用户**自己放进** `skills/` 的目录，放入即授权，
再要一次确认是重复且打断自动化。它走内部路径 `_register_now`，不经授权门。
要守的是 AI **程序化调用** `add_skill` / `remove_skill`。

**不阻断自进化**：`evolve()` → `growth.apply()` 只产出 `update_registry_entry`
（改的是路由表元数据，不是增删 skill），仍属自主，闭环不受影响。

## 更新获取层：消费与发布分离

**职责边界**：本套件面向使用者，**只消费不发布**。建仓 / 提交 / 推送 / 发版属作者端职责，不在框架能力内——`GitRemote` 刻意不提供 `commit` / `push` / `bootstrap` / `set_remote`，并由 `tests/test_suite.py::test_update_layer_is_read_only` 用 `hasattr` 断言锁死这条边界。

- **取版本**：比对本地 HEAD 与上游 ref，只报告不写任何东西。
- **拉更新**：启动（异步后台、不阻塞首用）或用户显式要求时拉取，走 `--ff-only`——本地有未提交改动时 git 直接拒绝，绝不静默覆盖；拉取后热更新路由表。
- **拉取后校验**：跑完整性校验，as-is 被篡改即上报 drift。
- **降级**：本地非 git 仓库或无远端时安全跳过，不中断使用。
- **网络兜底（内置）**：`git pull` 遇到网络错误（解析失败 / 连接超时 / 502 / 503 / 重置等）时，自动向 `manifest.deploy.accelerator_url`（默认用户自建腾讯云函数）请求**全员候选 IP**（`source=all`，不指定源/关键字/域名，合并 ziyou 自建探测与第三方镜像）。云函数侧已按延迟排序，**skill 内不做本地测试**，直接每域取前 `accelerator_top_n`（默认 3）最快候选；起一个**作用域限定在单次 git 调用的本地 CONNECT 代理**把 github 域名钉到候选 IP，由**本地 git 完成拉取**——云函数只供 IP、不拉仓库。单次调用内候选按序 failover（#1 不通自动试 #2/#3，全不通回退正常 DNS）；整体失败则**重新拉取云函数最新候选再试**（最多重新拉取 20 次），仍败回退系统代理/正常 DNS。绝不写系统 hosts、无需管理员。**启动异步后台**：skill 启动时异步查远端版本并条件拉取（未变 no-op），不阻塞首用；安装位置由运行时**实际仓库状态启发式判定（非硬编码目录名）**——非受管 git 套件仓库由 `sync_before_use` 内部自动跳过。用户也可显式 `sync` 同步拉取。

> 为什么这样切：用户拿到这个 skill 是为了用，不会也不需要发布。把作者端的发布塞进用户端框架，既放大了权限面，也让用户承担了本不属于他们的凭据与仓库管理负担。

## 自启用（启发式 · 免指定）

skill 常态是"技能"而非"套件"；但本套件自带 `SKILL.md`，置于 agent 的 skill 发现目录即被启动时自动发现，无需在对话中指定。**此能力不靠脚本实现，而是靠 SKILL.md 中的启发式自检引导教 AI 判断与行动**（见 SKILL.md「自启用」小节 6 步自检）。发现目录命名因软件而异（未必叫 skills），能否自启取决于运行环境与权限——不预约定，交 AI 启发式裁决。

## 上下文预算（硬约束）

| 约束 | 规则 |
| --- | --- |
| 渐进披露 | 元数据常驻 → SKILL.md 触发才入 → references/scripts 按需 |
| 两段式护 ctx | 廉价召回只取元数据，全量描述永不每 query 入 ctx |
| 蒸馏=压缩非堆积 | 轨迹压成紧凑规则，不回放原始日志；语义去重 + 剪枝 |
| 按需触发加载 | experience / user-model / library-map 存磁盘，仅触发匹配子集入 ctx |
| 脚本不占 ctx | 蒸馏 / 成长 / 校验 / 回滚 / 版本均为可执行脚本 |

## 落地状态

- 已实现：分层骨架、双模式路由裁决、蒸馏三 Lane、共享知识库、成长与守门、只读更新获取（版本查询 / 拉取 / 拉取后完整性校验与三方对账 / 异步后台条件拉取 + 云函数 IP 钉定兜底 / `accelerator_retries` 默认 20 可配）。自启用为**引导式**（SKILL.md 6 步启发式自检，无脚本实现）。
- 壳层健壮性：`sync()` 拉取后重载内存 manifest + 路由表，杜绝过期副本覆盖；`Suite.discover()` 幂等自动发现未注册 skill（逐 skill 隔离）；`register` 即闭合完整性 pin 链；`approve_proposal()` / `reject_proposal()` 闭合「提案 → 批准 / 拒绝」状态机；原生 skill 执行受 `allow_native` 闸门（默认开，可关，文档明示 = 任意代码执行）；`accelerator_url` 为 opt-in（manifest 不配则走系统代理/正常 DNS）；拉取超时/后台异常统一收敛为日志可观测，不再静默吞掉；子 skill front-matter `triggers` 必填、`domain` 可选（仅作次级召回词，缺省空列表不影响路由），用户丢最小 skill 零摩擦纳入。
- 用户建模闭环：`learn()` 沉淀 `{skill_id: 次数}`，`route()` 每轮读它做小幅排序加成（上限 0.5）。只写不读的画像等于死数据，所以这条链路两端都接上了。
