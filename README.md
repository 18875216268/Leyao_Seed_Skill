# leyao-seed-core · 任务处理核心

**纯框架，零业务**：把任务按「理解 → 规划 → 执行 → 验收 → 交付」五步推进并实时纠偏；先查总路由地图，选**已挂载的资产**承担具体业务，
没有可用资产就用自带判据自己完成；交付后回写经验、由框架自我进化。**无资产也能运行**。

> 本文件是**给人看的门口说明**；权威定义在 `SKILL.md`（层级导航）与各层入口文档（`processor/PROCESSOR.md` · `library/ROUTES.md` · `evolution/EVOLUTION.md` · `version/VERSION.md`）。

## 适合什么 / 不适合什么

- ✅ **适合**：需要多步推进、要留证与验收的任务；需要复用组织既有资产（方法论 / Skill 包）的任务；维护资产与路由本身。
- ✗ **不适合**：一步完成的单点问答或单一操作——交给宿主自身能力或对应的专业技能，本框架不必介入。

## 快速开始（3 步）

```bash
# 1) 放置：整包放到任意可写目录即可（宿主技能区 / 自选目录都行；目录名请保持 leyao-seed-core）
#    支持技能目录的宿主：放到 <宿主 skills 目录>/leyao-seed-core（目录名 = SKILL.md 里的 name）
#    从仓库取整包（任选其一）：
#      git clone --depth 1 https://github.com/18875216268/Leyao_Seed_Skill.git
#      https://codeload.github.com/18875216268/Leyao_Seed_Skill/tar.gz/refs/heads/main

# 2) 自检（离线；首次运行会自动初始化用户区）
python evolution/tests/run_checks.py          # 期望 ok=true · score=1.0

# 3) 资产管理台（可选，日常维护推荐）
python library/admin/console.py               # 浏览器打开 http://127.0.0.1:8765
```

## 三个基本动作

| 你要做的事 | 怎么做 |
| --- | --- |
| 让 agent 用起来 | 直接在任务里说需求（触发条件见 `SKILL.md` 的 `description`）；也可点名「用 leyao-seed-core」 |
| 看 / 改资产与路由 | 管理台 `python library/admin/console.py`；纯 CLI：`python library/engine.py add`（另有 `remove` / `move` / `update` / `default`，口径见 `library/admin/README.md`） |
| 让它越用越聪明 | 交付后追加轨迹 `python evolution/grow.py trace --task "<任务>" --routed "<命中节点id / 无命中写 none>" --outcome success\|partial\|fail`（默认自动沉淀经验；高风险改行走 `propose` → `apply`，见 `evolution/EVOLUTION.md`） |

## 结构（五层）

| 层 | 路径 | 职责 |
| --- | --- | --- |
| 主文档 | `SKILL.md` | 版本信息 + 层级导航 + 标准调用链 |
| 任务处理层 | `processor/` | 五步流程 + 实时控制 + 工作区四区约定 |
| 资产管理层 | `library/` | 总路由 `ROUTES.md`（由 `routes.json` 生成）+ 资产根 `assets/` + 管理台 `admin/` + 引擎 `engine.py` |
| 自我进化层 | `evolution/` | 五环自举（变择行证藏）+ 证环 `evolution/tests/` |
| 版本维护层 | `version/` | 就绪两件：宿主常驻 + 版本检测（**引导式**：不同宿主目录/机制不同，由 agent 自行定位本宿主的文件，找不到则如实告知） |

## 运行期数据（永不写回包内）

- **框架用户区**：`.leyao-data/`（与包同级；可用 `LEYAO_SEED_HOME` 覆盖）——记忆 / 轨迹 / 审计 / 提案 / 版本记录；
- **资产私有数据**：被框架挂载时落 `.leyao-data/data/assets/<卡片id>/`；独立部署时落 `~/.leyao-kb/`（可用 `LEYAO_KB_HOME` 覆盖）；
- 首次使用自动初始化；其中**审计与墓碑永不清理**（红线）。

## 就绪机制（用户首次使用 + 每日 14:00）

三件事：① 蒸馏业务速查卡（规范见 `library/assets/pp32an/references/card.md`）② 宿主常驻 ③ 版本检测；
后两件与宿主机制相关，规范见 `version/VERSION.md`（含失败降级：如实告知、不假装）。

## 版本与更新

- **当前版本**以 `manifest.json` 为准（与 `SKILL.md` 的 `metadata.version` 同步：改一处必改另一处）；
- **权威源仓库**：<https://github.com/18875216268/Leyao_Seed_Skill>（仓库根 = 本包根）——更新检测、整包取用与落地守门见 `version/VERSION.md`。

## 许可

MIT（见 `LICENSE`）。

## 目录一览

```text
leyao-seed-core/
├── SKILL.md            主文档（版本 + 层级导航 + 标准调用链 + 维护命令）
├── manifest.json       版本与层级清单（权威来源）
├── README.md           本文件（门口说明）
├── LICENSE             MIT
├── processor/          任务处理层（flow/ 五步 · control.md · shapes.md · templates/）
├── library/            资产管理层（ROUTES.md · routes.json · engine.py · admin/ · assets/）
├── evolution/          自我进化层（五环 + tests/ 证环 + templates/）
└── version/            版本维护层（VERSION.md：就绪两件）
```
