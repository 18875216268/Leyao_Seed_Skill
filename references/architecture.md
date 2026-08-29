# Skill 路由套件 · 架构

## 形态 Form C

**通用套件（独立可发布）＋ 桥接适配层**。核心通用、可单独开源；具体生态各挂一个薄 `bridge/<eco>/`，只做声明映射，不碰核心、不耦合。

- 不是 A（纯独立 meta-skill 无桥接）：已有真实消费方，无桥接则集成需复制或分叉，破坏单一事实源。
- 不是 B（嵌入某个生态模块）：牺牲通用与开源洁度，无法被其他生态复用。

## 五层

| 层 | 落点 | 职责 |
| --- | --- | --- |
| ① 总则 | `manifest.json` | 目录约定 / as-is 保证 / 统一契约 / 治理边界，可机校验 |
| ② skill 包 | `skills/` + `registry/skills.json` | 子 skill 原样目录 + 路由表（唯一事实源）+ 裁决 |
| ③ 进化层 | `evolution/` | 蒸馏（只读·生产者）→ 共享知识库 → 成长 / 建模 / 守门（读写·驱动者） |
| ④ 更新获取层 | `deploy/` | 只读消费：完整性校验 / 远端适配 / 取版本 / 启动或按需拉取；网络异常时经自建云函数直连兜底 |
| ⑤ 版本 | `manifest.json` | semver + 每 skill `version_pin`（lockfile 防漂移） |

## 子 skill 原样与关联

- **物理原样**：`skills/<id>/` 与其独立发布形态逐字节一致，套件零写入。
- **关联外置**：路由表以 `path` 指向目录、`mode` 声明调用方式。关联是元数据，不在 skill 内部。
- **双模式**：`llm`（把 SKILL.md 全文交 agent 执行，最原样）／ `native`（薄 `handler.py` 实现 `describe / can_handle / invoke / health`，更快更确定）。

## 路由：两段式 + 裁决 + 四策略

1. **廉价召回**：只读 `triggers` / `domain` / `negative_triggers` / `enabled` 等元数据。
2. **精确排序**：命中数 + `priority` + `scope` 具体度 + 经验加权（route 加分、avoid 减分；`candidate` 状态不参与）。
3. **裁决**：单命中直连；多命中取排序最优（排序已编码 priority↓ 与 scope 窄胜宽）；无命中兜底 llm。
4. **四策略**：直连 / 级联兜底 / 管道编排（按 `depends` 拓扑排序）/ 并行扇出。

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
| 增 / 删子 skill | 自主 |
| 改子 skill 内容 | **需用户授权**，提案 → 批准 → 执行 |
| 更新路由表 entry | 自主（变更后必须） |
| 触发部署 | 自主 |

## 更新获取层：消费与发布分离

**职责边界**：本套件面向使用者，**只消费不发布**。建仓 / 提交 / 推送 / 发版属作者端职责，不在框架能力内——`GitRemote` 刻意不提供 `commit` / `push` / `bootstrap` / `set_remote`，并由 `tests/test_suite.py::test_update_layer_is_read_only` 用 `hasattr` 断言锁死这条边界。

- **取版本**：比对本地 HEAD 与上游 ref，只报告不写任何东西。
- **拉更新**：启动或用户要求时拉取，走 `--ff-only`——本地有未提交改动时 git 直接拒绝，绝不静默覆盖；拉取后热更新路由表。
- **拉取后校验**：跑完整性校验，as-is 被篡改即上报 drift。
- **降级**：本地非 git 仓库或无远端时安全跳过，不中断使用。
- **网络兜底（内置）**：`git pull` 遇到网络错误（解析失败 / 连接超时 / 502 / 503 / 重置等）时，自动向 `manifest.deploy.accelerator_url`（默认用户自建腾讯云函数）请求 `ziyou` 源可达 IP，再用 `curl --resolve` 把 GitHub 域名钉到该 IP 直连下载 tarball 覆盖工作树——作用域限定在单次调用，绝不写系统 hosts，SNI/TLS 照常校验。源固定 `ziyou`（云函数自建 DNS+TCP 探测，零第三方依赖、无 MITM 面）。`api_fallback` 可关。

> 为什么这样切：用户拿到这个 skill 是为了用，不会也不需要发布。把作者端的发布塞进用户端框架，既放大了权限面，也让用户承担了本不属于他们的凭据与仓库管理负担。

## 上下文预算（硬约束）

| 约束 | 规则 |
| --- | --- |
| 渐进披露 | 元数据常驻 → SKILL.md 触发才入 → references/scripts 按需 |
| 两段式护 ctx | 廉价召回只取元数据，全量描述永不每 query 入 ctx |
| 蒸馏=压缩非堆积 | 轨迹压成紧凑规则，不回放原始日志；语义去重 + 剪枝 |
| 按需触发加载 | experience / user-model / library-map 存磁盘，仅触发匹配子集入 ctx |
| 脚本不占 ctx | 蒸馏 / 成长 / 校验 / 回滚 / 版本均为可执行脚本 |

## 落地状态

- 已实现：五层骨架、双模式路由裁决、蒸馏三 Lane、共享知识库、成长与守门、只读更新获取（版本查询 / 拉取 / 拉取后完整性校验）、复合版本；`tests/test_suite.py` 20/20 与 `tests/test_deploy_remote.py` 3/3 全绿。
- 待定：初始 skill 内容、各生态桥接映射。
