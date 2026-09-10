# 资产管理层 · 总路由地图

> 快照：2026-09-10 ｜ 事实源：`routes.json`（v2）｜本文件由 `engine.py` 生成，勿手工编辑；增删改走同步命令或管理台。

图例：类型为自由文本（默认 `方法论` / `Skill包`）；`→ 挂载` 即该节点在仓库内的资产目录（相对项目根，如 `library/assets/…`）。

> 路由方式：按各节点**描述**匹配任务场景 → 命中即进其 `→ 挂载` 目录，读 `SKILL.md`／`README.md` 按其指引调用；无命中则按任务处理层自带判据亲自动手（不依赖任何资产）。
> 同读 `library/.memory.md`：命中「失效模式」先规避，命中「有效做法」直接复用。
> 回写契约：交付后追加轨迹时 `--routed` 写**命中的节点 id**（各节点行首反引号内）；无命中（自带判据亲做）写 `none`——该字段是规则归属与命中率统计的唯一依据。

- `i7c4z1` **观远BI-乐药查询** `Skill?` → `library/assets/i7c4z1/`
  - _有任何业务数据需求时！可使用此资产。仅适用于查询当前前一天的数据，不适用实时查询当天数据！可自定义聚合。_

## 维护

```text
python library/engine.py                      # 重绘本地图 + 契约校验（挂载/入口文档/id）
python library/engine.py add --id <新id> --type <类型> --title "<标题>" [--parent <父id>] [--mount 挂载] [--description "<何时用>"]
python library/engine.py remove --id <节点id>
python library/engine.py update --id <节点id> [--title 新标题] [--mount 挂载] [--description "<何时用>"]
python library/admin/console.py                     # 可视化管理台（推荐给日常维护）
```
