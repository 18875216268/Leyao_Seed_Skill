---
name: Bi_智能取数_login_v1.02
description: "Use this skill when 用户要登录观远 BI、用自然语言查询或导出 BI 业务数据——无需写 SQL。覆盖「企微扫码登录 → 按子 skill 路由读原样包取数 → 导出/解读响应」全链路。触发词：BI、观远、取数、问数、智能问数、查询、分析、导出 Excel。"
compatibility: "需要 Python 3.10+ 与 requests（scripts/requirements.txt）；扫码窗口可选依赖 PyQt5；需访问 bi.leyopharm.com 与 login.work.weixin.qq.com"
metadata:
  mode: "llm"
  scope: "*"
  version: "1.02"
  triggers: "观远,BI,取数,问数,查询,分析,导出,自助取数"
  priority: "50"
  vendor_slot: "vendor/ 各通道（bi-cookie / bi-pat）+ vendor/optimizers/*（板块优化板），详见 vendor/SUBSKILL_ROUTING.md"
---

# Bi_智能取数_login（父 skill：总指引 + 功能器官 + 子 skill 路由）

## 0. 父子关系与总框架

**本 skill 是父 skill（总指引）**，集团子 skill 包（基础 + 优化）是能力提供方。四条裁决原则：

1. **准则优先级**：任何准则、要求、冲突以本 skill 为准；本 skill 未规定的部分，遵照子 skill 和集团子 skill 包。
2. **登录/凭证**：优先使用本 skill 框架自有登录组件（企微扫码，见 §1.1）；集团包中的登录/鉴权方式仅做备用——两者共存，不冲突。**优化 skill 不实现登录，其凭证统一由本 skill 自有登录组件提供，或由用户直接给定**（详见 `vendor/SUBSKILL_ROUTING.md` §6 第 5 条）。
3. **能力提供**：在遵循本 skill 框架指引的前提下，完完全全遵照子 skill 和集团 skill 包的相关文件说明，本框架不做任何转述篡改。通道与优化包（`vendor/`）随 skill **内置交付、原样只读**，由 AI 直读执行（无同步器、无动态拉取）。**本框架不内置、不解释任何 API 参数**——接口知识全部随各自通道/优化板文档交付，本文件仅做引导。
4. **路由引导**：以 `vendor/SUBSKILL_ROUTING.md` 为唯一总路由，次序为——**先通道优先，再板块优先；都不行，先通道降级，再板块降级**。即：默认主通道 bi-cookie；通道内优先所属优化板块，无或不满足再按通道本身指引；主通道无法满足时自动降级到备用通道并重复上述次序。**无法确认通道及优化板块时，给出选项由用户决定，不擅自代选。**

```
Bi skill（父：纯登录框架 + 裁决 + 路由引导）
├── 功能器官  scripts/（纯登录框架，不含任何 API 业务知识）
│   ├── 登录器  bi_login.py     CLI 薄封装（企微扫码 → token + headers + user）
│   ├── 登录本体 login_bi.py    登录逻辑单文件（被 bi_login.py 原样复用）
│   └── 公共底座 bi_common.py    错误/stdio/凭证读取公共层
└── vendor/（通道与子 skill，原样只读，接口知识全部随包自带）
    ├── bi-cookie/           通道1：Cookie 卡片通道（SKILL.md + references 契约 + scripts 三工具 + data 索引）
    ├── bi-pat/              通道2：PAT SQL 通道（自包含：凭证窗口 + 手册 + 四道闸 + pat_call.py）
    ├── optimizers/          板块优化文件夹（每板自包含并标注所属通道）
    │                        已接入：BI-出库统计Ultra查询v1.08（Cookie卡片通道 · 出库统计Ultra 板块）
    └── SUBSKILL_ROUTING.md   统一总路由（§1 通道准则；§2 板块准则；§3 决策树；§4/§5 路由表；§6 规则）
```

> 本 skill **不实现**任何自进化 / 经验画像 / 自动蒸馏 / 跨会话推荐模块：取数经验不落地为可自我修改的代码，集团包与优化包一律原样只读、零解析。这与 `Pms_智能取数_login` 的「无 SEM 自进化」原则一致（不绑定具体版本，避免随包升级而过时）。

## 1. 功能器官

### 1.1 登录器（bi_login.py —— 自有登录组件，**主**）
- **自有登录组件优先**：企微扫码获取凭证，为本 skill 的主登录路径（见 §0 裁决原则 2）。`scripts/bi_login.py` 是薄封装，登录逻辑 100% 原样复用同目录 `scripts/login_bi.py`（单文件自包含、配置内置，主机与端点白名单、禁止重定向、不走系统代理）。
- **备用路径**：自有组件不可用（如无界面且无法扫码）时，可按集团子包的鉴权流程走（见随包说明文档清单所列说明），操作细节完全遵照集团包文档——备用不等于转述，本框架不做任何假设。
- 用法：
  - CLI：`python scripts/bi_login.py`（默认弹扫码窗重新登录）/ `--status`（只验证，绝不弹窗；默认只输出元信息，**不含令牌明文**，`--show-token` 才输出完整凭证）/ `--reuse`（有效则复用，失效才弹窗）/ `--no-ui`（服务器/守护进程）/ `--no-remote`（跳过远端校验）；退出码 0 成功 / 1 业务错误 / 2 未分类错误
  - Python API：`relogin` / `verify_credential` / `get_credential` / `is_authenticated`
- **凭证仓库**：按账号一文件，落在登录器仓库（明文 JSON、原子写）。同一账号再扫码 → 更新，换人扫码 → 新增，互不覆盖。
- 登录产出完整凭证（`token` / 可直接使用的 `headers` / `user`），登录成功即按扫码人身份入库。
- **调用链 token 来源**：`--token` > 环境变量 `BI_TOKEN` > 凭证仓库最近登录账号（本地检查，绝不弹窗）。
- PyQt5 为可选依赖：只有弹扫码窗才需要；无界面环境用 `--no-ui`。

### 1.2 通道工具（已随通道下沉 `vendor/bi-cookie/scripts/`）

Cookie 卡片通道的三工具——发送器 `bi_call.py`、卡片索引 `bi_index.py`、导出器 `bi_export.py`——
**属于通道1**，用法与 API 契约全部见 `vendor/bi-cookie/SKILL.md`。
本框架不内置任何 API 参数说明，仅提供登录器与路由引导。

## 2. 子 skill 路由（必读 vendor/SUBSKILL_ROUTING.md）

- 集团基础 skill 含全量卡片/字段但无引导，直接通读取数慢；优化 skill 针对特定板块提供精简指引。
- **完整路由规则见 `vendor/SUBSKILL_ROUTING.md`**（§1 通道准则 / §2 板块准则 / §3 决策树 / §6 强制规则），核心次序：**先通道优先，再板块优先；都不行，先通道降级，再板块降级**；不转述、不改写，直接读原样文件；**凭证来源——子 skill 一律不实现登录，凭证由本 skill 登录组件提供或用户直接给定**。
- 当前已接入优化 skill：**BI-出库统计Ultra查询v1.08**（出库统计Ultra 板块：DSL 聚合查询/批量并发/分页/区域树/聚合导出；自包含，凭证三级窗口。详见 `vendor/optimizers/BI-出库统计Ultra查询v1.08/SKILL.md` 与 `vendor/SUBSKILL_ROUTING.md` §5）。
- 通道现状：**通道1 Cookie 卡片通道**（任何登录用户，主通道）；**通道2 PAT SQL 通道** `vendor/bi-pat/`（仅持 PAT 的特定权限用户，数据集级 SQL 自由聚合）。通道选择与权限分流见 `vendor/SUBSKILL_ROUTING.md` §1/§3。

## 3. 取数流程（AI 主导，非固定脚本链）

1. **登录**：`python scripts/bi_login.py`（弹扫码）或 `--status`（只验证不弹窗）。产出 user（登录人身份）。
2. **路由**：读 `vendor/SUBSKILL_ROUTING.md` §3 决策树（先通道后板块），确定通道与板块优化板。
3. **读通道/优化板文档并执行**：接口知识、工具用法、参数构造全部以所选包文档为准——
   通道1 入口 `vendor/bi-cookie/SKILL.md`，通道2 入口 `vendor/bi-pat/SKILL.md`，
   板块优化板入口 `vendor/optimizers/<板块>/SKILL.md`。
4. **故障分流**：登录类（401/1017）见 §4；取数类见所选通道文档的错误速查表。

## 4. 常见陷阱与故障处理（Agent 必读，仅框架级）

- **401 / token 失效**：先 `python scripts/bi_login.py` 重新扫码登录（弹窗），不要改代码；弹窗失败可点容器重试。
- **1017 单点登录被顶**：重新企微扫码即可。
- **代理报错（PROXY_ERROR）**：登录器本身不走系统代理；通道工具报代理错误时同命令加 `--no-proxy`，不要改系统代理设置。
- **TLS 报错（TLS_ERROR）**：仅在受控环境用 `--insecure`；优先修复本地 CA 配置。
- **取数类故障（40002/14001/5001/数据口径）**：以所选通道/优化板文档的错误速查表为准（通道1 见 `vendor/bi-cookie/references/api查询文档.md` §1）。
- **严禁改 `vendor/` 原样包与功能器官代码**：集团格式零假设，改坏无法回退到原文；接口理解只经 §3 流程（AI 直读）。

## 5. 读取与路由协议（边界）

- 本 skill 对集团格式**零假设**：接口理解完全依赖 AI 直接读 `vendor/` 各通道与优化板原样文档，框架不维护任何接口定义、不生成也不依赖任何契约文件。
- host 域名映射**外置于 `sync_config.json`**（`host_endpoints.biHost`）——换域名只改配置、免改码。
- **关于「子 skill」边界（避免混淆）：** 本 skill 的「外部能力槽位」是 `vendor/` 下的多子 skill（取数文档包 + 各优化），随 skill **内置交付、禁止改写**（零假设前提）。其消费方式是「AI 直读 + 路由」。
- 平台加载时，若槽位内自带的 `SKILL.md` 被平台级发现机制一并注册，会产生「Bi 与槽内包并存」的命名重复——本 skill 的 §4「严禁改 vendor/」已覆盖其只读约束。

## 版本变更记录

版本权威来源为本 SKILL.md 版本表（`name`、`metadata.version` **两处同步**；挂载目录名由宿主分配、不含版本）。

| 版本 | 日期 | 要点 |
| --- | --- | --- |
| v1.0.0 – v1.0.0 | 2026-09-01 ~ 09-04 | 登录与文档驱动通用壳时代：企微扫码登录、bi-docs 内置、bi_call 文档导航壳、bi_contracts 契约生成 |
| v1.02 | 2026-09-09 | ①**修复登录链**：企微扫码改用 `wwlogin` 新链（`login.work.weixin.qq.com`）；换证改为「带 code/state 访问 BI 回调首页 → 服务器 Set-Cookie 下发凭证」，不再调无权限的 `/api/user/token`；取身份改用 `GET /api/user/profile`；修复重新扫码未落库缺陷。②文档包 `bi-docs` 重组为 `vendor/bi_api`（总文档+api查询文档，混合格式）。③移除 `bi_sync`/`bi_contracts`/`endpoints.json`，`bi_call` 改为通用执行器；`sync_config` 精简为 `host_endpoints`。④复测未成功接口：软失败卡 2/3 已可成功取数（月份过滤/日期区间）。⑤新增**卡片索引** `bi_index.py`（每卡可传参数离线缓存，TTL+读时补新，实测 39 页/558 卡）。⑥新增**导出器** `bi_export.py`（卡片数据导出 xlsx，三步异步链实测通过）。⑦**接入首个板块优化板** `vendor/optimizers/BI-出库统计Ultra查询v1.07`：剥离其自带登录栈（auth/browser/DPAPI 凭证仓），凭证统一改由父 skill 登录器注入；树筛选键名修正 `fieldSeq→fields+values`（实测修复 5001）；formula 退出一致性校验；三类查询（汇总/省份Top/区域树）实测通过，已登记路由表。⑧**优化板自包含化**：`credentials.py` 改三级凭证窗口（环境变量 `BI_UID_TOKEN`/`BI_UID_TOKEN_SIG` → 凭证文件 `credential.local.json`/`BI_CREDENTIAL_FILE` → 父登录仓可选回退），子 skill 可脱离父 skill 移植；自包含 API 契约落地 `vendor/optimizers/BI-出库统计Ultra查询v1.07/references/api查询文档.md`；catalog 全量对齐运行时（37 筛选/50 维/33 指标）并实测新增字段。⑨**路由统一 + 新通道**：`SUBSKILL_ROUTING.md` 重写为「通道层+板块层」统一总路由（§0 决策树先通道后板块；板块路由表新增**所属通道**字段）；新增通道2 `vendor/bi-pat/`（PAT SQL 通道：gdpat_ 令牌凭证窗口、guancli 契约、四道闸纪律、REST 首轮探测——`GET /api/user/profile` 已探明支持 PAT 鉴权；仅限领取 PAT 的特定权限用户）。⑩**纯登录框架化**：通道 API 文档与工具下沉通道文件夹——`vendor/bi_api/` + 根 `scripts/` 三工具（bi_call/bi_index/bi_export + card_index.json）重组为自包含的 `vendor/bi-cookie/`（SKILL.md + references + scripts + data）；父 `SKILL.md` 删光 API 参数细节，瘦身为「登录器 + 裁决 + 路由引导」；全量回归通过。⑪**路由准则定稿**：SUBSKILL_ROUTING 重写为「通道准则（bi-cookie 默认主通道，凭证两级：框架登录器/用户直接给 token；其他通道一律备用——用户显式要求或主通道不满足时自动降级，降级必须说明原因）+ 板块准则（通道内优先优化板块，再板块降级）+ 决策树（先通道优先再板块优先，都不行先通道降级再板块降级）」；通道路由表增加优先级与触发条件列（bi-pat：用户提供 gdpat_ 秘钥或主动要求时使用）；新增「歧义交用户」准则（无法确认通道/板块时给出选项由用户决定，不擅自代选）。⑫**优化板 v1.07→v1.08**：凭证回退对齐纯登录框架（`scripts/login_bi.py`）；参数文档计数修正（50 维/33 指标，清理"51 维"旧述）；死代码清理（transport 空 POST 白名单、catalog 死字段 `snapshotOnlyDimensions`）；优化板 SKILL.md 增加版本记录 |
| v1.01 | 2026-09-08 | 架构对齐 Pms_智能取数_login_v1.8.0：①确认无自进化模块（经验画像/自动蒸馏/跨会话推荐，本就不实现）；②`vendor` 升级为多子 skill 结构（`bi-docs` 基础 + `optimizers/<板块>` 优化 + `SUBSKILL_ROUTING.md` 总路由），并预留板块优化槽位；③新增父子路由裁决（§0 原则4）；④`sync_config` 改为 `base_package` + `optimizers` 多包配置（动态集团包槽）；⑤`bi_sync` 支持按槽动态拉包与 `--list-slots`；⑥文档与代码全面对齐（清理旧范式表述残留） |
