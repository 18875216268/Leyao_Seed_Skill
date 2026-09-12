---
name: leyao-seed-core
description: "用这个 skill 处理需要成套流程与资产路由的任务：把任务按「理解→规划→执行→验收→交付」五步推进并实时纠偏，先查总路由地图选已挂载的资产来承担具体业务（没有可用资产就用自带判据自己完成），交付后回写经验、由框架自我进化。触发条件（满足其一即用）：① 任务需要多步骤推进，或需要遵循流程与准则（含留证、验收、交付物落位）；② 任务需要调度或复用组织内既有的资产、方法论、Skill 包（哪怕用户没有点名「用资产」）；③ 维护这套框架本身——增删改资产与路由、同步路由表、查看或处理进化提案与阈值、检查与更新版本。不适用：一步就能完成、且不涉及多步流程与资产调度的单一请求（一次问答或一个单点操作），它们由宿主自身能力或对应的专业技能直接承担，本框架不必介入。本框架零业务：只负责编排、路由与进化，业务口径由已挂载资产承担，资产缺失时照常推进。用户常这样说：帮我做这个任务、用我们现成的资产、查路由地图、管理资产、同步路由表、进化这套框架。使用时先按本文件的〈最高准则〉走标准调用链：不得凭记忆直接调用子资产，已绕过则立即回框架补齐。"
compatibility: "需要 Python 3.10+（仅标准库，无第三方依赖）；资产管理台在本地起 HTTP 服务（默认 127.0.0.1:8765，需要能开本地端口）"
license: "MIT"
metadata:
  version: "0.19.1"
  architecture: "processor + library(routes + assets) + evolution(五环自举) + version(版本维护)"
  author: "Leyao"
  date: "2026-09-13"
---

# leyao-seed-core · 任务处理核心

纯框架，零业务。业务能力全部由 `library/` 挂载的**可选资产**提供——**无资产也能运行**。

## 最高准则（每次任务必读 · 不得绕过）

1. **先读框架再动手**：适用任务先走标准调用链（本文件 → `library/ROUTES.md` → 用户区记忆 → 默认资产卡 → `processor/` 五步）；**不得凭记忆直接调用子资产** ✗；
2. **已绕过 → 立即回补**：若已凭记忆直接用子资产或直接开工 → 回到上面这条链补齐（含判据 0.5 与五步）再继续；
3. **单独使用仅限用户明确要求**：子资产本身可独立使用，但**在框架生态内**（资产位于某包的 `library/assets/` 下）一律先走框架流程；独立安装态（不在 `library/assets/` 下）不受此限；
4. **判据自带、不外包**：五步与实时控制的判据在 `processor/`，资产全部缺失也照常推进；资产内容**原样只读**（要改走提案）；
5. **如实收尾**：交付必留证（路径/命令/来源）、关键数字带口径、`grow.py trace` 的 `--outcome` 如实（禁止 false pass）。

> 何时生效：**每天首次使用**与**每日 14:00** 按 `version/VERSION.md` §零〈1 准则 + 4 行为〉就绪 / 巡检（幂等；引导式自行定位本宿主；写不进去则如实告知）。

## 层级总览

| 层级 | 路径 | 职责 |
| --- | --- | --- |
| 1 主文档 | `SKILL.md`（本文件） | 版本信息 + 基础说明 + 层级导航；不含任何业务与流程细节 |
| 2 任务处理层 | [processor/PROCESSOR.md](processor/PROCESSOR.md) | 处理任务：五步流程（理解→规划→执行→验收→交付）+ **工作区四区约定**（原始材料/任务执行/结果交付/归档）+ 实时控制纠偏 |
| 3 资产管理层 | [library/ROUTES.md](library/ROUTES.md) | 总路由地图（级联；大子树自动分片为 `library/routes/<id>.md` 局部图）+ 资产根 `library/assets/` + 资产管理台（`library/admin/`）+ 引擎（`engine.py`）+ L0 经验沉淀（用户区 `data/memory.md`）；资产内容任意可扩展，框架不依赖 |
| 4 自我进化层 | [evolution/EVOLUTION.md](evolution/EVOLUTION.md) | 五环自举（变择行证藏）：轨迹蒸馏 → 提案守门 → 棘轮落地 → 去糟粕取精华；**轨迹写入即自动沉淀经验**（trace 自动触发 reflect/evolve；库宽上限 C 守卫）；阈值可元进化 |
| 5 版本维护层 | [version/VERSION.md](version/VERSION.md) | **1 准则 + 4 行为**（唯一权威定义）：宿主常驻（**引导式**：自行定位本宿主的记忆 / 每轮规则再写入）· Skill 自主更新 · 基本知识库蒸馏 · 禁止绕过主框架流程；时机 = 每天首次使用（就绪）+ 每日 14:00（巡检）；一切落地经唯一落地器（提案 + apply） |

> 层内文档（`processor/PROCESSOR.md`、`library/ROUTES.md`、`evolution/EVOLUTION.md`、`version/VERSION.md`）由本框架**自行调度**：它们是层的入口说明，不是独立技能入口。宿主若把层内文档单独列出，仍以本文件的调用链为准——绕过它会让五步判据与路由契约失效。

## 开始一个任务（标准调用链）

```text
接到任务
  → 每天首次使用（及每日 14:00 定时任务唤醒）：按 version/VERSION.md〈1 准则 + 4 行为〉执行**就绪 / 巡检**（幂等、引导式自行定位本宿主；在位则静默；异步、失败降级，不阻塞任务）
  → 读 library/ROUTES.md（总路由地图：按节点描述匹配场景，定位可用资产；无资产也照常推进）
      同读用户区记忆 .leyao-data/data/memory.md（L0 经验：命中失效模式先规避、有效做法直接复用）
      同读**默认资产卡**（★ 行指向；卡在用户数据区 `card.md`）：每次任务必读、只做"识别与定位"；
      缺失/过期不阻断（如实标注 + 提示刷新）——任务层判据 0.5；未注册默认资产则跳过
      级联下钻：带「（N 个子节点 → 局部图 library/routes/<id>.md）」的节点 → 先读局部图继续匹配（可任意级联）；
      容器节点（只挂子节点、无挂载）不直接执行，下钻其子节点；叶节点（有挂载 / 入口文档）执行
  → 进入 processor/（按五步流程执行；每步判据自带，见 flow/1~5 与 control.md）
      执行时：命中资产则进其挂载目录读 SKILL.md／README.md 原样调用；无命中按自带判据亲自动手
  → 交付后：`python evolution/grow.py trace --routed "<命中节点 id / 无命中写 none>" …` 追加轨迹（五环自举入口，见 evolution/EVOLUTION.md）
```

## 资产管理层维护（增删改）

**一切增删改经同步工具**，禁止手工编辑 `ROUTES.md`（它由 `routes.json` 生成，手改会被下次重绘覆盖）。日常维护推荐资产管理台，纯 CLI 场景用同步命令：

```text
python library/admin/console.py                    # 资产管理台（新增 / 编辑 / 删除 / 获取复制源）
python library/engine.py                      # 重绘 ROUTES.md + 契约校验（挂载/id/分形/默认资产）+ 入口文档缺失提示 + 报告孤儿
python library/engine.py render               # 同上（显式子命令写法，与不带子命令完全等价）
                                              # 分形路由：子树超阈值（子节点 > 5 或 子树节点 > 20）自动生成/清理局部图 library/routes/<id>.md
python library/engine.py add --id <新id> --type <类型> --title "<标题>" [--parent <父id>] [--mount <挂载>] [--description "<何时用>"]
python library/engine.py remove --id <节点id>
python library/engine.py move --id <节点id> [--parent <父id>]     # 移动节点（省略 --parent 即移到根）
python library/engine.py update --id <节点id> [--title "<新标题>"] [--mount "<新挂载>"] [--description "<何时用>"]
python library/engine.py default --id <节点id>    # 设默认资产（每次任务必读；hook 当前仅 read）
python library/engine.py default --clear          # 取消默认资产
```

类型为**自由文本**：默认登记 `方法论` / `Skill包`，用户新增的类型自动汇入登记表（`known_types`）。
资产根＝`library/assets/`（"主页"）；节点挂载 = 资产目录，路径**自由**（`library/assets/` 下的任意相对路径，且**必须已存在**——`add` 只登记不建目录，管理台新建时会建占位目录）：管理台/引擎登记时的**默认目录名 = 卡片 id**（6 位随机、天然唯一），也可自定义为与该资产 skill `name` 一致的 kebab-case（如 `vendor/bi-cookie/`）——挂载目录名与官方校验的 `name` **互不约束**。资产是**可选内容**：有则按描述路由取用，无则按本框架自带判据亲做；资产根内的子目录只是组织方式，框架不感知、不依赖。

## 红线

- **资产内容一律原样只读**，框架零写入；要改资产内容：维护者实例走自我进化层提案（写入门禁 = 只允许落在 `library/assets/` 内，见 `evolution/actions.py`），使用者让 AI 起管理台显式维护。
- **编排判据自带**：五步流程与实时控制的判据写在 `processor/` 内，**不委托任何资产**；资产全部缺失也不影响任务编排。
- 自我进化层两档权限：用户区记忆（`.leyao-data/data/memory.md`）是**唯一自动写区**（L0）；其余变更一律提案守门——**内容类（资产/路由）仅维护者实例**，使用者改资产/路由走管理台显式维护；禁止静默修改（静默改会绕过审计留痕与回滚窗口）。
- 阈值层（用户区 `.leyao-data/data/meta.json`，默认值见 `evolution/templates/meta.json`）修改 = 元变异，需数据充足 + 用户批准；目标与红线（`evolution/EVOLUTION.md` 红线节）永不参与变异。
- 运行时数据与评测记录只存**用户区**（与 skill 同级的 `.leyao-data/`，可用 `LEYAO_SEED_HOME` 覆盖；首次使用自动初始化）；其中审计日志与墓碑记录永不清理。
- **版本维护**（`version/VERSION.md`）：更新只写包内（用户区永不进入更新作用域）；落地永远走唯一落地器（提案 + apply）——检查可异步，但不静默、不自动。
