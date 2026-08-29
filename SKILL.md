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
| `deploy/` | 部署层：完整性校验 / 远端适配 / push / pull |
| `bridge/` | 桥接适配层，每个生态一个薄映射 |
| `state/` | 运行时产物：共享知识库、提案、棘轮快照 |

## as-is 保证

套件绝不向 `skills/<id>/` 写入任何 router 代码，子 skill 与其独立发布形态逐字节一致。content-hash 记录于 manifest，部署时比对；漂移即拦截。

## 统一契约

- `llm`：只有 `SKILL.md`，框架交接路径给 agent 自行执行（最原样，零改动）。
- `native`：提供 `handler.py` 实现 `describe / can_handle / invoke / health`（更快更确定）。不愿改就用 llm 模式。

## 每次使用前

1. `Suite.sync()` —— 比对上游并拉取更新，热更新路由表；远端未配置则安全跳过。
2. `Suite.route(query)` —— 两段式召回（只读元数据，全量描述永不入 ctx）→ 裁决（priority↓ → scope 窄胜宽 → 兜底 llm）→ 四策略执行（直连 / 级联 / 管道 / 并行）。

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

## 自启用（无需每次指定）

skill 的常态是"技能"而非"套件"，但本套件自带 `SKILL.md`，因此只要置于某 agent 的 skill 发现目录，启动即被自动发现，无需在对话中指定。

- 常见约定：用户级 `~/.workbuddy/skills/skill-router-suite/`、项目级 `<项目>/.workbuddy/skills/skill-router-suite/`。**但目录命名因软件而异**（可能为 `plugins/`、`commands/`、`.agents/` 等），不能假设都叫 `skills`。
- 是否自启用、装到哪个目录，由 **AI 依当前环境裁决**，脚本只给线索与引导、绝不复制文件、绝不写系统目录。

**判定（线索）**：`Suite.install_status()` 返回 `current_root` / `parent_dir` / `looks_like_skills_dir`（仅弱线索：父目录是否命中常见 skills 命名，**非权威**）/ `candidates`（常见约定候选）/ `guidance`（AI 须核实候选确为当前 agent 的发现目录，否则查文档或询问用户）。

**安装计划（AI 执行复制）**：`Suite.install_plan([target])` 返回 `source` / `destination` / `exists` / `recommended_action`（copy 或 skip），**不复制**。AI 据计划核实目录为当前 agent 的发现目录后，自行复制（如 `cp -r` 或文件工具），目标已存在时先确认是否保留用户改动实例。

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
s.install_status()                # 自启用线索 + 候选目录 + 引导（AI 核实目录）
s.install_plan()                  # 复制计划（源/目标/是否已存在），不复制
s.install_plan(target="项目/.workbuddy/skills")  # 指定目录的计划
```

等价命令行（除 `sync`、`version`、`status`、`install-plan` 外，每个子命令执行前自动拉取一次上游更新）：

```bash
python scripts/cli.py list
python scripts/cli.py route "查一下销售报表" --strategy cascade
python scripts/cli.py add pms --source user_drop
python scripts/cli.py remove pms
python scripts/cli.py learn obs/traces.json
python scripts/cli.py evolve
python scripts/cli.py version
python scripts/cli.py status
python scripts/cli.py install-plan
python scripts/cli.py install-plan --target "项目/.workbuddy/skills"
python scripts/cli.py sync --force
```

验收：`python tests/test_suite.py`、`python tests/test_deploy_remote.py`（全绿）。

## 参考

完整架构（五层形态、蒸馏与成长分离、非对称同步、上下文预算）：`references/architecture.md`
