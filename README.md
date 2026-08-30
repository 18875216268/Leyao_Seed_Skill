# LeyaoSeedSkill

通用 skill 路由套件：声明式路由表（唯一事实源）＋ 子 skill 物理原样（零侵入）＋ 裁决 ＋ 进化层（蒸馏只读 / 成长读写分离）＋ 更新获取层（用户端只读）＋ 复合版本。

> **职责边界**：本套件面向**使用者**，只消费不发布。建仓 / 提交 / 推送 / 发版属**作者端**职责，刻意不提供在框架内（`GitRemote` 无 `commit` / `push` / `bootstrap`，由测试断言锁死）。

**零依赖**——只用 Python 标准库，不引 numpy / pandas / sklearn / PyYAML。这是定位，不是偏好：套件要能被丢进任意环境直接跑起来。

**给 agent 的操作手册是 `SKILL.md`**，本文件只是仓库说明，两者不重复。

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
LeyaoSeedSkill/
├── SKILL.md              给 agent 的操作手册（唯一入口）
├── suite.py              门面：一次装配 路由/进化/部署/版本
├── manifest.json         版本 + 总则 + 每 skill pin
├── core/                 内核：contract / frontmatter / registry / resolver
│                               / arbitrator / executor / router / audit
│                               / atomic / lint
├── evolution/            进化层：permissions / pipeline / distiller / store
│                               / growth / user_modeler / gate
├── deploy/               更新获取层（只读）：integrity / remote / pull / connectivity
├── registry/skills.json  路由表（唯一事实源）
├── skills/               子 skill 原样目录（套件零写入）
├── references/           api.md / architecture.md（按需加载，不进常驻上下文）
├── scripts/cli.py        命令行入口
├── state/                运行时：共享知识库 / 提案 / 棘轮快照 / 审计日志
└── tests/                8 个测试文件，121 项
```

## 快速开始

```bash
python scripts/cli.py discover   # 扫描 skills/ 下未注册子 skill 并登记（幂等）
python scripts/cli.py route "查一下销售报表"
python scripts/cli.py list
```

```python
from suite import Suite

s = Suite()
s.version()                       # 查询本地/上游版本（只读）
s.sync()                          # 拉取上游更新并热更新路由表（未配置远端则安全跳过）
s.route("查一下销售报表")          # 两段式召回 → 裁决 → 四策略执行
s.discover()                      # 批量扫描 skills/ 下未注册子 skill
s.add_skill("pms", "user_drop")   # 增：只返回提案，批准后由 approve_proposal 落地
s.modify_skill("pms", {...})      # 改：只返回提案，等用户授权
s.remove_skill("pms")             # 删：只返回提案，等用户授权
s.approve_proposal("prop-xxx")    # 批准后落地（add 登记 / remove 摘除 / modify 重派生）
s.learn(traces)                   # 轨迹蒸馏 + 用户建模
s.evolve()                        # 消费知识资产，做针对性变异
```

跑测试：

```bash
python tests/test_suite.py            # 37
python tests/test_spec_alignment.py   # 35
python tests/test_audit_trace.py      #  9
python tests/test_pull_deadline.py    # 16
python tests/test_connectivity.py     #  8
python tests/test_atomic_write.py     #  7
python tests/test_deploy_remote.py    #  5
python tests/test_performance.py      #  4
```

合计 **121 项，全绿为落地门槛**。每个文件自带 `main()`，不依赖 pytest。
测试临时目录落在套件**同级**的 `.suite_test_tmp`，由 `tests/_harness.py` 自动回收 24 小时前的残留。

> 跑测试会在套件目录内产生 `__pycache__`（已被 `.gitignore` 覆盖）。若要拷贝分发，
> 先 `find . -name __pycache__ -type d -prune -exec rm -rf {} +`，清理后应为 45 个文件。

## 关键设计

- **原样 + 关联**：内容在 `skills/<id>/`，关联元数据在路由表（`path` / `mode`）。套件零侵入，子 skill 可独立 star / fork。
- **两段式路由护上下文**：廉价召回只读元数据，全量 skill 描述永不每 query 入 ctx。
- **蒸馏 ≠ 成长**：蒸馏只读产出知识资产，成长读写消费并驱动修改，两者经共享知识库解耦。
- **守门是代码硬闸**：棘轮只升不降、评估门量化、置信门控让 candidate 永不进生产路径。
- **消费与发布分离**：用户端只取版本与拉取更新（`--ff-only`，绝不覆盖本地改动）；发布在框架外由作者端完成，边界由测试断言锁死。
- **路由可审计**：每次调用产生 `route` + `skill.invoke` 两条事件挂同一 trace，落 `state/audit.log`（JSONL，5MB 轮转保留 3 份），字段对齐 OTel GenAI 语义约定。审计记成败、耗时与决策依据（`query` / `strategy` / `routed`），**绝不记 skill 返回内容**。
- **增删与改子 skill 都需授权**：`add_skill` / `remove_skill` / `modify_skill` 一律只生成提案等待批准，不静默增删改用户资产。唯一豁免是 `discover()`——用户把目录放进 `skills/` 本身就是授权。

## 自进化闭环

丢入子 skill → `discover()` 扫描登记 → 蒸馏 front-matter 派生路由表条目 → 路由命中 →
执行成败进审计 → `learn()` 蒸馏轨迹 → `evolve()` 消耗知识资产做针对性变异 →
变更再次进审计。全过程可回放（`replay(trace_id)`），漂移可解释。

## 当前状态

五层骨架完整，121 项测试全绿，套件 lint 自检 CLEAN。

- 完整 API 签名、lint 规则码全集、审计事件字段、deploy 配置键：`references/api.md`
- 架构（分层、蒸馏与成长分离、非对称同步、上下文预算）：`references/architecture.md`
