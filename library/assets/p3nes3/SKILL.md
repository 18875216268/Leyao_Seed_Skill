---
name: Pms_智能取数_login_v1.8.3
description: "Use this skill when 用户要登录乐药 PMS、用自然语言查询或导出 PMS 业务数据——无需写 SQL。覆盖「企微扫码登录 → 按子 skill 路由读原样包取数 → 导出 Excel」全链路。触发词：PMS、乐药、取数、问数、智能问数、查询、分析、导出 Excel。"
compatibility: "需要 Python 3.10+ 与 requests（scripts/requirements.txt）；扫码窗口可选依赖 PyQt5；需访问 pms.ysbang.cn、pms.leyopharm.com、auth.leyopharm.com、login.work.weixin.qq.com"
metadata:
  mode: "llm"
  scope: "*"
  version: "1.8.3"
  triggers: "乐药,PMS,取数,促销毛利,智能问数,pms"
  priority: "50"
  vendor_slot: "vendor/leyo-sys（集团基础）+ vendor/optimizers/*（优化板，详见 vendor/SUBSKILL_ROUTING.md）"
---

# Pms_智能取数_login（父 skill：总指引 + 功能器官 + 子 skill 路由）

> **框架生态提示**：本资产位于某包的 `library/assets/` 下 → 使用前**先回主框架**：上溯到含 `library/engine.py` 的包根，读其 `SKILL.md` 的〈最高准则〉→ `library/ROUTES.md` → 用户区记忆 / 默认资产卡 → 按 `processor/` 五步执行；**独立安装态**（本目录不在 `library/assets/` 下）不受此限——按本资产独立使用即可。

## 登录（唯一入口 · 变量填充版）

> **与主框架衔接（硬）**：触发条件、入口链（取资产路径 → 判「凭证已在位？」→〈登录流程〉/〈验证流程〉 → 作用<业务域>）、失败兜底（〈问题未解决流程〉＝`processor/control.md`〈卡壳处置〉 · 通用循环 ✓）与通用红线——**一律以主框架 `processor/PROCESSOR.md`〈资产使用准则〉④ 为准**；**本节只填本资产的变量值，不复述流程** ✗。

**变量 1 · 验证动作（只读 · 绝不弹窗）**
python scripts/pms_login.py --status        # 有效 → 复用 ✓；无效 → 变量 3

**变量 2 · 凭证落点（唯一仓库）**
%LOCALAPPDATA%\pms-operations-query\accounts\<accountNo>.json
· 取用：**一律经登录器工具**（出口命令 `stored_token()`（§1.1）/ `--status` 输出）✓；**禁止 AI 自行获取**（自读 / 自解 / 自拼凭证 ✗）；**令牌明文不得回显** ✗
· 产出（一处齐备 ✓）：token + 身份 + 公司口径（providers / provider_id）+ 发货仓清单

**变量 3 · 登录器（存在独立登录器 → 必须调用）**
python scripts/pms_login.py            # 默认：扫码弹窗（登录成功 → 自动落库 ✓）
python scripts/pms_login.py --reuse    # 有效复用、失效才弹（常规首选 ✓；扫码成功同样自动落库 ✓）
python scripts/pms_login.py --no-ui    # 无界面：不弹窗 → 由用户提供凭证（备用路径见 §1.1）
· **登录动作全部由登录器本体完成**（弹窗 / 二维码 / 换证 / 落库）✓；agent **只管等待用户完成登录** → 成功后**经其工具直接取用凭证** ✓；**禁止 AI 自行获取凭证** ✗
· **主取数壳 `pms_call.py` 在凭证缺失 / 失效时自动强制调起本登录器**（同一本体；无界面 `--no-ui` 关闭 ✓）——agent 无需介入；**辅助工具 / 其它情形发现失效 → agent 主动调起本登录器重登**（或按说明引导用户提供新凭证 ✓）

**变量 4 · 业务域适用表（本资产唯一权威 = 框架「作用<业务域>」的取值）**

| 凭证来源 | 本 skill + 优化板块（psol5x 等） | 集团域业务 |
| --- | --- | --- |
| **A 自有凭证（登录器产出 · 默认 ✓）** | ✓ 原生可用（本资产设计） | ✓ 可用，**仅「自实现」路径**：读集团包信息 → 自解接口/参数 → 自写一次性脚本直打；**禁传集团包内工具** ✗；集团包每次更新 → 必须重新解析 ✓ |
| **B 集团 key（集团方式；用户明确要求 / 提供 `ak_` 时）** | ★未验证 · 兜底（**默认不用**；**用户明确要求 → 按其走 ✓**，交付显式标注未验证 ✓；其余启用细则另行约定） | ✓ 原生：按集团包说明使用（可用集团全部工具） |

**变量 5 · 重试上限**：登录 / 重登 ≤3 次（与 `vendor/SUBSKILL_ROUTING.md`〈回退判据〉判据 ③ 同规 ✓；**用尽后按〈问题未解决流程〉：询问用户 → 按决定继续 / 触发回退 ✓**）

**指针（本节不重复 ✗）**
· 回退链 A→桥接→C[`ak_`]→D（含「自实现」细节）→ `vendor/SUBSKILL_ROUTING.md`〈回退判据〉+〈桥接指引〉
· 子包凭证传递（`--token`/`PMS_TOKEN`/`--provider-id`/`--state-file`；子包不读本仓库）→ §0 第 2 条
· 无界面 / 401 重登 / 参数语义 → §1.1（细节以它为准）

**本资产特有红线**
· **两套凭证不得交叉使用** ✗（A / B 各自只服务上表列明适用的域）
· 参数 / 权限 / 限流 / 网络类报错**不触发登录** ✗

> 命令路径：均为**资产根相对路径**（**cwd＝本 SKILL.md 所在目录**）✓。

## 0. 父子关系与总框架

**本 skill 是父 skill（总指引）**，集团子 skill 包（基础 + 优化）是能力提供方。四条裁决原则：

1. **准则优先级**：任何准则、要求、冲突以本 skill 为准；本 skill 未规定的部分，遵照子 skill 和集团子 skill 包。
2. **登录/凭证**：**登录做法、双鉴权与消费范围一律按本文件顶部〈登录（唯一入口）〉执行**（本 skill 是全部取数任务的**唯一登录口** ✓；任何子包都不发起登录 ✗）。**优化 skill 不实现登录，其凭证统一由本 skill 自有登录组件提供，或由用户直接给定**（详见 `vendor/SUBSKILL_ROUTING.md` §3 第 6 条）。
   **登录信息一处齐备、Agent 取全即用（钥匙在 Agent 手里）**：登录产出 = **token + 身份 + 公司口径 + 发货仓清单**（`providers` / 单公司账号自动确定 `provider_id` / `warehouses`，见 §1.1），全部落在**同一处**凭证仓库；Agent 在其有效期内**记住并复用**，调用任何子 skill（**含将来新增的包**）时**直接传参**（`--token`/`PMS_TOKEN`；公司口径 `--provider-id`/`PMS_PROVIDER_ID`；需要文件形态时 `--state-file`）——**无需为每个子包学习或配置凭证来源**。子 skill **不感知凭证来源、不读取本 skill 凭证仓库、不含任何凭证获取约定**；**自带登录模块的子包，其登录仅作备用、默认不启用**。
3. **能力提供**：在遵循本 skill 框架指引的前提下，完完全全遵照子 skill 和集团 skill 包的相关文件说明（**包方式不固定，必须动态的获取并更新**），本框架不做任何转述篡改。集团包是动态的：未更新时按已沉淀经验使用，一旦更新即同步更新经验与路由表（见 §2）。
4. **路由引导**：以 `vendor/SUBSKILL_ROUTING.md` 路由文档为主。同场景优先以子 skill（集团包优化 skill）的查询方式为准；若子 skill 无法解决，再回退到集团基础 skill。即：优化 skill 是集团基础 skill 特定板块的优化方案，存在时优先使用；不存在时去集团基础 skill 找基本方案。

```
Pms skill（父：总指引 + 裁决 + 路由）
├── 功能器官  Functional Organs  (scripts/，执行取数业务)
│   ├── 登录器  pms_login.py      自有登录组件：企微扫码 → token + user（主）
│   ├── 同步器  pms_sync.py      原样拉取集团基础包 + 各优化包到 vendor/（零解析）
│   ├── 发送器  pms_call.py      通用 HTTP 执行器：AI 直读原样包 → 构造请求 → 发送
│   └── 体检器  vendor_lint.py    vendor 接入体检：扫「重复登录」残留（接入新包必跑）
└── 集团子 skill 包  vendor/（原样只读，接口理解全部由 AI 直读完成）
    ├── leyo-sys/             集团基础 skill（全量板块参数说明，无引导）
    │                        ⚠ 出厂**未落盘（该目录不存在）**：首次使用需 pms_sync 拉取（见 §1.2）
    ├── optimizers/<板块>/     集团包优化 skill（特定板块优化指引，可多个）
    │                        已接入：Pms_促销毛利v1.08（实际入口在其下一级 `psol5x/`）
    └── SUBSKILL_ROUTING.md   子 skill 总路由文档（作用 / 路由表 / 规则）
```

## 1. 功能器官

### 1.1 登录器（pms_login.py —— 自有单文件登录组件，**主**）
- **自有登录组件优先**：企微扫码获取凭证，为本 skill 的主登录路径（见 §0 裁决原则 2）。单文件自包含、配置内置，主机与端点双白名单、禁止重定向、不走系统代理。
- **备用路径（集团方式 · `ak_`）**：**适用范围与启用条件一律以顶部〈登录〉变量 4 为准**（集团域 ✓ 原生；本域 ★未验证 · 默认不用 ✗）；与自有组件**二选一、不并用** ✓；**已有有效凭证时不得再发起任何登录** ✗。操作细节完全遵照 `vendor/leyo-sys/` 随包文档——备用不等于转述，本框架不做任何假设。**集团包未同步（`vendor/leyo-sys` 不在位）时先跑 `python scripts/pms_sync.py`，否则按 `processor/control.md`〈卡壳处置〉** ✗；**该路径精确前置（`leyo-sys` 落盘 + `ak_`）见 `vendor/SUBSKILL_ROUTING.md`〈回退判据〉「C 跳前置」**
- 用法：
  - CLI：`python scripts/pms_login.py`（默认=**总是重扫** ✗；请优先 `--status` 只验证 / `--reuse` 有效即复用 ✓）/ `--status`（只验证，绝不弹窗）/ `--reuse`（有效则复用，失效才弹窗）/ `--no-ui`（服务器/守护进程）/ `--no-remote`（跳过远端校验）；**扫码成功均自动落库 + 补齐公司/仓口径** ✓；退出码 0 成功 / 1 业务错误 / 2 未分类错误
  - Python API：`relogin` / `verify_credential` / `get_credential` / `is_authenticated`
- **凭证仓库**：按账号一文件，`%LOCALAPPDATA%\pms-operations-query\accounts\<accountNo>.json`（明文 JSON、原子写、权限 600；`PMS_OPERATIONS_HOME` 可覆盖；**非 Windows** 走 `XDG_DATA_HOME`/`~/.local/share`）。同一账号再扫码 → 更新，换人扫码 → 新增，互不覆盖。
- **取用出口（供 Agent 传参用 · 一律经登录器工具 ✓）**：`stored_token()`（本地检查、绝不弹窗；供 agent 取用后传子 skill）——
  `python -c "import sys; sys.path.insert(0,'scripts'); import pms_common; print(pms_common.stored_token())"`；
  Agent 取到后直接传给子 skill（`--token`，或 `PMS_TOKEN` 注入一次复用于多次调用）。
- **边界**：凭证仓库**只由本 skill 与其 Agent 经登录器工具取用**（**禁止 AI 自行获取**——自读 / 自解凭证文件 ✗）；子 skill 不读取本仓库、不含凭证获取逻辑（子包保持完全独立）。凭证过期时在本 skill 重新登录一次即可。
- 登录产出完整凭证（`token` / 可直接使用的 `headers` / `user`：userId·userName·accountNo·角色 / **公司口径 `providers`·`provider_id`·`provider_name`** / **发货仓清单 `warehouses`**——登录时一并自动带出，取不到不影响登录），登录成功即按扫码人身份入库。
- **一处取全**：`python scripts/pms_login.py --status`（远端校验 + **老凭证自动补齐公司 + 仓库口径**）→ 输出即 Agent 所需的**全部登录信息**（token / 身份 / 公司口径 / 发货仓清单）；`--status --no-remote` 可跳过远端校验。
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
  - token 自动注入（`--token` > `PMS_TOKEN` > 登录器取用）；**凭证缺失 / 失效自动调起登录器本体**（`--relogin` 强制重扫 / `--no-ui` 不弹窗报错指路 / `--no-remote` 跳过校验）✓；body 双发 token
  - 集团接口均为 POST，发送器固定 POST，无 method 选项；`content_type` 默认 `application/json`，亦可 `application/x-www-form-urlencoded`

### 1.4 体检器（vendor_lint.py —— 接入体检，防重复登录）
- **作用**：扫描 `vendor/` 下每个子包，检出三类「会引发重复登录」的残留——① 自有登录实现（login/scan/qr/鉴权域/二维码依赖）；② 指路父登录的指引（`pms_login` / 扫码 / 企微登录 / 重新登录）；③ 读取父凭证仓库（`pms-operations-query` / `PMS_OPERATIONS_HOME`）。
- **用法**：`python scripts/vendor_lint.py`（`--json` 机器可读）；退出码 `0` 干净 / `1` 有发现。**接入新子包时必跑**（见 `vendor/SUBSKILL_ROUTING.md` §4 接入验收）。

## 2. 子 skill 路由（必读 vendor/SUBSKILL_ROUTING.md）

- 集团基础 skill 含全量板块参数但无引导，直接通读取数慢；优化 skill 针对特定板块提供精简指引。
- **完整路由规则见 `vendor/SUBSKILL_ROUTING.md` §3（共 6 条）**，核心包括：严格按路由表选取子 skill；同场景优先优化 skill；优化 skill 未涉及 / 无法解决时回退集团基础 skill；不转述、不改写，直接读原样文件；动态更新；以及**凭证来源——优化 skill 不实现登录，凭证由本 skill 自有登录组件提供或用户给定**。
- 当前已接入优化 skill：**促销毛利**（`optimizers/Pms_促销毛利v1.08`，适用场景与回退条件见路由表）。（实际入口在其下一级 `psol5x/`）

## 3. 取数流程（AI 主导，非固定脚本链）

1. **登录**：`python scripts/pms_login.py --status`（只验证不弹窗：**凭证有效**时自动补齐公司/仓口径；**失效则返回退出码 1、不会重登** ✗）→ 有效即复用 ✓；需重登时 `python scripts/pms_login.py`（默认重扫）/ `--reuse`（失效才弹）——**扫码成功自动落库 + 补齐公司/仓口径** ✓（Python API `login_and_store()` 同源等价）。产出 user（登录人身份）。
2. **路由**：按用户意图查 `vendor/SUBSKILL_ROUTING.md` 路由表，确定用哪个子 skill（优化 / 基础）。
3. **读文档**：读对应子 skill 原样文档（md / 脚本 / payload 模板），理解目标接口的 host / path / content_type / 必填参数。
4. **构造**：AI 组织 payload（token 由发送器自动注入，无需手写）；host 用 `sync_config` 的 `host_endpoints` 键名（`--host-key`）或直接给 `--url`。
5. **发送**：`python scripts/pms_call.py ...`（见 §1.3）；导出加 `--out-file`。
   *若命中的是优化 skill（如促销毛利），则按其随包脚本与文档执行，凭证用 `--token` / `PMS_TOKEN` 交给它（见路由文档 §3 第 6 条）。*
6. **公司 / 仓口径**：`providerId` 已随登录带出（**单公司账号自动确定**，见 §1.1），Agent 直接传给子 skill（`--provider-id` / `PMS_PROVIDER_ID`）；发货仓清单同样随登录落盘，**默认 = 全部发货仓**（需要收敛时用 `--warehouse-id`）；多公司账号按随包文档中带 lookup 语义的接口消歧后传入。

## 4. 常见陷阱与故障处理（Agent 必读）

- **401 / token 失效**：`python scripts/pms_login.py --reuse` 重登（有效即复用；失效自动弹窗 → **扫码成功自动落库** ✓）→ 重试本次查询 ✓；不要改代码；弹窗失败可点容器重试。
- **42053 请求过频**（当前已知集团限流码，以随包文档为准）：缩小时间范围、按天拆分明细；`pms_call.py` 已内置退避重试（默认 2 次）。
- **代理报错（PROXY_ERROR）**：登录器本身不走系统代理；`pms_call.py` 报代理错误时同命令加 `--no-proxy`，不要改系统代理设置。
- **TLS 报错（TLS_ERROR）**：仅在受控环境用 `--insecure`；优先修复本地 CA 配置（登录器可用 `PMS_CA_BUNDLE` 指定证书）。
- **多公司账号**：口径与动作见 §3 第 6 条（`providerId` 随登录带出；子公司列表登录不收集 ✗）。
- **大报表导出**：超过导出阈值（以随包文档为准）走后台异步，此时响应无下载 URL，提示去任务列表下载，不要干等；同步导出用 `--out-file` 落盘。
- **名称对不上**：先 lookup 同名候选让用户确认，不要擅自选第一条；随包文档标注【强制直传】的筛选项禁止额外 lookup。（**本包未标注时**：按包内单值筛选项规则执行 ✓）

> **交付三硬规则**（① 来源泳道标注（含 Excel 落点）② 多板块交叉分栏 ③ 预估口径 + 取数时点）：见 `vendor/SUBSKILL_ROUTING.md`〈交付硬规则〉✓。

- **严禁改 `vendor/` 原样包与功能器官代码**：集团格式零假设，改坏无法回退到原文；接口理解只经 §3 流程（AI 直读）。

## 5. 读取与路由协议（边界）

- 本 skill 对集团格式**零假设**：接口理解完全依赖 AI 直接读 `vendor/leyo-sys` 与 `vendor/optimizers` 原样包，框架不维护任何接口定义。
- 槽位目录、下载源与**发送器**的 host 域名映射外置于 `sync_config.json`——换包 / 换地址 / 加优化板只改配置 ✓；⚠️ **但登录器主机与子包端点仍是代码内常量**（`scripts/pms_login.py`、子包 `pms_common.py`）→ 集团**换域名时这两处需改码**。
- **关于「子 skill」边界（避免混淆）：** 本 skill 的「外部能力槽位」是 `vendor/` 下的多子 skill（集团基础 + 各优化），由 `pms_sync.py` 整包原样拉取、**禁止改写**（零假设前提）。其消费方式是「AI 直读 + 路由」。
- 平台加载时，若槽位内自带的 `SKILL.md` 被平台级发现机制一并注册，会产生「Pms 与槽内包并存」的命名重复——本 skill 的 §4「严禁改 vendor/」已覆盖其只读约束；此时**仍以本 skill 为唯一入口与唯一登录口**：即便直接调用槽内包，凭证也由本 skill 出具并经 Agent 传入（槽内包不登录）。

## 版本变更记录

版本权威来源为本 SKILL.md 版本表（`name`、`metadata.version` 两处同步；槽位目录名由宿主决定、不含版本）。

| 版本 | 日期 | 要点 |
| --- | --- | --- |
| v1.0.0 – v1.7.3 | 2026-08-28 ~ 09-04 | SEM 自进化框架时代（蒸馏 / 进化 / 观测 / 画像）、登录组件替换为自有企微扫码组件、父子架构确立、去硬编码与去包依赖；**该时代的 SEM 内容已于 v1.8.0 全部移除** |
| v1.8.0 | 2026-09-07 | 减重重构 + 子 skill 路由落地：①移除 SEM 自进化模块；②`pms_call` 改为通用 HTTP 执行器（AI 直读原样包构造请求，不再依赖 `endpoints.json`）；③`vendor` 升级为多子 skill 结构（`leyo-sys` 基础 + `optimizers/<板块>` 优化 + `SUBSKILL_ROUTING.md` 总路由），并接入首个优化 skill（促销毛利包）；④新增父子路由裁决（§0 原则4）；⑤`sync_config` 改为 `base_package` + `optimizers` 多包配置；⑥移除促销毛利包的登录模块与登录文档，凭证统一由本 skill 自有登录组件提供或用户给定；⑦文档与代码全面对齐（清理旧范式术语、重复内容、表述残留与失效描述） |
| v1.8.1 | 2026-09-12 | 凭证模型澄清 + 残留清理（防重复登录）：①明确**唯一登录口**（集团包鉴权仅备用、与自有组件二选一、不并用；已有有效凭证时不得再发起登录）；②确立「**凭证由 Agent 取用并直接传入子 skill**，子包不感知凭证来源、不读取父凭证仓库」；③促销毛利包清理 5 处「按父 skill 登录」残留误导（改为"由调用方传入"）；④修复 `providerId` 文档↔代码键名不一致（兼容驼峰/下划线）；⑤新增 `scripts/vendor_lint.py` 接入体检 + 路由 §4 接入验收步骤；⑥版本同步口径修正为「`name` + `metadata.version` 两处同步」 |
| v1.8.2 | 2026-09-12 | **登录信息一处齐备（公司口径并入登录产出）**：①登录模块补回公司列表调用（`getSubProviderList/v2050`，**同一官方主机、白名单内，未放宽任何安全边界**），登录即带出 `providers` / `provider_id` / `provider_name`（单公司账号自动确定；多公司列候选）；②**老凭证自愈**：`pms_login.py --status` 一次补齐公司口径并写回**同一处**凭证仓库；③由此 Agent **一处取全**（token + 身份 + 公司口径），使用任何子 skill（**含将来新增的包**）直接传参，**无需为子包学习或配置凭证来源**；④明确「子包自带登录模块 → 仅作备用、默认不启用」 |
| v1.8.3 | 2026-09-12 | **登录信息补齐最后一块（发货仓清单）**：①接入集团数据中心官方端点 `providerWarehouseOption/pv9210`（主机 `pms.leyopharm.com` 加入**官方主机白名单**，未放行任何第三方）；②`collect_login_scope()` 一次取「公司列表 + 发货仓清单」，任一失败只损失该部分、**不阻断登录**；③`warehouses` 随登录落盘于**同一处**凭证仓库，**不自动收窄**仓口径（默认 = 全部发货仓，需要收敛用 `--warehouse-id`）；④`--status` 自愈覆盖两部分口径。**至此登录模块产出 = 全部登录信息（token + 身份 + 公司 + 仓库）** |
