# 卡（常驻速查）规范

> **定位**：运营知识库的「**目录页 + 最热结论页**」——名称 → 一句话 → 指针（知识回池、地基看路由）；供 AI **每任务必读、一眼识别**。
> 只读、极薄、离线可用；**不是知识库副本** ✗（全文永远在池里，卡只放"知道有这回事、去哪查"）。
> **数据源（硬）**：① **共享池**（业务知识条目：`pool#id` 指向）；② **`@` 基础卡**（路由地基条目：`node#id` 指向——
> 输入 = 路由面 `@` 节点的名称 + 描述，**由卡宿主代蒸**，见 §3）；**云智库永不蒸馏** ✗——它体量大且定位为"只查"，
> 只作**按需查询兜底**（命中卡条目后回池检索；查询结果不回写卡）。

## 1. 产出（用户数据区三件套 + 候选）

| 文件 | 内容 | 说明 |
| --- | --- | --- |
| `card.json` | 机器态：`{meta, items:[{name, alias, oneliner, pointer, trust, quality}]}` | **事实源**；`meta` 必须含 `pool_version`（优先取 fetch 的 `pool_updated_at`，无则记生成时间）/ `generated_at` / `ttl_days`（check 补 `items/checked/chars`） |
| `card.md` | 渲染物（**稳定排序 = 入卡序**，不按热度重排；**尾部固定【基础卡】节**——无基础卡条目则不出现） | **AI 每任务读这个** |
| `card.meta.json` | `{pool_version, generated_at, ttl_days, items, checked, chars}` | **快照诊断**（fresh/stale 判据；不触发刷新） |
| `card.candidates.json` | fetch 产物（**派生中间物，可随时删**）：含 `mode`（index/category）· `total` · `pool_updated_at` · `truncated` · `diff{new,changed,gone}` | 增量蒸馏依据 |

落点两种形态（与 `common.HOME` 一致）：**挂载态**（本资产位于某包的 `library/assets/` 下）→ `<包父级>/.leyao-data/data/assets/<id>/`；**独立态**（不在 `assets/` 下，或显式设 `LEYAO_KB_HOME`）→ `~/.leyao-kb/`。
`items[].trust/quality` 为池侧来源快照（备查；当前不参与判定）。

## 2. 条目纪律（硬）

- 格式：`name（alias）→ oneliner ≤25 字 → pointer`；**pointer 两种**：知识条目 `pool#<id>`（候选在场时 `check` **离线全量核验**，死指针判不合格；`--verify N` 再抽样回池验存在）｜基础卡条目 `node#<id>`（`check --nodes <routes.json>` 时离线核验"节点存在且有 mount"；未提供 → 仅格式校，fail-soft）；
- 写不下（>25 字）**即弃**——说明它该走按需检索，不该上卡 ✗；
- **基础卡条目**（`node#id`）：只收**有 mount** 的 `@` 节点（容器不上卡 ✗）；一句话由**卡宿主代蒸**（输入 = 节点名称 + 描述，写不下同样弃）；按 `节点id + 描述指纹` 增量——新增/变更才重蒸，**原位替换 / 尾部追加、不重排**；
- **不新增事实**（只压缩与指向；定义冲突时**以池 authority 为准**并触发卡更新）；
- **规则类条目（禁令 / 红线 / 合规）**：一句话只用于**识别**——AI 命中后**必须回池读全文**再执行（命令：`python scripts/card.py read --id <pool#id>`），不得只按一句话行动 ✗；
- 预算：**显示文本（name+alias+oneliner）≤ 1500 字**，另计**固定开销 67 字**（卡头 40 + 基础卡节头 27）；**条数 ≤ 80**；`pool#id` / `node#id` 指针是机器句柄，不计入；
- 不存全文/表格/案例；**不记执行经验**（那是 `data/memory.md` 的职责）；
- 卡**只读给 AI**；运行卡（用户区）由**资产自治**更新（AI 会话内四步 + 留痕 ✓）；
  `asset_write` 提案只用于**包内**（规范/基线文档）——用户区卡不在提案作用域。

## 3. 生产者插槽（蒸馏可替换 · 与框架解耦）

```text
输入：候选 JSON（title / content / pool_id / trust / quality / hit_count / version）
输出：条目草案（name / alias / oneliner≤25字 / pointer=pool#id）
约束：不新增事实 · 保留 pool_id 可回验 · 写不下即弃
闸门：无论谁蒸馏，`card.py check` 是唯一验收
```

**优先级（按能力寻源，不点名任何资产 ✗）**：路由中匹配的「蒸馏/提炼/摘要」类资产（多个 → 按判据 1b/2.6 选最优）
→ 无 → 判据 3.5 ③按需获取（find-skills）→ ④轻量调研 → ⑤宿主自做。

## 4. 刷新（单层：唯一蒸馏时点 = 每日 14:00）

| 场景 | 行为 | 执行者 |
| --- | --- | --- |
| **首次使用**（无卡） | ① 生成卡（fetch → 蒸馏 → check → render）；② **创建每日 14:00 定时任务**（**幂等按用途判重**：已存在同一刷新任务——无论名称写法——则**就地更新为当前口径**，不新建 ✗）——让宿主每日 14:00 被唤起跑巡检；③ **完成就绪（1 准则 + 4 行为）**：宿主常驻 + Skill 自主更新 + 禁止绕过（见 `version/VERSION.md` §零——「就绪」的**唯一权威定义**，其他文档只指针不复制） | 宿主 AI（**自行定位本宿主的**自动化/定时能力：自动化面板 / 任务计划 / API / CLI…因宿主而异，不预设 ✗；**不可用 → 如实告知用户**：按宿主文档自建唤起，或人工每日跑一次四步——不假装已具备无人值守 ✗） |
| **每日 14:00**（唯一时点） | 定时任务唤起 → `fetch → 蒸馏（只处理 diff）→ check → render`；**无变更（diff 空）→ 不动卡**；随后按 `version/VERSION.md` §零 **巡检 4 行为**（宿主常驻在位 / 位置未变 · Skill 自主更新检测 · 入口提示在位；读 `<用户区>/data/state/ready.json` 校验就绪戳——均在位则**静默通过**）；失败如实标注、不阻断 | 宿主 AI（无人值守） |

**无其他蒸馏时点** ✗：不做"会话追赶"（读到过期不自动刷）、不设多档触发；机器 14:00 不在线 → 当天不跑、**次日 14:00 补上**。
手动命令（`fetch/check/render`）始终可跑——属维护动作，不算"蒸馏时点"。
**定时任务的任务描述**（供宿主创建时使用，`<技能包>` 替换为实际路径）：`刷新业务速查卡并巡检 4 行为：① 按 <技能包>/library/assets/pp32an/references/card.md 规范执行——跑 scripts/card.py fetch → 蒸馏（业务条目 + @ 基础卡条目一并蒸，只处理 diff）→ check --nodes <技能包>/library/routes.json → render（diff 空则不动卡）；② 按 <技能包>/version/VERSION.md §零 巡检 4 行为（宿主常驻在位/位置未变 · Skill 自主更新检测 · 入口提示在位；读 <用户区>/data/state/ready.json 校验就绪戳；均在位则静默通过）；③ 全程失败如实标注、不阻断`。

**增量（已实现）**：`fetch` 输出 `diff{new,changed,gone}`（池已升级时基于 `fields=index` 全池索引 → **含长尾 new 与废弃 gone**；
未升级时基于上一份候选）→ 只蒸馏**新增/变更**条目；基础卡条目按其 `节点id+描述指纹` 同样只处理变更；卡**原位替换已变行 / 尾部追加新行**——**不重排**（前缀稳定 ✓）。
**诊断（诚实）**：`status` 按 **TTL + 校验态** 判**快照新鲜度**（离线；仅诊断，不触发刷新）；
"池版本变化"由 **`fetch` 的 `pool_updated_at`/`diff` 显式暴露**（池已升级；`meta.pool_version` 记录快照）。

## 5. 用法（AI / 维护者）

```bash
python scripts/card.py status [--ttl-days N]   # 快照诊断（missing/fresh/stale/unchecked；不触发刷新）
python scripts/card.py fetch  [--category …] [--limit 50]   # 拉共享池候选 + 精确 diff（index 优先，自动回落）
python scripts/card.py check [--verify N] [--nodes <routes.json>]   # 校验卡（唯一验收）；--verify 抽样回池；--nodes 离线核验基础卡指针（**框架内建议带**）
python scripts/card.py render                 # card.json → card.md（稳定排序）
python scripts/card.py read --id <pool#id>    # 按指针读全文（规则类条目命中后的"回池读全文"；只读、不写状态）
python tests/run_term_eval.py --coverage      # 术语快问：题库/指针/覆盖核对（离线）
python tests/run_term_eval.py --emit-prompt --arm card|base   # 两臂探测提示词（带卡 / 无卡）
```
退出码：`0` 成功 ｜ `1` 卡不合格 ｜ `3` 依赖/未命中（无卡、无池端点、无候选）｜ `5` 网络失败（全部类别失败）。
生成流程：`fetch` → **AI 按本规范蒸馏（只处理 diff）** → 写 `card.json` → `check` → `render`。

## 6. 边界（硬）

- **fail-soft**：池拉不到 / 无卡 → 用旧卡或如实标注「卡不可用」，**绝不阻断任务**；
- 卡**不做路由决策**（选谁执行是 `ROUTES.md` 的事）——【基础卡】节只做**识别与定位**（名称→一句话→`node#id`）；**不做执行经验**（那是 `memory.md` 的事）——各司其职；
- **云智库永不进卡** ✗（数据源硬规则见 §1）；查询结果也不回写卡（要更新知识条目 → 先更新共享池）；
- 卡是三件套的唯一事实源是 `card.json`；`card.md` 为渲染物（手改会被 `render` 覆盖 ✗）。
