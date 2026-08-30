# LeyaoSeedSkill · 完整 API 参考

本文是 `SKILL.md` 的展开层。SKILL.md 只放操作要点，细节、签名、规则码全集放这里，
符合渐进式披露（元数据 → 指令 → 按需资源）。

## 1. Suite 公开 API

构造：`Suite(root=None, allow_native=True)`

| 方法 | 签名 | 语义与注意 |
| --- | --- | --- |
| `version` | `version()` | 返回本地/上游 commit 与是否有更新。纯只读，无任何写操作 |
| `sync` | `sync(force=False)` | 拉取上游更新并热更新路由表。拉取后**重载内存 manifest**（否则后续 `add_skill`/`save` 会用过期副本覆盖刚拉取的配置），再跑 `integrity.compatibility()` 做 registry / manifest / 文件系统三方对账，结果附在返回值的 `compatibility` 键上并留审计（只留痕、不阻断）。远端未配置则安全 no-op |
| `discover` | `discover(source="user_drop")` | 扫描 `skills/` 下未注册子 skill 并幂等登记。逐 skill try/except 隔离，单个失败不阻断其余。返回 `[(skill_id, action, detail)]`，`action ∈ registered / skipped / error`。登记后自动跑 lint（只记录不阻断） |
| `route` | `route(query, strategy="direct", fallback=None, trace_id=None)` | 两段式召回 → 裁决 → 执行。返回 dict 带 `trace_id`；传入 `trace_id` 可把多次调用串成一条链路 |
| `add_skill` | `add_skill(skill_id, source, rel_path=None, overrides=None)` | `source ∈ user_create / user_drop / remote_pull`（见 `evolution/pipeline.py::SOURCES`，传错抛 `ValueError`）。原样放入 → 派生 entry → 刷路由表 |
| `modify_skill` | `modify_skill(skill_id, changes)` | **只生成提案，不落地**。必须经 `approve_proposal` 才生效 |
| `approve_proposal` | `approve_proposal(proposal_id)` | 闭环 提案 → 批准 → 执行。对 `modify_skill_content`：依据当前 SKILL.md 重派生 entry（保留 `manual_overrides`）→ 写回结构化变更 → 复 pin 完整性 → 落盘。其他 action 仅置为 `approved` |
| `reject_proposal` | `reject_proposal(proposal_id)` | 把提案置为 `rejected` 并落盘。这是状态机的另一个终态——只有 approved 的话，没人认领的提案会永远悬在 pending，pending 列表最终变成噪音 |
| `pending_proposals` | `pending_proposals()` | 列出所有 `state == "pending"` 的提案 |
| `remove_skill` | `remove_skill(skill_id)` | 注销（同时移除 integrity entry）并落盘。返回是否确有移除 |
| `learn` | `learn(traces)` | 轨迹蒸馏产出规则 + 用户建模观察 |
| `evolve` | `evolve()` | `growth.apply(growth.evolve())`。每条变异带 `evidence`（`source`/`rule_id`/`support`/`success_rate`/`state`），同一轮共享一个 `trace_id` |

#### 经验规则的两条通道与作用域（易误解，务必分清）

`learn()` 蒸馏出的规则有两类，走**完全不同**的通道：

| kind | 触发方式 | 消费位置 | 作用域 |
| --- | --- | --- | --- |
| `route`（`user_override` 轨迹） | 用户反复把某类 query 改派给某 skill | `rank` 阶段的 `experience_boost` | **只在已召回的候选内加分调序** |
| `avoid`（`success=False` 轨迹） | 某 skill 在某模式下反复失败 | `evolve()` → 写回路由表 `negative_triggers` | 落地为排除词，后续 `recall` 阶段直接过滤 |

**关键边界**：`evolve()` 只消费 `avoid` 与 `library_map.conflicts`，**不消费 `route`**——
`route` 规则不会被写进路由表，只在每次 `route()` 时经 `experience` 参数实时影响排序。

因此 `route` 规则**救不活零召回的 query**：`recall(entries, query)` 不接受经验数据，
若某 query 一个候选都没召回，再强的 `route` 规则也无用武之地（宁可 fallback 也不错召）。

实测（两 skill 的 trigger 分别为 `销售报表` / `库存报表`）：
- 多候选：`rank` 无规则 → `[stock-check, sales-report]`；带 `route` 规则 target=`sales-report` → `[sales-report, stock-check]`（**排序确实被改变**）
- 零召回：query `看一下业绩` 召回为 `[]`；带规则后仍为 `[]`（**救不活**）

**推论**：若某个词在任何子 skill 的 `triggers` / `description` 里都不存在，
那么无论用户手动改派多少次，系统都学不会。正解是把该词补进对应子 skill 的元数据
（走 `modify_skill → approve_proposal`），而不是依赖蒸馏。

规则晋升门槛（`evolution/store.py::PROMOTE_RULES`）：
`candidate → validated` 需 `support ≥ 3` 且 `success_rate ≥ 0.7`；
`validated → locked` 需 `support ≥ 6` 且 `success_rate ≥ 0.85`。
只有 `validated` / `locked`（`CONSUMABLE_STATES`）会被消费，`candidate` 永不进生产路径。
| `evaluate` | `evaluate(name, test_prompts, runner, payload=None)` | 跑 `run_eval` 得分 → 交棘轮 `keep_or_rollback`。返回 verdict 并附 `score` |
| `schedule_background_sync` | `schedule_background_sync()` | 起 daemon 线程查远端版本并条件拉取，不阻塞首用。安装位置由运行时真实仓库状态启发式判定，非硬编码目录名 |
| `save` | `save()` | 显式落盘路由表 |

`allow_native=False` 时匹配到 native skill 直接拒绝，不静默执行未知代码。默认开启是设计取舍：原生 skill 即任意代码执行，只应注册可信 skill。

### 三源注册：`source` 取值

`source` 记录「这个子 skill 是哪条路进来的」，唯一取值集合在 `evolution/pipeline.py::SOURCES`，
`register()` 会校验，传错抛 `ValueError`；CLI 也从同一处取，不会与代码漂移。

| 取值 | 语义 | 谁产生 |
| --- | --- | --- |
| `user_create` | 用户/AI 显式创建并注册（知道自己在建什么） | 调用方显式传 |
| `user_drop` | 目录被原样丢进 `skills/` 后被 `discover()` 扫描登记 | `discover()` 默认 |
| `remote_pull` | 来源为远端拉取 | 调用方显式传 |

**注意**：`remote_pull` 是**对外开放的取值，不是框架自动产生的**。套件自带的 `sync()`
同步的是**套件自身**（`skills/` 随仓库一起更新），它只做 `registry.load()`，不会逐个
重新登记子 skill —— 因此内置同步路径不会产生 `remote_pull`。若你自己从远端取回一个子
skill 再注册，才应显式传它。别指望 `source` 能区分「这个 skill 是随套件同步来的」——
它区分不了，随套件同步来的 skill 在 manifest 里保留的是上游写入时的 source。

## 2. 路由表 entry 契约

必填只有 `id` 与「可召回元数据」；其余由 `core/contract.py::DEFAULTS` 兜底：

```python
DEFAULTS = {
    "domain": [], "version_pin": "0.0.0",
    "enabled": True, "depends": [],
    "negative_triggers": [], "description": "",
}
```

`auth` 与 `health` 已从契约中**删除**：它们写进条目却没有任何消费点，属于死字段。
危害不只是多两个键——读注册表的一方（含 AI）会以为框架在跟踪子 skill 的鉴权要求与
健康度，从而基于不存在的信息做判断。宁可没有这个字段，也不要有字段名却无语义的字段。

注意区分：`health()` 是 native handler 的**契约函数**（`NATIVE_FUNCS`，executor 真实调用），
那是活的；删掉的只是条目上那个永远为 1.0 的 `health` 字段。

`version_pin` 由 `integrity.pin()` 在打 pin 时同步刷新（复 pin 也刷新，否则 manifest 里
留旧版本号，漂移报告反而误导），并由 `verify()` 输出 `version_drift` 诊断——版本漂移只作
诊断不改变 `ok` 结论，因为 version 写在 SKILL.md 里已被 `content_hash` 完整覆盖；它的价值
是让「改了什么」可读（「pms 从 1.0.0 变成 1.2.0」比一串哈希有信息量）。

#### 完整性两个函数的返回结构（易混，务必区分）

同模块的两个函数返回**不同形状**，取错键会拿到 `None` 而不报错，排查时极易误判为「没检测到问题」：

```python
integrity.verify(manifest, root)
# {"ok": bool, "checked": int, "missing": [skill_id], "drift": [...], "version_drift": [...]}
#   missing        —— 目录已不在磁盘上
#   drift          —— [{"skill", "expected"(hash), "actual"(hash)}]  内容被改动
#   version_drift  —— [{"skill", "expected"(版本), "actual"(版本)}]  仅诊断，不改 ok

integrity.compatibility(manifest, entries, root)
# {"ok": bool, "problems": [{"skill", "reason"}]}
#   —— registry / manifest / 文件系统三方对账（条目缺 SKILL.md、native 缺 handler.py、
#      manifest 有而 registry 无）
```

**注意 `verify()` 没有 `problems` 键，`compatibility()` 没有 `drift` 键。**
`verify()` 由 `Suite.sync()` 之外的使用方直接调用（例如想确认子 skill 是否被改动过）；
`compatibility()` 已被 `Suite.sync()` 自动调用，结果附在 sync 返回值的 `compatibility` 键上。

**可召回性门禁**：`triggers` 与 `description` 至少有一个非空，否则 `validate()` 抛错。
依据：Anthropic Agent Skills 规范中 `name` 是标识符不是召回信号，生态里的 skill 绝大多数
只有 `name` + `description`。若强制 `triggers`，标准 skill 召回率为零。

### front-matter 解析

`core/frontmatter.py::parse_frontmatter`。零依赖（不引入 PyYAML），支持：

| 形态 | 示例 |
| --- | --- |
| 标量 / 行内列表 / 行内映射 | `key: v`、`key: [a, b]`、`key: {k: v}` |
| 块列表 | `key:` 换行 `  - a` |
| 嵌套映射（可再含块列表） | `network:` 换行 `  allow: [x]` |

必须支持嵌套的原因：OWASP 权限字段是两级结构（`network.allow`、`permissions.deny_write`）。
扁平解析器会把它们读成空字符串，于是**作者明明声明了，lint 仍报 `undeclared_network`**——
文档承诺的能力实际不可达。

它放在 `core/` 而不在 `evolution/`：它被 core / evolution / deploy 三层共同消费，
归到任何一层都会让其余两层反向依赖那一层。`evolution/distiller.py` 仍 import 它，
那是它的真实使用点，不是兼容别名。

注释处理：整行注释直接跳过，行内尾注释会被剥离，引号内的 `#` 保留（URL fragment）。
不处理注释会让注释行中断解析，其后所有字段静默丢失。

## 3. 召回信号与权重

`core/resolver.py`，`recall()` 做准入、`rank()` 做排序：

| 信号 | 权重 | 说明 |
| --- | --- | --- |
| `triggers` | 2.0 | 作者显式声明，最精准 |
| `name` | 1.5 | 官方规范标识符，可作弱召回信号 |
| `domain` | 1.0 | 次级召回词 |
| `description` | 2.0 × 覆盖度 | 覆盖度 = 命中词数 / 查询词数，**不是命中数**——避免长描述仅因词多占优 |
| 用户历史频次 | ≤0.5 × 相对占比 | `usage_boost()`。上限刻意小于一个 trigger 命中（2.0）：个性化只能在同一档内调序，不能推翻语义更相关的结果。零历史时恒为 0 |

准入门禁：有 `triggers`/`name`/`domain` 任一强信号即准入；只有 `description` 弱信号时需
`MIN_WEAK_HITS = 2` 个词命中，防止单词巧合误召。

分词：中英文混合，零依赖。英文按 `[a-z0-9]+`切词 + 极简复数还原（`spreadsheets` → `spreadsheet`）
+ 停用词过滤；中文按 bigram 切分（`min_len=2`）。

**匹配先归一化**：`_matches()` 先试原文子串，落空再把词项与查询都剥掉空格 / 标点 /
连字符后比对。所以「促销 毛利」「促销，毛利」都能命中触发词「促销毛利」，「P-M-S」能命中
「PMS」。中文输入夹空格、英文带连字符都是常态，纯子串匹配会整片漏召。

两个约束：

- 归一化**只增加**命中、绝不减少——原文命中的 query 改后仍然命中，因此不会改坏既有行为。
- 归一化后短于 `MIN_NORM_LEN = 2` 字符的词项不参与（`C++` 归一化成 `c`，若放行会命中任何
  含 c 的查询）。一次漏召修复换来一片误召，比原来的问题更糟。

`negative_triggers` 走同一套 `_matches()`，否则会出现"写了排除却不生效"。

**description 的 2-gram 按内容缓存**（`_desc_tokens`）。它是 entry 的不变量，但放在 recall
里就会在「每个 query × 每个 entry」上重算一次——实测 N=200 时单次 7.624ms 中有 7.472ms
（约 98%）花在这里。缓存按 description 内容键控，上限 4096 条、满了直接清空（只影响性能
不影响语义）。返回的集合只读取用，调用方不得原地修改。

## 4. 执行策略

`core/executor.py`：`direct` / `cascade` / `pipeline` / `parallel`。

`parallel(picked, query, root=None, allow_native=True, max_workers=5, timeout=30.0, overall_timeout=None)`

- 走 `ThreadPoolExecutor` **真并发**，不是 for 循环。
- 同时受单次 `timeout`（默认 30s）与整体 `overall_timeout` 约束。
- **结果按请求顺序返回**。乱序返回会破坏上下文顺序进而引发幻觉，这是主流厂商的明确要求。
- 已知限制：Python 无法强制杀死线程，超时语义是"不再等待"，线程可能仍在后台跑完。

## 5. lint 规则码全集

`lint_skill(skill_dir, root=None)` → `[{"level", "code", "message"}]`

| code | level | 含义 |
| --- | --- | --- |
| `missing_skill_md` | error | 目录缺 SKILL.md |
| `bad_mode` | error | `mode` 非 `llm`/`native` |
| `secret.openai_key` | error | OpenAI 密钥 |
| `secret.anthropic_key` | error | Anthropic 密钥 |
| `secret.aws_akid` | error | AWS access key id |
| `secret.github_token` | error | GitHub token（`gh[pousr]_`） |
| `secret.slack_token` | error | Slack token（`xox[baprs]-`） |
| `secret.google_api` | error | Google API key（`AIza`） |
| `secret.stripe_live` | error | Stripe live key |
| `secret.private_key` | error | PEM 私钥头 |
| `secret.jwt` | error | 三段式 JWT |
| `secret.assigned` | error | `api_key`/`password`/`token` 等赋值型硬编码 |
| `secret.high_entropy` | warn | 熵值 ≥4.0 且长度 ≥32 的串（可能是 base64 资源） |
| `undeclared_network` | warn | 代码有网络调用但未声明 `network` |
| `network_is_bool` | warn | `network` 写成布尔，无法约束出网范围 |
| `network_allow_all` | warn | `allow` 含 `*`（**不因设了 `deny: "*"` 而豁免**——两者管的是不同层面） |
| `network_tier_mismatch` | warn | `allow` 含 `*` 但 `risk_tier` 低于 L2 |
| `undeclared_shell` | warn | 有 `subprocess`/`os.system` 但未声明 `shell` |
| `shell_tier_mismatch` | warn | `shell=true` 但 `risk_tier` 低于 L2 |
| `undeclared_write` | warn | 有写操作但未声明 `permissions.deny_write` |
| `name_mismatch` | warn | `name` 与目录名不一致 |
| `name_too_long` | warn | `name` > 64 字符 |
| `no_triggers` | warn | 未声明 triggers，仅靠 name/description 召回 |
| `desc_too_long` | warn | `description` > 1024 字符 |
| `desc_short` | warn | `description` < 20 字符 |
| `desc_no_trigger_hint` | info | description 缺触发语义词 |
| `too_long` | warn | SKILL.md > 500 行，违背渐进式披露 |
| `domain_dup_triggers` | warn | `domain` 与 `triggers` 完全重复 |

| `mode` 显式非法值 | `bad_mode` | error | 写成 `llm`/`native` 之外的值 |
| `mode` 未声明 | — | — | **不报错**：由 distiller 自动判定（有 `handler.py` → native）。把它判成 error 会与「mode 可选」的文档直接矛盾 |

`network_allow_all` 为什么是 warn 而不是 error：无限制出网确有合法场景（浏览器 / 抓取类
skill），判死会逼作者删掉声明、退回 `undeclared_network` 的 warn——等于惩罚诚实声明、奖励
隐瞒。真正的对账交给 `network_tier_mismatch`（无限制出网却自称 L0/L1），与 `shell_tier_mismatch`
同构。这是「声明要能被写出来」优先于「声明要被判死」的取舍。

误报抑制：占位符（`example`/`placeholder`/`<your-key>`/`xxx`/`****`）、环境变量引用
（`os.environ`/`getenv`/`process.env`/`${VAR}`/`%VAR%`/`vault`）。

高熵字符集刻意不含 `_` / `-` / `/`。含 `_` 会把 snake_case 标识符整段吞成一个"凭证"
（实测：`test_ip_proxy_falls_back_to_dns_for_unmapped` 44 字符熵 4.03、
`overall_deadline=DEFAULT_OVERALL_DEADLINE` 41 字符熵 4.29），含 `/` 会吞掉 URL 路径。
真实凭证（hex / base64 / 字母数字 key）不会用下划线或连字符做词分隔。代价是 base64url
形态不再被泛型熵检测覆盖——可接受，因为真凭证几乎总会赋值给 `api_key`/`password`/`token`
之类变量，由 `secret.assigned` 无条件检出，与字符集无关。

测试目录（`tests` / `test` / `__tests__` / `spec` / `fixtures` / `testdata`）**整支跳过**，
凭证扫描与能力探测都跳。凭证侧：测试夹具天然含合成凭证，扫它只产出必然被忽略的 error，
而一个总被忽略的 error 通道等于没有 error 通道；能力侧：测试代码里 `import requests`
不能证明 skill 运行时会出网。需要"连测试一起扫"的 CI 级覆盖请用 gitleaks 或 TruffleHog。

扫描范围：扩展名白名单（`.py .js .ts .sh .ps1 .json .yaml .env .md` 等），
跳过 `.git`/`node_modules`/`__pycache__` 等目录，单文件 > 512KB 跳过。

**安全约束**：命中凭证只报文件、行号、规则名，**绝不回显匹配内容**。lint 报告会被 AI 读进
上下文，回显等于把密钥从磁盘送进模型上下文。

## 6. 审计日志

路径 `<root>/state/audit.log`，JSONL，5MB 轮转保留 3 份（`MAX_BYTES` / `KEEP_ROTATED`）。

字段对齐 OTel GenAI Semantic Conventions（2026-06 stable），但不引入 OTel SDK，保持零依赖：
`ts` `trace_id` `span_id` `parent_span_id` `event` `operation.name` `status` `duration_ms` + 业务字段。

```python
from core.audit import record, tail, replay, new_trace, new_span

tid = new_trace()
record("route", root=root, trace_id=tid, status="ok", duration_ms=12.3, query=..., skill=...)
tail(50, root=root)                 # 最近 50 条
tail(50, root=root, trace_id=tid)   # 按 trace 过滤
replay(tid, root=root)              # 还原整条链路，跨轮转文件，按 ts 升序
```

`root` 必须显式传。三个函数都接受 `root=None` 并回落到 `os.getcwd()`，
那会把日志写到**进程启动目录**而不是套件目录——事后再也找不回来，可审计性直接归零。

`replay` 只认 `trace_id`；`tail` 是"还不知道 trace_id 时"的唯一入口。

`record()` 返回 `trace_id`，任一异常只返回 `None`，不影响主流程（可观测不得拖垮业务）。

当前记录的事件类型：

| event | 位置 | 关键字段 |
| --- | --- | --- |
| `route` | `Suite.route` | `query` `strategy` `routed` `skill` `duration_ms` |
| `skill.invoke` | `core/executor.py` | `skill` `mode` `status` `reason` `duration_ms` |
| `discover_lint` | `Suite.discover` | `skill` `issues` |
| `sync_compatibility` | `Suite.sync` | `problems`（registry / manifest / 文件系统三方不一致的条目） |
| `growth.apply` | `evolution/growth.py` | `before` `after` `authority` `evidence` |

**一次完整调用 = `route` + `skill.invoke` 两条事件挂在同一条 trace 上**：前者回答"路由到了谁"，
后者回答"跑得怎么样"。只有前者时，trace 能证明"选对了"却无法证明"跑通了"——而自进化恰恰
依赖成败反馈（ASG-SI：behavioral drift is difficult to audit or reproduce），缺了执行结果，
事后就无法判断某次权重调整是依据真实失败还是凭空猜测。

执行审计的三条边界：

1. **只记成败、耗时与决策依据，绝不记 `result` 内容**——`route` 事件会记 `query` / `strategy` / `routed`：
   少了这些就无法回答"为什么路由到它"，而路由可审计性正是本套件的存在理由（Klarna：point at
   the code and prove）。**被排除的是 `skill.invoke` 一侧的返回体**——skill 的返回可能含业务敏感数据。
2. **异常照记后原样冒泡**——审计在 `finally` 里落盘，不改变 `direct` 策略下异常向上传播的既有行为。
3. **`llm` 模式只记到"已交接"**（`handoff: "llm"`）——该模式下框架不代执行，真正的执行在框架之外，
   此时 `status: ok` 的语义是"SKILL.md 已成功交给 agent"，不是"任务已完成"。

**已知覆盖缺口**：`sync` / `pull` 的网络路径与耗时走 `logging`，未并入审计 trace。
这两者是框架自运维动作、不在用户请求链路内，合并进 trace 反而会污染"这次请求发生了什么"的语义；
排障拉取问题直接看日志即可。

## 7. deploy 配置键

`manifest.json → deploy`：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `remote` | `origin` | 远端名 |
| `branch` | `main` | 分支 |
| `remote_url` | `None` | 远端 URL |
| `token_env` | `None` | 读取 token 的环境变量名（不落盘密钥） |
| `accelerator_url` | `None` | **opt-in**，不配则不内嵌第三方地址 |
| `accelerator_source` | `ziyou` | `all` 并入第三方镜像源 |
| `accelerator_retries` | `20` | 换 IP 尝试上限 |
| `overall_deadline` | `180.0` | 整体时间预算（秒） |
| `pull_timeout` | `120.0` | 单次 git 调用上限（秒） |

`GitRemote` **刻意不提供** `commit` / `push` / `bootstrap`，由测试断言锁死（本套件只消费不发布）。

### pull 的重试语义

必须分清两件事，混为一谈会既慢又无效：

- **failover 换 IP** —— 每次换一个新目标，本身不需要退避（换条路就试）。
- **retry 同一目标** —— 同一目标反复打，必须退避，否则是在锤一个正在故障的服务。

加速器循环是前者，所以 `accelerator_retries` 可以保持较大；真正需要补的是整体时间预算。

治理要点：

1. 单次受 `pull_timeout` 约束（一次 TCP 挂起不能吃掉全部预算）。
2. 整体受 `overall_deadline` 约束；剩余预算低于 `MIN_EFFECTIVE_TIMEOUT`（10s）不再发起新尝试。
3. 剩余预算透传给单次调用，单次永远不超过总预算。
4. 云函数连续 3 次返回空候选即熔断（`CIRCUIT_THRESHOLD`）——这是同一目标重试，不是 failover。
5. 非空候选但失败时用 **full jitter** 退避（延迟在 `[0, min(cap, base × 2^attempt)]` 均匀采样，
   `base=0.5` `cap=8.0`）。固定延迟 ± 抖动仍会形成同步重试波前。
6. 预算仍有余量时回退系统代理/正常 DNS 再试一次。

没有这套治理时，21 次 × 120s ≈ 42 分钟无上限阻塞。

## 8. CLI

```bash
python scripts/cli.py list
python scripts/cli.py discover                     # 扫描 skills/ 下未注册目录并登记（幂等，主路径）
python scripts/cli.py route "查询" --strategy cascade
python scripts/cli.py add <id> --source user_drop  # 按 id 注册单个，不做扫描
python scripts/cli.py remove <id>
python scripts/cli.py proposals          # 列出待授权提案
python scripts/cli.py approve <proposal_id>
python scripts/cli.py reject <proposal_id>
python scripts/cli.py learn traces.json
python scripts/cli.py evolve
python scripts/cli.py version
python scripts/cli.py sync --force
```

**后台同步只挂在只读命令上**（`list` / `route` / `proposals`）；`sync`、`version` 自带同步语义。
**变更类命令不会也不应触发后台拉取**：路由表是整表覆盖写，`sync_before_use` 拉完会执行
`registry.load()` 用磁盘内容替换内存副本，而变更命令把自己这份内存副本整体写回——
两条路径重叠时后写的一方会抹掉对方的落盘结果（刚注册的子 skill 或刚拉到的上游更新二选一丢失）。
子命令的归类由 `scripts/cli.py` 的三个常量显式声明，并与 parser 实际子命令**精确相等**，
由 `test_every_cli_command_is_classified_for_background_sync` 锁死——新增子命令漏归类会直接失败。

`--source` 与 `--strategy` 的取值集合分别从 `evolution.pipeline.SOURCES` 与
`core.router.STRATEGIES` **导入**，不在 CLI 里另抄一份，避免代码已改而 CLI 没跟上。

## 9. 测试

| 文件 | 项数 | 覆盖 |
| --- | --- | --- |
| `tests/test_suite.py` | 35 | 契约 / 路由表 / 两段式召回与归一化 / 裁决 / 四策略执行 / 蒸馏 / 成长 / 权限与提案闭环 / 完整性（含 version 漂移）/ 用户建模加成 / 三方一致性对账 / **经验规则作用域边界（只调序不救活零召回）** / 端到端（含最小 skill 冒烟） |
| `tests/test_deploy_remote.py` | 5 | 部署层只读边界、上游拉取、漂移上报、CLI 后台同步归类完备性 |
| `tests/test_connectivity.py` | 8 | hosts 解析、IP 钉定通道、failover |
| `tests/test_spec_alignment.py` | 35 | 官方规范兼容性、front-matter 解析（嵌套 / 注释）、召回归一化与缓存、临时区回收边界、lint 安全扫描（含相对路径不误报）、套件自检（能力声明可读性 / 危险声明不失效） |
| `tests/test_performance.py` | 4 | 真并发、超时隔离、整体预算、顺序保序 |
| `tests/test_audit_trace.py` | 9 | trace 贯穿、执行成败留痕、不记返回内容、成长留痕、轮转、tail 与 replay |
| `tests/test_pull_deadline.py` | 16 | pull 的 deadline 治理、full jitter、熔断、回退决策 |
| `tests/test_atomic_write.py` | 7 | 原子写：失败不留半截 JSON、不留临时文件 |
| **合计** | **119** | 8 文件全绿为落地门槛 |

每个文件可独立运行（`python tests/xxx.py`），自带 `main()` 汇总，不依赖 pytest。

### 测试临时区 `tests/_harness.py`

6 个测试文件共用同一个临时目录落点，由 `_harness.setup()` 统一提供：

- **落点**：套件**同级**的 `.suite_test_tmp`（不在 git 仓库内）。这样既避开沙箱对系统
  临时区的拦截，也避免 temp 目录被套件自身的 git 上下文污染，导致 `NOT_A_REPO` 断言失效。
- **回收**：`setup()` 顺带清掉 24 小时前、且前缀属于本辅助区（`skill-` / `srs-`）的目录。
  辅助区只增不减——测试正常结束时 `finally` 会清理，但进程被杀 / 崩溃时目录就留下了；
  累积到几百个后宿主环境的批量删除阈值会把测试直接卡死，**验收门槛会变得不可复现**。
- **边界**：回收只认自家前缀、只认超龄目录，且失败不抛异常。这条边界由
  `test_tmp_harness_sweep_only_touches_stale_own_directories` 锁死——回收删的是真实文件，
  误删的代价远高于残留。

若确实需要清空整个辅助区，直接删除 `.suite_test_tmp` 即可，不影响套件本身。
