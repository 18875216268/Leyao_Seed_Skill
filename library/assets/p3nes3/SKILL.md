---
name: Pms_智能取数_login_v1.8.0
description: "Use this skill when 用户要登录乐药 PMS、用自然语言查询或导出 PMS 业务数据——无需写 SQL。覆盖「企微扫码登录 → 按子 skill 路由读原样包取数 → 导出 Excel」全链路。触发词：PMS、乐药、取数、问数、智能问数、查询、分析、导出 Excel。"
compatibility: "需要 Python 3.10+ 与 requests（scripts/requirements.txt）；扫码窗口可选依赖 PyQt5；需访问 pms.ysbang.cn、pms.leyopharm.com、auth.leyopharm.com、login.work.weixin.qq.com"
metadata:
  mode: "llm"
  scope: "*"
  version: "1.8.0"
  triggers: "乐药,PMS,取数,促销毛利,智能问数,pms"
  priority: "50"
  vendor_slot: "vendor/leyo-sys（集团基础）+ vendor/optimizers/*（优化板，详见 vendor/SUBSKILL_ROUTING.md）"
---

# Pms_智能取数_login（父 skill：总指引 + 功能器官 + 子 skill 路由）

## 0. 父子关系与总框架

**本 skill 是父 skill（总指引）**，集团子 skill 包（基础 + 优化）是能力提供方。四条裁决原则：

1. **准则优先级**：任何准则、要求、冲突以本 skill 为准；本 skill 未规定的部分，遵照子 skill 和集团子 skill 包。
2. **登录/凭证**：优先使用本 skill 框架自有登录组件（企微扫码，见 §1.1）；集团包中的登录/鉴权方式仅做备用（见随包说明文档清单）——两者共存，不冲突。**优化 skill 不实现登录，其凭证统一由本 skill 自有登录组件提供，或由用户直接给定**（详见 `vendor/SUBSKILL_ROUTING.md` §3 第 6 条）。
3. **能力提供**：在遵循本 skill 框架指引的前提下，完完全全遵照子 skill 和集团 skill 包的相关文件说明（**包方式不固定，必须动态的获取并更新**），本框架不做任何转述篡改。集团包是动态的：未更新时按已沉淀经验使用，一旦更新即同步更新经验与路由表（见 §2）。
4. **路由引导**：以 `vendor/SUBSKILL_ROUTING.md` 路由文档为主。同场景优先以子 skill（集团包优化 skill）的查询方式为准；若子 skill 无法解决，再回退到集团基础 skill。即：优化 skill 是集团基础 skill 特定板块的优化方案，存在时优先使用；不存在时去集团基础 skill 找基本方案。

```
Pms skill（父：总指引 + 裁决 + 路由）
├── 功能器官  Functional Organs  (scripts/，执行取数业务)
│   ├── 登录器  pms_login.py      自有登录组件：企微扫码 → token + user（主）
│   ├── 同步器  pms_sync.py      原样拉取集团基础包 + 各优化包到 vendor/（零解析）
│   └── 发送器  pms_call.py      通用 HTTP 执行器：AI 直读原样包 → 构造请求 → 发送
└── 集团子 skill 包  vendor/（原样只读，接口理解全部由 AI 直读完成）
    ├── leyo-sys/             集团基础 skill（全量板块参数说明，无引导）
    │                        ⚠ 出厂为空：首次使用需 pms_sync 拉取（见 §1.2）
    ├── optimizers/<板块>/     集团包优化 skill（特定板块优化指引，可多个）
    │                        已接入：Pms_促销毛利v1.08
    └── SUBSKILL_ROUTING.md   子 skill 总路由文档（作用 / 路由表 / 规则）
```

## 1. 功能器官

### 1.1 登录器（pms_login.py —— 自有单文件登录组件，**主**）
- **自有登录组件优先**：企微扫码获取凭证，为本 skill 的主登录路径（见 §0 裁决原则 2）。单文件自包含、配置内置，主机与端点双白名单、禁止重定向、不走系统代理。
- **备用路径**：自有组件不可用（如无界面且无法扫码）时，可按集团子包的鉴权流程走（见随包说明文档清单所列说明），操作细节完全遵照集团包文档——备用不等于转述，本框架不做任何假设。
- 用法：
  - CLI：`python scripts/pms_login.py`（默认弹扫码窗重新登录）/ `--status`（只验证，绝不弹窗）/ `--reuse`（有效则复用，失效才弹窗）/ `--no-ui`（服务器/守护进程）/ `--no-remote`（跳过远端校验）；退出码 0 成功 / 1 业务错误 / 2 未分类错误
  - Python API：`relogin` / `verify_credential` / `get_credential` / `is_authenticated`
- **凭证仓库**：按账号一文件，`%LOCALAPPDATA%\pms-operations-query\accounts\<accountNo>.json`（明文 JSON、原子写、权限 600；`PMS_OPERATIONS_HOME` 可覆盖）。同一账号再扫码 → 更新，换人扫码 → 新增，互不覆盖。
- 登录产出完整凭证（`token` / 可直接使用的 `headers` / `user`：userId·userName·accountNo·角色），登录成功即按扫码人身份入库。
- **调用链 token 来源**：`--token` > 环境变量 `PMS_TOKEN` > 凭证仓库最近登录账号（本地检查，绝不弹窗）。
- PyQt5 为可选依赖：只有弹扫码窗才需要；无界面环境用 `--no-ui`。登录产出 `user.accountNo / userId` 作为取数身份标识。
- 多公司账号：登录不负责收集子公司列表；主接口需要的 `providerId` 由取数时从子 skill 文档中带 lookup 语义的接口消歧后传入（用 `pms_call.py --host-key ... --path ...` 按文档构造，具体 action 名/路径以当前集团包原样文档为准，不在此写死）。

### 1.2 同步器（pms_sync.py）
- **完完全全动态**：源与槽位全部外置于 `sync_config.json`：
  - `base_package`：集团基础 skill（`src_url` + `vendor_dir` = `leyo-sys`）
  - `optimizers`：各优化 skill 列表（`name` + `src_url` + `vendor_dir`）
  - 只知「去配置的链接下载包、落到配置的槽位」，集团换包/换地址/换域名只改配置、免改码。
- 仅判定并拉取（版本门控：本地为空 / 版本变化 / `--force`），整包原样落盘、零解析；`--check` 只报告差异不写入。
- 失联三步降级链：① 自动重试（退避 3 次）→ ② `--src-url <新地址>` 换源（持久化到 `base_package.src_url`）→ ③ 仍失败输出换源指引，框架保持可用（已落地 vendor/ 照常工作）。

### 1.3 发送器（pms_call.py —— 通用 HTTP 执行器）
- **范式**：AI 直接读 `vendor/leyo-sys` 与 `vendor/optimizers` 原样文档，理解接口（host / path / content_type / 必填参数），构造请求，本脚本负责发送并取回响应 / 导出文件。
- 用法：
  - 完整 URL：`python scripts/pms_call.py --url <https://.../api/...> --payload-file payload.json`
  - host 基址 + 路径：`python scripts/pms_call.py --host-key pmsHost --path /api/Index/xxx --payload-file payload.json`（host 基址从 `sync_config.json` 的 `host_endpoints` 解析）
  - 导出落盘：`... --out-file 报表.xlsx`（自动识别文件流 / data.url 两种形态）
  - 响应写入文件：`... --output resp.json`（导出结果用 `--out-file`）
  - token 自动注入（`--token` > `PMS_TOKEN` 环境变量 > 凭证仓库最近登录账号，本地检查绝不弹窗）；body 双发 token
  - 集团接口均为 POST，发送器固定 POST，无 method 选项；`content_type` 默认 `application/json`，亦可 `application/x-www-form-urlencoded`

## 2. 子 skill 路由（必读 vendor/SUBSKILL_ROUTING.md）

- 集团基础 skill 含全量板块参数但无引导，直接通读取数慢；优化 skill 针对特定板块提供精简指引。
- **完整路由规则见 `vendor/SUBSKILL_ROUTING.md` §3（共 6 条）**，核心包括：严格按路由表选取子 skill；同场景优先优化 skill；优化 skill 未涉及 / 无法解决时回退集团基础 skill；不转述、不改写，直接读原样文件；动态更新；以及**凭证来源——优化 skill 不实现登录，凭证由本 skill 自有登录组件提供或用户给定**。
- 当前已接入优化 skill：**促销毛利**（`optimizers/Pms_促销毛利v1.08`，适用场景与回退条件见路由表）。

## 3. 取数流程（AI 主导，非固定脚本链）

1. **登录**：`python scripts/pms_login.py`（弹扫码）或 `--status`（只验证不弹窗）。产出 user（登录人身份）。
2. **路由**：按用户意图查 `vendor/SUBSKILL_ROUTING.md` 路由表，确定用哪个子 skill（优化 / 基础）。
3. **读文档**：读对应子 skill 原样文档（md / 脚本 / payload 模板），理解目标接口的 host / path / content_type / 必填参数。
4. **构造**：AI 组织 payload（token 由发送器自动注入，无需手写）；host 用 `sync_config` 的 `host_endpoints` 键名（`--host-key`）或直接给 `--url`。
5. **发送**：`python scripts/pms_call.py ...`（见 §1.3）；导出加 `--out-file`。
   *若命中的是优化 skill（如促销毛利），则按其随包脚本与文档执行，凭证用 `--token` / `PMS_TOKEN` 交给它（见路由文档 §3 第 6 条）。*
6. **多公司**：`providerId` 由取数时从基础 / 优化文档中带 lookup 语义的接口消歧后传入（`--provider-id`）。

## 4. 常见陷阱与故障处理（Agent 必读）

- **401 / token 失效**：先 `python scripts/pms_login.py` 重新扫码登录（弹窗），不要改代码；弹窗失败可点容器重试。
- **42053 请求过频**（当前已知集团限流码，以随包文档为准）：缩小时间范围、按天拆分明细；`pms_call.py` 已内置退避重试（默认 2 次）。
- **代理报错（PROXY_ERROR）**：登录器本身不走系统代理；`pms_call.py` 报代理错误时同命令加 `--no-proxy`，不要改系统代理设置。
- **TLS 报错（TLS_ERROR）**：仅在受控环境用 `--insecure`；优先修复本地 CA 配置（登录器可用 `PMS_CA_BUNDLE` 指定证书）。
- **多公司账号**：主接口多要求 `providerId`；登录不收集子公司列表，取数前用文档中带 lookup 语义的接口消歧后传入。
- **大报表导出**：超过导出阈值（以随包文档为准）走后台异步，此时响应无下载 URL，提示去任务列表下载，不要干等；同步导出用 `--out-file` 落盘。
- **名称对不上**：先 lookup 同名候选让用户确认，不要擅自选第一条；随包文档标注【强制直传】的筛选项禁止额外 lookup。
- **严禁改 `vendor/` 原样包与功能器官代码**：集团格式零假设，改坏无法回退到原文；接口理解只经 §3 流程（AI 直读）。

## 5. 读取与路由协议（边界）

- 本 skill 对集团格式**零假设**：接口理解完全依赖 AI 直接读 `vendor/leyo-sys` 与 `vendor/optimizers` 原样包，框架不维护任何接口定义。
- 槽位目录、下载源、host 域名映射**全部外置于 `sync_config.json`**——集团换包 / 换地址 / 换域名 / 加优化板，只改配置、免改码。
- **关于「子 skill」边界（避免混淆）：** 本 skill 的「外部能力槽位」是 `vendor/` 下的多子 skill（集团基础 + 各优化），由 `pms_sync.py` 整包原样拉取、**禁止改写**（零假设前提）。其消费方式是「AI 直读 + 路由」。
- 平台加载时，若槽位内自带的 `SKILL.md` 被平台级发现机制一并注册，会产生「Pms 与槽内包并存」的命名重复——本 skill 的 §4「严禁改 vendor/」已覆盖其只读约束。

## 版本变更记录

版本权威来源为本 SKILL.md 版本表（目录名、`name`、`metadata.version` 三处同步）。

| 版本 | 日期 | 要点 |
| --- | --- | --- |
| v1.0.0 – v1.7.3 | 2026-08-28 ~ 09-04 | SEM 自进化框架时代（蒸馏 / 进化 / 观测 / 画像）、登录组件替换为自有企微扫码组件、父子架构确立、去硬编码与去包依赖；**该时代的 SEM 内容已于 v1.8.0 全部移除** |
| v1.8.0 | 2026-09-07 | 减重重构 + 子 skill 路由落地：①移除 SEM 自进化模块；②`pms_call` 改为通用 HTTP 执行器（AI 直读原样包构造请求，不再依赖 `endpoints.json`）；③`vendor` 升级为多子 skill 结构（`leyo-sys` 基础 + `optimizers/<板块>` 优化 + `SUBSKILL_ROUTING.md` 总路由），并接入首个优化 skill（促销毛利包）；④新增父子路由裁决（§0 原则4）；⑤`sync_config` 改为 `base_package` + `optimizers` 多包配置；⑥移除促销毛利包的登录模块与登录文档，凭证统一由本 skill 自有登录组件提供或用户给定；⑦文档与代码全面对齐（清理旧范式术语、重复内容、表述残留与失效描述） |
