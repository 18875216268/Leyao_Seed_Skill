# 证环评分（evolution/tests）

自我进化层「证」环唯一可自动化的一半：结构完整性 + 一致性 + 路由契约 + 触发评测 + 更新链路回归。语义类判定由 AI 在
`grow.py review` 时承担、由用户终审。

## 运行

```text
python evolution/tests/run_checks.py          # 结构 / 一致性自检（一票否决的客观输入）
python evolution/tests/run_route_drill.py     # 路由演练（判据链分支回归 + 描述模板静态校验，见下）
python evolution/tests/run_task_drill.py      # 任务层演练（推进控制机制在场 + 模板可用，见下）
python evolution/tests/run_task_set.py        # 任务集回归（判定器校准 + pass^k 台账，见下）
python evolution/tests/run_trigger_eval.py    # 触发评测汇总（官方方法，见下）
python evolution/tests/run_update_sandbox.py  # 更新链路回归（零联网沙箱，见下）
```

## 路由演练（run_route_drill.py · 零联网 · 零写入）

判据链在**真实描述**上的分支回归 + 描述分级静态校验，共 **27 项**：**A 静态（9）**（描述非空 / 无旧措辞残留 / 结构化节点【回退】【别名】非空 / 别名≠标题 / 业务节点互斥指引 / 判据含 1c / shapes 含路由决策记录 / D 区形状 / 自由描述降级标注在场）·
**B 场景矩阵（14 例）**（父子按贴合度取 → 子或父 / 不适用语义化排除（"非 X" 不误排除）/ 输入前置挡下（"无需凭证" 不误判）/ 覆盖不全 → 问用户 / 组合需求 → 问用户 / 模糊任务 → 先澄清 / 别名命中 / 无命中 → 亲做）·
**C 过程记录 D 区形状（1）**（候选·依据·结论·复核·必要性·四格·成因 齐备）· **D 结构闭环（3）**（目录与记录结构，详见脚本输出）。
判定口径：**规则化 dry-run**——验证"判据链能否给出正确分支"；真实 AI 行为由实战轨迹（D 区四格）继续校准。改描述、改判据、加/删资产后**必跑**。

`run_checks.py` 输出 JSON（`ok` / `passed` / `total` / `score` / `checks`），退出码 0/1；是棘轮与回滚的**唯一客观输入**
（`grow.py apply` 用它做一票否决，不过则自动回滚）。

## 任务层演练（run_task_drill.py · 零联网 · 零写入）

推进控制机制在**真实文档**上的在场回归（**19 项**）：
**A 机制在场（6）**（`control.md` 三节：循环上限 / 停滞检测 / 回退与重走；上限必须设且**不写死数字**；停滞三条件且动作=回退；回退四类触发 + 三条约束；执行步"路线不改"与"换工具=回退"互指；**台账只写用户区**）·
**B 各步判据（8）**（规划含不适用条件 / 事实与猜测 / 上限，+ **阶段化（需求契约表）** 与 **验收 rubric、不可逆步骤前置标识**；执行含工具循环三要素与满足即停、**阶段出口勾选（防目标漂移）**；验收含红线单列与成本对照上限、**rubric 逐条对照**；交付含台账登记动作、**用户纠正必须采集（--override）**、**outcome 必须如实（禁 false pass）**）·
**C 模板可用（3）**（第 6 节四行在场；模板可直接落地为四区工作区；**形状与模板含需求契约表**（R1 行 / 状态列 / `shapes.md` 第 8 节））·
**D 指路闭环（2）**（`PROCESSOR.md` 实时控制节指向推进控制；本清单已登记）。

判定口径：**静态与模板级校验**。改 `processor/`（判据 / 流程 / 模板）后**必跑**；真实执行行为由实战轨迹（过程记录第 6 节）继续校准。

## 任务集回归（run_task_set.py · 零联网 · 零写入）

对**工作区产物**按任务层判据判定四档（`pass` / `legacy` / `doing` / `fail`），与**用例期望**比对，输出**判定准确率**——测的是判定器本身，不是 AI 行为。用例：`task_set.json`（2 个真实工作区 + 5 个合成用例，每例带期望档位）。

- 三档阈值（`--profile strict|balanced|loose`）用于**找最佳平衡**；实测（2026-09-12）：strict **5/7**（误杀历史产物 2 例）· loose **6/7**（漏放根散落 1 例）· **balanced 7/7（默认，推荐）**。
- 关键设计：把"**缺新机制**"（→ `legacy`，历史合理）与"**结构性错误**"（→ `fail`）分开——既不误杀，也不漏放。
- `--record` 把本次结果追加到**用户区** `.leyao-data/data/state/task_set_results.jsonl`（**pass^k 台账**；只写用户区、不改包内——红线）；交付后登记一次见 `processor/flow/5-deliver.md` 动作 4。

诚实边界：本回归**不度量 AI 行为**；pass^k 需真实多次执行累积（台账机制已就位）。

## 更新链路回归（run_update_sandbox.py · 零联网）

把包与用户区整体复制到临时目录、`LEYAO_SEED_HOME` 指向沙箱——**全程不触网、不写真实包与真实用户区**。
场景：干净更新（含本地新增文件）/ 本地偏离与备份（逐字节比对覆盖前原文）/ 证环不过整体回滚 ×2
（坏路由事实源、`version_sync` 失配）/ 契约拒收 ×5 / 门禁与既有链路回归 / 终检。
**项数不作判据**（脚本内即声明「计数无关断言」）：**全过 + `score=1.0`** 才算通过——新增/删除证环检查项不会让本沙箱失效。
通过后自动清理沙箱；失败则保留目录并把路径写进 JSON 汇总（取证）。改过落地器、`version/VERSION.md`
或证环后**必跑**。

## 覆盖的检查（run_checks.py）

| 检查 | 判什么 |
| --- | --- |
| `required_files` | 框架必需文件齐全 |
| `manifest_layers` | 五层声明与 `manifest.json` 一致 |
| `root_layout` | 根目录仅含 manifest 声明的层级（无游离目录） |
| `skill_frontmatter` | 官方 Agent Skills 硬规则：字段白名单 + `name` 为小写 kebab-case 且**等于目录名**（与官方 `skills-ref validate` 等价，防回退） |
| `user_area` | 用户区就绪（目录 / `config.json` / 记忆；角色 = maintainer / user） |
| `paths_external` | 包内零运行态（记忆 / 阈值 / 状态 / 评测记录只存用户区 `.leyao-data/`） |
| `routes_contract` | 路由**硬契约**：挂载存在 · id 唯一 · 分形局部图一致 · 默认资产注册合法（入口文档缺失 → **软提示**，不判失败） |
| `routes_described` | 每个节点都有「何时用」描述（否则 AI 无法路由） |
| `routes_alias` | 别名规范（SKOS 精神）：别名 ≠ 标题 · 跨节点不重复（歧义召回防线） |
| `memory_sections` | 用户区记忆四段齐备（`.leyao-data/data/memory.md`） |
| `processor_sections` | 任务处理层结构契约：五步 flow 五段（输入·动作·出口判据·红旗·引导）齐备 + 「判据分级」在场 |
| `workdir_conventions` | 工作区**四区约定**：flow/2·3·5 与 `shapes.md` 齐备四区名、无旧四区名（`inputs/`·`work/`·`deliverables/`·`archive/`）残留、`processor/templates/过程记录.md` 在场 |
| `meta_sanity` | 阈值层数值合法 |
| `version_sync` | `manifest.json` 与 `SKILL.md` 声明版本一致（发版口径：改一处必改另一处） |
| `versions_shape` | 用户区版本记录结构合法（`local` / `history`≤10 / `baseline`；未生成时计入项数并标注跳过） |
| `routes_render` | `ROUTES.md` 与 `routes.json` 一致（引擎渲染产物，未手工编辑） |
| `default_asset` | 默认资产**呈现面 + 判据面**：`ROUTES.md` 有 ★ 行且含节点 id · 判据 0.5 在场（未注册时合法跳过；**注册合法性归 `routes_contract`**、**卡文件健康归资产 `card.py check`**——不重复判） |
| `base_card_guard` | 基础卡片（名称 `@` 开头，兼容全角 `＠`）**不可删除**：行为级回归——自身拒绝 · 子树含基础卡片的祖先拒绝 · 普通卡可删（守卫唯一实现在 `engine.node_remove`，CLI 与管理台共用） |
| `doc_refs` | 文档里反引号引用的框架路径真实存在（文档 ↔ 文件） |
| `doc_commands` | 文档里的 `python <脚本>` 指向真实脚本（文档 ↔ 代码） |
| `doc_cli_args` | 文档里的**子命令与 `--参数`**真实存在（文档 ↔ CLI 接口；改名 / 删参数后不会静默失真） |
| `distiller_success_lane` | 成功路径蒸馏 lane 在位（成功 ≥ `min_support` → route 候选，正常使用也能学到） |
| `capacity_guard` | 库宽上限 C 守卫在位（Ratchet：上限是非发散必要条件；默认 200、`meta` 可调） |
| `parse:*` | 事实源 / 阈值 / 运行时状态均为合法 JSON（运行时文件尚未生成时**计入项数并标注跳过**，保证覆盖率恒定、不随运行状态缩水） |

## 触发评测（能力侧测量 · 官方方法）

结构自检只能防回退，**测不出"技能会不会被用上"**——那由 `description` 决定。触发评测补上这一半：

- **用例**：`trigger_queries.json` —— 20 条基线（10 应触发 + 10 不应触发，负例以 near-miss 为主；train 12 / validation 8）+ **8 条泛化检验**（`split=fresh`，从未参与调参，对应官方"更严格的泛化检验"）。
- **方法**（Agent Skills 官方）：每条跑 `runs_per_query` 次 → `trigger_rate`；阈值 0.5（应触发须 >、不应触发须 <）；只用 train 的失败指导改动，用 validation 判断是否泛化；5 轮通常收敛。
- **执行**：`python evolution/tests/run_trigger_eval.py --emit-prompt` 打印探测提示词（取当前 `SKILL.md` 的真实 name / description，避免与描述脱节），在任意宿主逐条跑，把结果写成用户区 `data/state/trigger_results.json` 后跑汇总器（**尚无结果文件时退出码 2**，属"未测"而非"失败"）。
- **判定**：`--min-pass 0.9`（官方电子书参照值）**且**任一条用例未通过即判未达标——后者就是官方停止条件「train 全部通过」，比单一通过率更硬。

## 人工清单（发布/交付前逐项过）

1. `python evolution/tests/run_checks.py` —— `ok=true` 且 `score=1.0`
2. `python evolution/tests/run_route_drill.py` —— 路由演练 27 项全过（**改描述 / 改判据 / 增删资产必跑**）
3. `python evolution/tests/run_task_drill.py` —— 任务层演练 19 项全过（**改 `processor/` 判据 / 流程 / 模板必跑**）
4. `python evolution/tests/run_task_set.py` —— 判定准确率 **7/7**（balanced；**改判据 / 改模板 / 新增工作区产物后必跑**）
5. `python evolution/tests/run_update_sandbox.py` —— 更新链路回归全过 + `score=1.0`（**项数不作判据**；**改过落地器 / 版本维护层 / 证环必跑**）
6. `python evolution/tests/run_trigger_eval.py` —— 合计与 validation 通过率均达标（首次：按提示先 `--emit-prompt` 产出试题、回收结果到用户区后再跑）
7. `python library/engine.py` —— 重绘成功、无契约问题
8. `python evolution/grow.py status` —— 正常返回 JSON；交付场景下 traces/rules 应为初始态
9. 官方规范校验：`python -m skills_ref.cli validate .`（需 `pip install skills-ref`）→ 输出 `Valid skill`
10. 目录结构与 `SKILL.md` 层级导航一致；无 `__pycache__` 残留
11. 运行时数据与评测记录只存用户区（与 skill 同级 `.leyao-data/`，可用 `LEYAO_SEED_HOME` 覆盖），不进交付（其中审计日志与墓碑记录永不清理，随框架整体迁移）
12. 发版：`manifest.json` 与 `SKILL.md` 版本同步（`version_sync` 守住）；宿主常驻与更新流程见 `version/VERSION.md`
