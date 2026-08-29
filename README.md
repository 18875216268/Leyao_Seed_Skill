# skill-router-suite

通用 skill 路由套件：声明式路由表（唯一事实源）＋ 子 skill 物理原样（零侵入）＋ 裁决 ＋ 进化层（蒸馏只读 / 成长读写分离）＋ 更新获取层（用户端只读）＋ 复合版本。

> **职责边界**：本套件面向**使用者**，只消费不发布。建仓 / 提交 / 推送 / 发版属**作者端**职责，刻意不提供在框架内（`GitRemote` 无 `commit` / `push` / `bootstrap`，由测试断言锁死）。

**形态 Form C** —— 通用套件（独立可发布）＋ 桥接适配层。核心通用、可单独开源；具体生态（leyao / Pms）各挂一个薄 `bridge/<eco>/`，只做声明映射，不碰核心、不耦合。

## 架构

| 层 | 落点 | 职责 |
| --- | --- | --- |
| ① 总则 | `manifest.json` | 目录约定 / as-is 保证 / 统一契约 / 治理边界，可机校验 |
| ② skill 包 | `skills/` + `registry/skills.json` | 子 skill 原样目录 + 路由表（唯一事实源）+ 裁决 |
| ③ 进化层 | `evolution/` | 蒸馏（只读·生产者）→ 共享知识库 → 成长 / 建模 / 守门（读写·驱动者） |
| ④ 更新获取层 | `deploy/` | 只读消费：完整性校验 / 远端适配 / 取版本 / 启动或按需拉取 |
| ⑤ 版本 | `manifest.json` | semver + 每 skill `version_pin`（lockfile 防漂移） |

## 目录结构

```
skill-router-suite/
├── SKILL.md              给 agent 的操作手册
├── suite.py              门面：一次装配 路由/进化/部署/版本
├── manifest.json         版本 + 总则 + 每 skill pin
├── core/                 通用内核：contract / registry / resolver / arbitrator / executor / router
├── evolution/            进化层：permissions / pipeline / distiller / store / growth / user_modeler / gate
├── deploy/               更新获取层（只读）：integrity / remote / pull / connectivity
├── registry/skills.json  路由表（唯一事实源）
├── skills/               子 skill 原样目录（套件零写入）
├── bridge/               桥接适配层（Form C 的桥）
├── obs/                  观测流（轨迹）
├── state/                运行时：共享知识库 / 提案 / 棘轮快照
└── tests/test_suite.py   全链路测试
```

## 快速开始

```bash
python tests/test_suite.py           # 27/27 passed
python tests/test_deploy_remote.py   # 4/4 passed
python tests/test_connectivity.py    # 8/8 passed
```

```python
from suite import Suite

s = Suite()
s.version()                       # 查询本地/上游版本（只读）
s.sync()                          # 拉取上游更新并热更新路由表（未配置远端则安全跳过）
s.route("查一下销售报表")          # 两段式召回 → 裁决 → 四策略执行
s.add_skill("pms", "user_drop")   # 三源之一：原样放入 → 派生 entry → 刷新路由表
s.modify_skill("pms", {...})      # 改内容：生成提案，等用户授权
s.learn(traces)                   # 轨迹蒸馏 + 用户建模
s.evolve()                        # 消费知识资产，做针对性变异
```

## 关键设计

- **原样 + 关联**：内容在 `skills/<id>/`，关联元数据在路由表（`path` / `mode`）。套件零侵入，子 skill 可独立 star / fork。
- **两段式路由护上下文**：廉价召回只读元数据，全量 skill 描述永不每 query 入 ctx。
- **蒸馏 ≠ 成长**：蒸馏只读产出知识资产，成长读写消费并驱动修改，两者经共享知识库解耦。
- **守门是代码硬闸**：棘轮只升不降、评估门量化、置信门控让 candidate 永不进生产路径。
- **消费与发布分离**：用户端只取版本与拉取更新（`--ff-only`，绝不覆盖本地改动）；发布在框架外由作者端完成，边界由测试断言锁死。

## 当前状态

已实现：五层骨架、core 路由裁决（双模式 + 四策略）、evolution 蒸馏 / 成长 / 建模 / 守门、deploy 只读更新获取（版本查询 / 拉取 / 拉取后完整性校验）、复合版本；23/23 + 4/4 + 8/8 测试全绿。

待定：初始 skill 内容、各生态桥接映射。

参考：`../设计文档/Skill路由套件架构设计v1.0.md`
