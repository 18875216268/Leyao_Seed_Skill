# 证环评分（evolution/tests）

自我进化层「证」环唯一可自动化的一半：结构完整性 + 一致性 + 路由契约 + 触发评测 + 更新链路回归。语义类判定由 AI 在
`grow.py review` 时承担、由用户终审。

## 运行

```text
python evolution/tests/run_checks.py          # 结构 / 一致性自检（一票否决的客观输入）
python evolution/tests/run_trigger_eval.py    # 触发评测汇总（官方方法，见下）
python evolution/tests/run_update_sandbox.py  # 更新链路回归（零联网沙箱，见下）
```

`run_checks.py` 输出 JSON（`ok` / `passed` / `total` / `score` / `checks`），退出码 0/1；是棘轮与回滚的**唯一客观输入**
（`grow.py apply` 用它做一票否决，不过则自动回滚）。

## 更新链路回归（run_update_sandbox.py · 零联网）

把包与用户区整体复制到临时目录、`LEYAO_SEED_HOME` 指向沙箱——**全程不触网、不写真实包与真实用户区**。
场景：干净更新（含本地新增文件）/ 本地偏离与备份（逐字节比对覆盖前原文）/ 证环不过整体回滚 ×2
（坏路由事实源、`version_sync` 失配）/ 契约拒收 ×5 / 门禁与既有链路回归 / 终检，共 57 项断言。
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
| `routes_contract` | 路由**硬契约**：挂载存在 · id 唯一（入口文档缺失 → **软提示**，不判失败） |
| `routes_described` | 每个节点都有「何时用」描述（否则 AI 无法路由） |
| `memory_sections` | 用户区记忆四段齐备（`.leyao-data/data/memory.md`） |
| `processor_sections` | 任务处理层结构契约：五步 flow 五段（输入·动作·出口判据·红旗·引导）齐备 + 「判据分级」在场 |
| `meta_sanity` | 阈值层数值合法 |
| `version_sync` | `manifest.json` 与 `SKILL.md` 声明版本一致（发版口径：改一处必改另一处） |
| `versions_shape` | 用户区版本记录结构合法（`local` / `history`≤10 / `baseline`；未生成时计入项数并标注跳过） |
| `routes_render` | `ROUTES.md` 与 `routes.json` 一致（引擎渲染产物，未手工编辑） |
| `doc_refs` | 文档里反引号引用的框架路径真实存在（文档 ↔ 文件） |
| `doc_commands` | 文档里的 `python <脚本>` 指向真实脚本（文档 ↔ 代码） |
| `doc_cli_args` | 文档里的**子命令与 `--参数`**真实存在（文档 ↔ CLI 接口；改名 / 删参数后不会静默失真） |
| `parse:*` | 事实源 / 阈值 / 运行时状态均为合法 JSON（运行时文件尚未生成时**计入项数并标注跳过**，保证覆盖率恒定、不随运行状态缩水） |

## 触发评测（能力侧测量 · 官方方法）

结构自检只能防回退，**测不出"技能会不会被用上"**——那由 `description` 决定。触发评测补上这一半：

- **用例**：`trigger_queries.json` —— 20 条基线（10 应触发 + 10 不应触发，负例以 near-miss 为主；train 12 / validation 8）+ **8 条泛化检验**（`split=fresh`，从未参与调参，对应官方"更严格的泛化检验"）。
- **方法**（Agent Skills 官方）：每条跑 `runs_per_query` 次 → `trigger_rate`；阈值 0.5（应触发须 >、不应触发须 <）；只用 train 的失败指导改动，用 validation 判断是否泛化；5 轮通常收敛。
- **执行**：`python evolution/tests/run_trigger_eval.py --emit-prompt` 打印探测提示词（取当前 `SKILL.md` 的真实 name / description，避免与描述脱节），在任意宿主逐条跑，把结果写成用户区 `data/state/trigger_results.json` 后跑汇总器。
- **判定**：`--min-pass 0.9`（官方电子书参照值）**且**任一条用例未通过即判未达标——后者就是官方停止条件「train 全部通过」，比单一通过率更硬。

## 人工清单（发布/交付前逐项过）

1. `python evolution/tests/run_checks.py` —— `ok=true` 且 `score=1.0`
2. `python evolution/tests/run_update_sandbox.py` —— 更新链路回归 57 项全过（**改过落地器 / 版本维护层 / 证环必跑**）
3. `python evolution/tests/run_trigger_eval.py` —— 合计与 validation 通过率均达标
4. `python library/engine.py` —— 重绘成功、无契约问题
5. `python evolution/grow.py status` —— 正常返回 JSON；交付场景下 traces/rules 应为初始态
6. 官方规范校验：`python -m skills_ref.cli validate .`（需 `pip install skills-ref`）→ 输出 `Valid skill`
7. 目录结构与 `SKILL.md` 层级导航一致；无 `__pycache__` 残留
8. 运行时数据与评测记录只存用户区（与 skill 同级 `.leyao-data/`，可用 `LEYAO_SEED_HOME` 覆盖），不进交付（其中审计日志与墓碑记录永不清理，随框架整体迁移）
9. 发版：`manifest.json` 与 `SKILL.md` 版本同步（`version_sync` 守住）；宿主常驻与更新流程见 `version/VERSION.md`
