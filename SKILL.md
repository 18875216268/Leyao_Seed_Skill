---
name: LeyaoSeedSkill
description: 通用 skill 路由套件。把多个原样子 skill 编排成可路由、可进化、可非对称更新的技能库。当用户要把若干 skill 统一管理、需要按查询自动选中并调用合适的 skill、注册或增删子 skill、蒸馏使用轨迹做成长与守门、或从上游拉取套件更新时使用。也适用于「我有好几个 skill 想统一管理」「让 agent 自动选 skill」「skill 库自进化」「skill 路由表」这类诉求。两条入口：Python（suite.Suite）与命令行（scripts/cli.py）。
domain: [skill-router, skill-suite, skill 管理, 技能路由]
triggers:
  - skill 套件
  - 路由 skill
  - 注册 skill
  - 部署 skill
  - 进化 skill
  - 管理多个 skill
  - 自动选择 skill
# 能力声明（OWASP AST03 最小权限 / AST04 诚实元数据）。本套件确实出网（git 拉上游）、
# 确实起子进程（git）、确实写盘（路由表/审计日志），因此如实声明而非隐去。
# 若自行配置了 accelerator_url，请把它所在的域名一并加进 network.allow。
network:
  allow: ["github.com"]
  deny: "*"
shell: true
permissions:
  deny_write:
    - SOUL.md
    - MEMORY.md
    - AGENTS.md
    - IDENTITY.md
    - USER.md
risk_tier: L2
version: 1.0.0
scope: suite.*
priority: 0
agent_created: true
---

# LeyaoSeedSkill · Skill 路由套件

把若干**原样不改**的子 skill 编排成一个可路由、可进化、可只读更新的技能库。
子 skill 目录里的文件一个字节都不会被动过。

## 目录约定

| 路径 | 作用 |
| --- | --- |
| `skills/<id>/` | 子 skill 原样目录，套件零写入 |
| `registry/skills.json` | 路由表，唯一事实源 |
| `manifest.json` | 版本 + 每 skill 内容 hash + deploy 配置 |
| `core/` | 内核：契约 / front-matter 解析 / 路由表 / 两段式召回 / 裁决 / 四策略执行 / 审计 / 原子写 / 质量门禁 |
| `evolution/` | 进化层：蒸馏（只读）→ 共享知识库 → 成长（读写）/ 用户建模 / 守门 / 权限 |
| `deploy/` | 更新获取层（只读）：完整性校验 / 远端适配 / 连通性 / 拉取 |
| `state/` | 运行时产物：共享知识库、提案、棘轮快照、审计日志 |
| `state/audit.log` | 结构化审计日志（带 trace/span，5MB 轮转保留 3 份） |
| `tests/` | 8 个回归测试文件，全绿为落地验收门槛 |
| `references/` | 深度文档：`architecture.md` 架构、`api.md` 完整 API 与规则码 |

依赖方向是单向的：`evolution → core`、`deploy → core`、`core` 只依赖标准库。
`core` 不反向依赖任何上层，`evolution` 与 `deploy` 之间无环。

## 关于 `skills/` 下的子 skill

这些子 skill **由本套件管理，不是宿主可独立发现的 skill**。它们的路径是
`<套件根>/skills/<id>/SKILL.md`（套件根之下两层），不在宿主的常规发现路径里。
但若宿主的发现机制是递归扫 `**/SKILL.md`，它们也可能被单独列出来。

遇到这种情况：**以本套件的路由结果为准**，不要绕过路由直接按宿主的列表调用。
否则同一件事会有两个入口，路由表、审计、经验积累全部失去意义。

## 放入第一个子 skill（闭环）

整个流程不需要改任何框架代码：

1. 把子 skill 的目录**原样**拷进 `skills/<id>/`（它自带 `SKILL.md`，可自带脚本）。
2. `Suite.discover()`（命令行：`python scripts/cli.py discover`）—— 扫描到未注册目录，
   读 front-matter 蒸馏出路由表 entry，自动跑一次 lint，然后登记。幂等，重复调用不会重复登记。
   `discover` 是批量扫描；`add <id>` 是按 id 注册单个，两者不重复。
3. 之后 `Suite.route(query)` 就能命中它；命中后按 `mode` 执行。
4. 用得越多，轨迹蒸馏出的经验规则与用户使用频次会反过来影响排序。

   **注意经验规则的作用边界**：它只在**已召回的候选集合内**调整排序（`experience_boost`
   作用于 `rank` 阶段，`recall` 不接受经验数据），**不会把零召回的 skill 补进候选**。
   也就是说，召回能力取决于子 skill 自己 `triggers` / `description` 写得好不好；
   经验只能在"已经召回到若干个"之间挑更合适的那个。

   这条边界是刻意的——宁可 fallback 也不错召。它带来一个后果：某个词（例如"业绩"）
   如果**任何**子 skill 的元数据里都没写，那么即使用户反复手动改派给它十次，
   系统也学不会。遇到这种情况，正解是**把该词补进对应子 skill 的 `triggers` 或
   `description`**（走 `modify_skill → approve_proposal`），而不是指望蒸馏。
5. 若将来子 skill 的 `SKILL.md` 被改动，`Suite.sync()` 与 `integrity.verify()`
   会报内容漂移；要改内容必须走 `modify_skill → approve_proposal`。

子 skill 的 front-matter 最小形态（只有 `triggers` 是强制的）：

```yaml
---
name: <id>            # 可选，缺省取目录名
mode: llm             # 可选，有 handler.py 则自动判为 native
triggers:            # 必填：主召回词（如「查销售报表」「pms」）
  - 示例触发词
priority: 0          # 可选，数值越大越优先
scope: "*"           # 可选，越具体越优先胜出
domain: []           # 可选：次级召回词（如 ["报表","销售"]）
---
```

符合 Anthropic Agent Skills 官方规范的 skill（只有 `name` + `description`、没有
`triggers`）同样能进路由表——`description` 本身就是官方的召回字段，本套件按覆盖度
计分用它做召回。只有 `triggers` 与 `description` **都为空**时才会被拒绝。

## 统一契约

- `llm`：只有 `SKILL.md`，框架交接路径给 agent 自行执行（最原样，零改动）。
- `native`：提供 `handler.py` 实现 `describe / can_handle / invoke / health`（更快更确定）。**注意：`handler.py` 经 `exec_module` 原样执行，等于任意本地代码执行——只注册你信任的 skill**。可用 `Suite(allow_native=False)` 关闭原生执行（关闭后匹配到 native skill 直接拒绝，不静默跑未知代码）。不愿改就用 llm 模式。

## 每次使用前

1. `Suite.sync()` —— 比对上游并拉取更新，热更新路由表；远端未配置则安全跳过。拉取后内存 manifest 与路由表一并重载，并对账 registry / manifest / 文件系统三方一致性（只留痕、不阻断），绝不会用过期副本覆盖刚拉取的配置。
2. `Suite.discover()` —— 扫描 `skills/` 下未注册子 skill 并幂等登记（逐 skill 隔离，缺 SKILL.md 或异常不阻断其余）。返回 `[(skill_id, action, detail)]`，`action ∈ registered / skipped / error`。
3. `Suite.route(query)` —— 两段式召回（只读元数据，全量描述永不入 ctx）→ 裁决（priority↓ → scope 窄胜宽 → 兜底 llm）→ 四策略执行（直连 / 级联 / 管道 / 并行）。返回值带 `trace_id`，可串起后续调用。

## 权限矩阵（先于一切进化动作）

| 操作 | 权限 |
| --- | --- |
| 读子 skill | 自主 |
| 增 / 删子 skill | 自主 |
| 改子 skill 内容 | **需用户授权**，提案 → 批准 / 拒绝 → 执行 |
| 更新路由表 entry | 自主（变更后必须） |

提案是状态机，`pending` 有两个终态：`approved`（批准并落地）与 `rejected`（关闭）。
只有批准没有拒绝的话，没人认领的提案会一直悬着，pending 列表最终变成噪音。
用 `pending_proposals()` 列出待办，`approve_proposal()` / `reject_proposal()` 收口。

## 安全扫描（lint）

`discover()` 登记每个新 skill 时**会自动跑** `lint_skill`，结果进 warning 日志与审计日志（事件 `discover_lint`）。

但它是**只报告、不阻断**——已注册的 skill 不会因 lint 报警被拒收。这是刻意的：生态里大量存量 skill 没有 OWASP 声明式字段，硬阻断会让 `discover()` 形同虚设。

所以来源不明的 skill，请主动跑一次并人工判断：

```python
from core.lint import lint_skill
issues = lint_skill("skills/<id>", root="<套件根>")   # -> [{"level","code","message"}, ...]
```

`level` 三档：`error` / `warn` / `info`。**`error` 级应先处理再用**——只有三族：`secret.*`（凭证）、`bad_mode`、`missing_skill_md`。

| 规则族 | 代表码 | 含义 |
| --- | --- | --- |
| 凭证泄露 | `secret.openai_key` `secret.anthropic_key` `secret.aws_akid` `secret.github_token` `secret.slack_token` `secret.google_api` `secret.stripe_live` `secret.private_key` `secret.jwt` `secret.assigned` | 硬编码密钥 / 私钥 / JWT。级别 `error`。**只报文件、行号、规则名，绝不回显匹配内容**——lint 报告会进 LLM 上下文，回显等于把密钥二次送进模型 |
| 高熵串 | `secret.high_entropy` | 熵值 ≥4.0 的长串。级别 `warn`（可能是 base64 资源，需人工判断） |
| 权限声明一致性 | `undeclared_network` `undeclared_shell` `undeclared_write` | 代码里实际发网络请求 / 起子进程 / 写文件，front-matter 却没声明 |
| 危险声明 | `network_allow_all` `network_tier_mismatch` `network_is_bool` `shell_tier_mismatch` | 网络白名单写成全通、无限制出网却自称低风险、`network` 写成布尔值、shell 权限与 `risk_tier` 对账不上 |
| 规范长度 | `name_too_long`（>64）`desc_too_long`（>1024）`too_long`（body>500 行） | 超出 Anthropic Agent Skills 规范上限，会导致该 skill 被截断或无法发现 |

误报已被抑制：占位符（`xxx` / `<your-key>` / `****`）、环境变量引用（`os.environ` / `process.env` / `${VAR}`）不算泄露；测试目录整支跳过。

可选的 OWASP 对齐字段（未声明即按最宽处理，**建议显式收窄**）：

```yaml
network:
  allow: ["api.example.com"]      # 域名白名单；写 "*" 会被判 network_allow_all
  deny: "*"                       # 白名单之外一律拒绝（推荐，但不豁免上面的检查）
permissions:
  deny_write: ["SOUL.md", "MEMORY.md", "AGENTS.md"]   # 保护身份文件
risk_tier: L1                     # L0 只读 / L1 受限 / L2 提权 / L3 高危
```

`allow` 与 `deny` 管的是两件事，别互相抵消：`deny` 约束的是**白名单之外的默认动作**，
而 `allow` 一旦含通配符，白名单已覆盖全部域名，`deny` 便无从生效——所以 `allow: ["*"]`
无论 `deny` 怎么写都会被判 `network_allow_all`。它是 `warn` 而非 `error`：无限制出网确有
合法场景（浏览器 / 抓取类 skill），判死会逼作者删掉声明退回 `undeclared_network` 的 `warn`，
等于惩罚诚实声明。真正的对账由 `network_tier_mismatch` 承担——无限制出网却自称 L0/L1 会
另报一条，与 `shell_tier_mismatch` 同构。

## 进化层（蒸馏与成长分离）

- 蒸馏（只读·生产者）：Lane A 注册派生（frontmatter → 路由表）／ Lane B 轨迹经验／ Lane C 库级结构。只读取、只产出，绝不修改受管资产。
- 共享知识库（first-class）：`experience` / `user-model` / `library-map`，除索引外均磁盘持久、按需加载。
- 成长（读写·驱动者）：反思 → 进化 → 评估，消费知识资产做针对性变异，严格优于基线才保留。
- 用户建模：`learn()` 观察轨迹沉淀 `{skill_id: 次数}`，`route()` 每轮读它做**小幅**排序加成（上限 0.5，刻意小于一个 trigger 命中的 2.0——个性化只能在同一档内调序，不能让「你以前用过」推翻「这个更相关」）。零历史时恒为 0。
- 守门：棘轮（只升不降，劣化即回滚）＋ 评估门（test-prompts 量化）＋ 置信门控（candidate 永不进生产路径）。
- 评估：`Suite.evaluate(name, test_prompts, runner)` 跑一组加权的 test-prompts 得分数，交棘轮判定 keep 或 rollback。test-prompt 形如 `{"prompt": "...", "expect_contains": [...], "expect_not_contains": [...], "weight": 1}`。
- 留痕：每次成长变更都写入审计日志，附 `before` / `after` / `evidence`（含 `support`、`success_rate`、`rule_id`），可事后回放核对"为什么改了这一条"。

## 更新获取层（用户端 · 只读）

职责边界：**本套件只消费，不发布**。建仓、提交、推送、发版属作者端职责，不在框架能力内——`GitRemote` 刻意不提供 `commit` / `push` / `bootstrap`，由测试断言锁死这条边界。

- 取版本：`Suite.version()` 返回本地与上游 commit，以及是否有更新；不触发任何写操作。
- 拉更新：`Suite.sync()` 在启动或用户要求时拉取，热更新路由表，并在拉取后跑完整性校验（as-is 被篡改即上报 drift）＋ 三方一致性对账。
- 版本：复合 manifest（semver）＋ 每 skill `content_hash` / `version_pin`（lockfile 防漂移）。
- 降级：本地非 git 仓库或无远端时安全跳过，绝不报错中断使用。**若本套件是以拷贝方式安装（目录里没有 `.git`），自动更新不可用，属预期行为**——需要自动更新请用 `git clone` 安装。
- 加速：网络不通时走云函数取可达 GitHub IP 再钉 IP 拉取。**这是 failover 换目标，不是重试同一目标**，所以尝试次数可以大；但整体受时间预算约束。

`manifest.json → deploy` 可配：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `accelerator_url` | 无（**需显式 opt-in**） | 不配则 pull 走系统代理/正常 DNS，不内嵌任何第三方地址 |
| `accelerator_source` | `ziyou` | `all` 会并入第三方镜像源 |
| `accelerator_retries` | `20` | 换 IP 重试上限 |
| `overall_deadline` | `180.0` | **整体时间预算（秒）**，含所有重试与退避 |
| `pull_timeout` | `120.0` | 单次 git 调用上限（秒） |

预算治理：单次受 `pull_timeout` 约束；整体受 `overall_deadline` 约束，剩余预算不足一次有效握手（<10s）即停止；云函数连续 3 次返回空候选则熔断，不对着坏掉的加速器空转；退避用 full jitter，不构成同步重试波前。**没有这套治理时，21 次 × 120s ≈ 42 分钟无上限阻塞。**

## 可观测与回放

审计日志带 trace / span，字段对齐 OTel GenAI 语义约定（`trace_id` / `span_id` / `parent_span_id` / `status` / `duration_ms`）。

```python
from core.audit import tail, replay, new_trace

r = s.route("查一下销售报表")
r["trace_id"]                                    # 本次调用链路 ID，可透传给下一次 route 串成一条
tail(20, root=s.root)                            # 最近 20 条（root 必传，否则会落到进程启动目录）
tail(50, root=s.root, trace_id=r["trace_id"])    # 只看某条链路
replay(r["trace_id"], root=s.root)               # 按时间升序还原整条链路（跨轮转文件）
```

`replay` 只认 `trace_id`，`tail` 是"还不知道 trace_id 时"的入口——两者都要显式传 `root`。

成长类变更同样落审计，可用 `replay` 回放"哪条规则在什么时候把 priority 从 X 改到了 Y"。

**一次完整调用落两条事件**，都挂在同一条 trace 上：

| event | 回答什么 | 关键字段 |
| --- | --- | --- |
| `route` | 路由到了谁 | `query` `strategy` `routed` `skill` `duration_ms` |
| `skill.invoke` | 跑得怎么样 | `skill` `mode` `status` `reason` `duration_ms` |

只有 `route` 时，trace 能证明"选对了"却无法证明"跑通了"——而自进化依赖成败反馈，缺了执行结果就无法事后判断某次权重调整是依据真实失败还是凭空猜测。

三条边界：审计**只记成败、耗时与决策依据，绝不记 `result` 内容**——`route` 事件会记 `query` / `strategy` / `routed`（不记就无法回答"为什么路由到它"，路由可审计性正是本套件的存在理由），但 `skill.invoke` 事件绝不记 skill 的返回体（可能含业务敏感数据）；异常照记后**原样冒泡**，不改变 `direct` 策略下异常向上传播的既有行为；`llm` 模式只记到"已交接"（`handoff: "llm"`），此时 `status: ok` 表示 SKILL.md 已交给 agent，不是任务已完成。

## 召回质量自测

路由的命不命得中，必须能量化，不能靠感觉。做法：为每个 skill 准备 30-50 条 prompt，分三桶——`in-scope`（该命中）、`adjacent`（相邻领域，不该命中）、`out-scope`（完全无关，绝不该命中），分别统计 recall 与 precision，目标 85 / 90。

```python
from core.resolver import recall
hits = recall(s.registry.enabled(), "查一下销售报表")   # 返回候选及其命中信号
```

`recall` 的加权信号（可在 `core/resolver.py` 调权重）：`triggers`（2.0，作者显式声明，最高）→ `name`（1.5）→ `domain`（1.0）→ `description` 覆盖度（2.0）。

三个要点：

- **官方规范对齐**：生态里的 skill 绝大多数只有 `name` + `description`。若只认 `triggers`，这类标准 skill 召回率为零。`description` 按覆盖度（命中词 / 查询词数）计分而非命中数，避免长描述仅因词多占优。
- **准入门禁**：只有 `description` 弱信号时需 ≥2 个词命中才准入，防止单词巧合误召；有 `triggers` / `name` / `domain` 任一强信号则直接准入。
- **匹配会归一化**：触发词与查询都先剥掉空格、标点、连字符再比对，所以「促销 毛利」「促销，毛利」都能命中触发词「促销毛利」，「P-M-S」能命中「PMS」。归一化只**增加**命中、不减少；归一化后短于 2 字符的词项不参与（`C++` 不会退化成 `c` 去误匹配）。

新增 skill 或调整 triggers 后，请重跑一次三桶自测再提交。

## 自启用（启发式 · 免指定）

本套件自带 `SKILL.md`：只要置于当前 agent 的 skill 发现目录，启动即被自动发现，无需用户每次指定。但"发现目录"的命名与位置因 agent 而异（未必叫 skills），且能否自启取决于运行环境与权限——**不预约定，交给 AI 启发式裁决**。

使用前请按以下自检引导行动（不是约束，是教 AI 怎么想）：

1. 分析本 skill 当前使用环境（agent 类型、用户级/项目级、当前所在位置）。
2. 检查当前安装方式是否属于"技能/套件"级别，而非临时副本。
3. 检查本 skill 在当前环境是否已被 agent 启发式自启（启动即加载）。
4. 若未自启，思考如何达成：放到正确的发现目录、核实目录名与权限，无需用户每次指定。
5. 不确定某 agent 的发现目录或约定时，联网查询其文档。
6. 确保本 skill 权限足够高（可读写发现目录），以完成启发式自启。

核心：教 AI 判断与行动，而非预置硬编码目录约定。

## 入口

```python
from suite import Suite

s = Suite()                            # root 缺省为套件目录；allow_native=False 可禁原生执行
s.version()                            # 本地/上游 commit 与是否有更新（只读）
s.sync()                               # 拉取上游更新并热更新路由表（附 compatibility 对账）
s.sync(force=True)                     # 忽略"已有更新"判断，强制拉一次
s.discover()                           # 扫描 skills/ 下未注册 skill 并幂等登记
s.route("查一下销售报表")                # 两段式路由 + 裁决 + 执行，返回带 trace_id
s.route("查一下销售报表", strategy="cascade", trace_id=tid)   # 换策略 / 串链路
s.add_skill("pms", "user_drop")        # 三源之一：原样放入 → 派生 entry → 刷路由表
s.modify_skill("pms", {...})           # 改内容：生成提案，等用户授权
s.pending_proposals()                  # 列出待授权提案
s.approve_proposal("prop-xxx")         # 批准后落地：重派生 entry + 写回变更 + 复 pin + 落盘
s.reject_proposal("prop-xxx")          # 拒绝并关闭提案
s.remove_skill("pms")                  # 注销并落盘
s.learn(traces)                        # 轨迹蒸馏 + 用户建模
s.evolve()                             # 消费知识资产做针对性变异（变异带 evidence）
s.evaluate("pms", test_prompts, runner)  # 跑评估门，棘轮判定 keep / rollback
s.schedule_background_sync()           # 后台线程查版本 + 条件拉取，不阻塞首用
s.save()                               # 显式落盘
```

`modify_skill` 只生成提案，**必须经 `approve_proposal` 或 `reject_proposal` 才收口**——缺这一步就是死循环（提案永远悬空）。`approve_proposal` 对 `modify_skill_content` 会依据当前 SKILL.md 重派生 entry、保留 `manual_overrides`、写回结构化变更、复 pin 完整性。

`route` 的四种策略：`direct`（单命中直连）、`cascade`（按优先级依次尝试直到成功）、`pipeline`（串行串联）、`parallel`（真并发，非伪并发）。`parallel` 走线程池，同时受单次超时（默认 30s）与整体预算约束，**结果按请求顺序返回**（乱序会破坏上下文导致幻觉）；单命中也走同一条并发路径，行为一致。已知限制：Python 无法强制杀死线程，超时只是"不再等待"。

等价命令行（除 `sync`、`version` 外，每个子命令执行前自动拉取一次上游更新）：

```bash
python scripts/cli.py list
python scripts/cli.py discover                      # ★ 主路径：扫描 skills/ 下未注册目录并登记（幂等）
python scripts/cli.py route "查一下销售报表" --strategy cascade
python scripts/cli.py add pms --source user_drop    # 按 id 注册单个，不做扫描
python scripts/cli.py remove pms
python scripts/cli.py proposals
python scripts/cli.py approve prop-xxx
python scripts/cli.py reject prop-xxx
python scripts/cli.py learn traces.json   # traces.json 为任意轨迹 JSON 数组文件，非框架托管目录
python scripts/cli.py evolve
python scripts/cli.py version
python scripts/cli.py sync --force
```

## 验收

8 个测试文件，共 119 项，**全绿为落地门槛**。改动任何一层后请全跑：

```bash
python tests/test_suite.py            # 35 内核：契约/路由表/召回与归一化/裁决/执行/蒸馏/成长/权限/完整性/提案闭环/经验规则作用域边界/端到端
python tests/test_spec_alignment.py   # 35 官方规范兼容性、front-matter 解析、召回缓存、临时区回收边界、lint 安全扫描（含套件自检）
python tests/test_audit_trace.py      #  9 trace 贯穿、执行留痕、成长留痕、轮转、tail 与 replay
python tests/test_pull_deadline.py    # 16 pull 的 deadline 治理与熔断
python tests/test_connectivity.py     #  8 加速器与钉 IP 通道
python tests/test_atomic_write.py     #  7 原子写：失败不留半截 JSON、不留临时文件
python tests/test_deploy_remote.py    #  5 部署层只读边界、上游拉取、漂移上报、CLI 后台同步归类
python tests/test_performance.py      #  4 真并发、超时隔离、整体预算、顺序保序
```

每个文件自带 `main()`，不依赖 pytest。测试用的临时目录落在套件**同级**的 `.suite_test_tmp`
（不在 git 仓库内，避免污染 `NOT_A_REPO` 断言），由 `tests/_harness.py` 统一提供并自动回收
24 小时前的残留；积得太多可直接删掉整个 `.suite_test_tmp`，不影响套件。

跑测试会在套件目录内产生 `__pycache__`。它已被 `.gitignore` 覆盖，不影响版本库；但若要
**把整个目录拷贝分发**，先删掉再拷——否则会把本机 Python 版本的字节码一起带给对方：

```bash
find . -name __pycache__ -type d -prune -exec rm -rf {} +   # 清理后应为 45 个文件
```

## 参考

- 完整 API 签名、lint 规则码全集、审计事件字段、deploy 配置键：`references/api.md`
- 架构（分层、蒸馏与成长分离、非对称同步、上下文预算）：`references/architecture.md`
