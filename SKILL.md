---
name: leyao-seed-core
description: "用这个 skill 处理需要成套流程与资产路由的任务：把任务按「理解→规划→执行→验收→交付」五步推进并实时纠偏，先查总路由地图选已挂载的资产来承担具体业务（没有可用资产就用自带判据自己完成），交付后回写经验、由框架自我进化。触发条件（满足其一即用）：① 任务需要多步骤推进，或需要遵循流程与准则（含留证、验收、交付物落位）；② 任务需要调度或复用组织内既有的资产、方法论、Skill 包（哪怕用户没有点名「用资产」）；③ 维护这套框架本身——增删改资产与路由、同步路由表、查看或处理进化提案与阈值。不适用：一步就能完成、且不涉及多步流程与资产调度的单一请求（一次问答或一个单点操作），它们由宿主自身能力或对应的专业技能直接承担，本框架不必介入。本框架零业务：只负责编排、路由与进化，业务口径由已挂载资产承担，资产缺失时照常推进。用户常这样说：帮我做这个任务、用我们现成的资产、查路由地图、管理资产、同步路由表、进化这套框架。"
compatibility: "需要 Python 3.10+（仅标准库，无第三方依赖）；资产管理台在本地起 HTTP 服务（默认 127.0.0.1:8765，需要能开本地端口）"
metadata:
  version: "0.5.0"
  architecture: "processor + library(routes + assets) + evolution(五环自举)"
  author: "Leyao"
  date: "2026-09-10"
---

# leyao-seed-core · 任务处理核心

纯框架，零业务。业务能力全部由 `library/` 挂载的**可选资产**提供——**无资产也能运行**。

## 层级总览

| 层级 | 路径 | 职责 |
| --- | --- | --- |
| 1 主文档 | `SKILL.md`（本文件） | 版本信息 + 基础说明 + 层级导航；不含任何业务与流程细节 |
| 2 任务处理层 | [processor/PROCESSOR.md](processor/PROCESSOR.md) | 处理任务：五步流程（理解→规划→执行→验收→交付）+ 实时控制纠偏 |
| 3 资产管理层 | [library/ROUTES.md](library/ROUTES.md) | 总路由地图（级联）+ 资产根 `library/assets/` + 资产管理台（`library/admin/`）+ 引擎（`engine.py`）+ L0 经验沉淀（`.memory.md`）；资产内容任意可扩展，框架不依赖 |
| 4 自我进化层 | [evolution/EVOLUTION.md](evolution/EVOLUTION.md) | 五环自举（变择行证藏）：轨迹蒸馏 → 提案守门 → 棘轮落地 → 去糟粕取精华；阈值可元进化 |

> 层内文档（`processor/PROCESSOR.md`、`library/ROUTES.md`、`evolution/EVOLUTION.md`）由本框架**自行调度**：它们是层的入口说明，不是独立技能入口。宿主若把层内文档单独列出，仍以本文件的调用链为准——绕过它会让五步判据与路由契约失效。

## 开始一个任务（标准调用链）

```text
接到任务
  → 读 library/ROUTES.md（总路由地图：按节点描述匹配场景，定位可用资产；无资产也照常推进）
      同读 library/.memory.md（L0 经验：命中失效模式先规避、有效做法直接复用）
  → 进入 processor/（按五步流程执行；每步判据自带，见 flow/1~5 与 control.md）
      执行时：命中资产则进其挂载目录读 SKILL.md／README.md 原样调用；无命中按自带判据亲自动手
  → 交付后：`python evolution/grow.py trace --routed "<命中节点 id / 无命中写 none>" …` 追加轨迹（五环自举入口，见 evolution/EVOLUTION.md）
```

## 资产管理层维护（增删改）

**一切增删改经同步工具**，禁止手工编辑 `ROUTES.md`（它由 `routes.json` 生成，手改会被下次重绘覆盖）。日常维护推荐资产管理台，纯 CLI 场景用同步命令：

```text
python library/admin/console.py                    # 资产管理台（新增/编辑/删除/关联资产）
python library/engine.py                      # 重绘 ROUTES.md + 契约校验（挂载存在/入口文档/id 唯一）+ 报告孤儿
python library/engine.py render               # 同上（显式子命令写法，与不带子命令完全等价）
python library/engine.py add --parent <节点id> --id <新id> --type <类型> --title "<标题>" [--mount <挂载>]
python library/engine.py remove --id <节点id>
python library/engine.py update --id <节点id> [--title "<新标题>"] [--mount "<新挂载>"]
```

类型为**自由文本**：默认登记 `方法论` / `Skill包`，用户新增的类型自动汇入登记表（`known_types`）。
资产根＝`library/assets/`（"主页"）；节点挂载即资产目录：`library/assets/<位置>/<卡片id>/`（目录名即卡片 id，天然唯一）。资产是**可选内容**：有则按描述路由取用，无则按本框架自带判据亲做；资产根内的子目录只是组织方式，框架不感知、不依赖。**新挂载资产的目录名建议用 kebab-case 且与该资产的 skill `name` 一致**（如 `vendor/bi-cookie/`），这样它既能被本框架路由，也能独立通过 Agent Skills 官方校验。

## 红线

- **资产内容一律原样只读**，框架零写入；要改资产内容走自我进化层提案（写入门禁 = 只允许落在 `library/assets/` 内，见 `evolution/actions.py`）。
- **编排判据自带**：五步流程与实时控制的判据写在 `processor/` 内，**不委托任何资产**；资产全部缺失也不影响任务编排。
- 自我进化层两档权限：`library/.memory.md` 是**唯一自动写区**（自我进化层维护，L0）；其余文档与路由变更一律提案守门（用户批准或否决），禁止静默修改——静默改会让变更失去审计留痕与回滚窗口。
- 阈值层（`evolution/meta.json`）修改 = 元变异，需数据充足 + 用户批准；目标与红线（`evolution/EVOLUTION.md` 红线节）永不参与变异。
- 运行时数据（`evolution/state/`）不进交付；其中审计日志与墓碑记录永不清理。
