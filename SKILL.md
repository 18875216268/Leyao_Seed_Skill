---
name: skill-router-suite
description: 通用 skill 路由套件，把多个原样子 skill 编排成一个可持续进化的技能库。当用户需要把若干 skill 组织为可路由、可进化、可非对称部署的套件时使用，涵盖注册与增删子 skill、按查询裁决路由、蒸馏使用轨迹、成长与守门、以及按需推送或拉取更新。
domain: [skill-router, skill-suite]
triggers:
  - skill 套件
  - 路由 skill
  - 注册 skill
  - 部署 skill
  - 进化 skill
version: 0.1.0
scope: suite.*
priority: 0
agent_created: true
---

# Skill 路由套件

形态 Form C：**通用套件（独立可发布）＋ 桥接适配层**。核心是通用的，桥接层只做声明映射、不耦合核心。

## 目录约定

| 路径 | 作用 |
| --- | --- |
| `skills/<id>/` | 子 skill 原样目录，套件零写入 |
| `registry/skills.json` | 路由表，唯一事实源 |
| `manifest.json` | 版本 + 每 skill pin + 总则校验项 |
| `core/` | 通用内核：契约 / 路由表 / 两段式召回 / 裁决 / 四策略执行 |
| `evolution/` | 进化层：蒸馏（只读）→ 共享知识库 → 成长（读写）/ 建模 / 守门 |
| `deploy/` | 部署层（只读）：完整性校验 / 远端适配 / 拉取（pull） |
| `bridge/` | 桥接适配层，每个生态一个薄映射 |
| `state/` | 运行时产物：共享知识库、提案、棘轮快照 |

## as-is 保证

套件绝不向 `skills/<id>/` 写入任何 router 代码，子 skill 与其独立发布形态逐字节一致。content-hash 记录于 manifest，部署时比对；漂移即拦截。

## 统一契约

- `llm`：只有 `SKILL.md`，框架交接路径给 agent 自行执行（最原样，零改动）。
- `native`：提供 `handler.py` 实现 `describe / can_handle / invoke / health`（更快更确定）。**注意：`handler.py` 经 `exec_module` 原样执行，等于任意本地代码执行——只注册你信任的 skill**。可用 `Suite(allow_native=False)` 关闭原生执行（关闭后匹配到 native skill 直接拒绝，不静默跑未知代码）。不愿改就用 llm 模式。

## 每次使用前

1. `Suite.sync()` —— 比对上游并拉取更新，热更新路由表；远端未配置则安全跳过。拉取后内存 manifest 与路由表一并重载，绝不会用过期副本覆盖刚拉取的配置。
2. `Suite.route(query)` —— 两段式召回（只读元数据，全量描述永不入 ctx）→ 裁决（priority↓ → scope 窄胜宽 → 兜底 llm）→ 四策略执行（直连 / 级联 / 管道 / 并行）。
3. `Suite.discover()` —— 扫描 `skills/` 下未注册子 skill 并幂等登记（逐 skill 隔离，缺 SKILL.md 或异常不阻断其余）；把新丢进来的 skill 一键纳入路由表。

## 权限矩阵（先于一切进化动作）

| 操作 | 权限 |
| --- | --- |
| 读子 skill | 自主 |
| 增 / 删子 skill | 自主 |
| 改子 skill 内容 | **需用户授权**，提案 → 批准 → 执行 |
| 更新路由表 entry | 自主（变更后必须） |

## 进化层（蒸馏与成长分离）

- 蒸馏（只读·生产者）：Lane A 注册派生（frontmatter → 路由表）／ Lane B 轨迹经验／ Lane C 库级结构。只读取、只产出，绝不修改受管资产。
- 共享知识库（first-class）：`experience` / `user-model` / `library-map`，被路由、引导、部署、成长共同消费；除索引外均磁盘持久、按需加载。
- 成长（读写·驱动者）：反思 → 进化 → 评估，消费知识资产做针对性变异，严格优于基线才保留。
- 守门：棘轮（只升不降，劣化即回滚）＋ 评估门（test-prompts 量化）＋ 置信门控（candidate 永不进生产路径）。

## 更新获取层（用户端 · 只读）

职责边界：**本套件只消费，不发布**。建仓、提交、推送、发版属作者端职责，不在框架能力内——`GitRemote` 刻意不提供 `commit` / `push` / `bootstrap`，由测试断言锁死这条边界。

- 取版本：`Suite.version()` 返回本地与上游 commit，以及是否有更新；不触发任何写操作。
- 拉更新：`Suite.sync()` 在启动或用户要求时拉取，热更新路由表，并在拉取后跑完整性校验（as-is 被篡改即上报 drift）。
- 版本：复合 manifest（semver）＋ 每 skill `version_pin`（lockfile 防漂移）。
- 降级：本地非 git 仓库或无远端时安全跳过，绝不报错中断使用。

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

s = Suite()                       # root 缺省为套件目录
s.version()                       # 查询本地/上游版本与是否有更新（只读）
s.sync()                          # 拉取上游更新并热更新路由表
s.route("查一下销售报表")          # 两段式路由 + 裁决 + 执行
s.add_skill("pms", "user_drop")   # 三源之一：原样放入 → 派生 entry → 刷路由表
s.modify_skill("pms", {...})      # 改内容：生成提案，等用户授权
s.learn(traces)                   # 轨迹蒸馏 + 用户建模
s.evolve()                        # 消费知识资产做针对性变异
```

等价命令行（除 `sync`、`version` 外，每个子命令执行前自动拉取一次上游更新）：

```bash
python scripts/cli.py list
python scripts/cli.py route "查一下销售报表" --strategy cascade
python scripts/cli.py add pms --source user_drop
python scripts/cli.py remove pms
python scripts/cli.py learn obs/traces.json
python scripts/cli.py evolve
python scripts/cli.py version
python scripts/cli.py sync --force
```

验收：`python tests/test_suite.py`、`python tests/test_deploy_remote.py`（全绿）。

## 参考

完整架构（五层形态、蒸馏与成长分离、非对称同步、上下文预算）：`references/architecture.md`
