# 资产管理层 · 总路由地图

> 快照：2026-09-11 ｜ 事实源：`routes.json`（v2）｜本文件由 `engine.py` 生成，勿手工编辑；增删改走同步命令或管理台。

图例：类型为自由文本（默认 `方法论` / `Skill包`）；`→ 挂载` 即该节点在仓库内的资产目录（相对项目根，如 `library/assets/…`）。

> 路由方式：按各节点**描述**匹配任务场景 → 命中即进其 `→ 挂载` 目录，读 `SKILL.md`／`README.md` 按其指引调用；无命中则按任务处理层自带判据亲自动手（不依赖任何资产）。
> 同读用户区记忆 `.leyao-data/data/memory.md`（与 skill 同级）：命中「失效模式」先规避，命中「有效做法」直接复用。
> 回写契约：交付后追加轨迹时 `--routed` 写**命中的节点 id**（各节点行首反引号内）；无命中（自带判据亲做）写 `none`——该字段是规则归属与命中率统计的唯一依据。

- `bvix9o` **公共套件** `Skill包` → `library/assets/bvix9o/`
  - _万能套件，任何情况下可参考，目前包含：

1.制作PPT；
2.绘制流程图；
3.将抽象观点变成图；
4.降ai味；
5.更多......

其它需要各种技能协助进行任务处理的时候，可参考此文档，以获取更多技能，并获得更多支持。_
- `fmcq5n` **github-web-skill** `Skill包` → `library/assets/fmcq5n/`
  - _本机网络正常，但访问github因为各种原因不可达时，使用此技能以连通。_
- `p3nes3` **Pms-乐药查询** `Skill包` → `library/assets/p3nes3/`（1 个子节点）
  - _有任何业务数据需求时！可使用此资产。适用于各类数据查询，出库数据、促销数据以及基础数据等，按内置板块参数筛选查询，实时性较强（实时性：促销毛利＞其它板块）。但无法自定义聚合、计算数据，具体见主文档。_
  - `psol5x` **Pms-促销毛利板块** `Skill包` → `library/assets/p3nes3/vendor/optimizers/Pms_促销毛利v1.08/psol5x/`
    - _Pms-乐药查询/销毛利板块定向优化！当查询的数据涉及当天或某天实时数据（精确到时分秒）、品种分层数据查询时，可优先使用此板块。具体可查询：
1.当天汇总数据，包括含税销售、P4毛利等；
2.当天品种明细，即各个品种汇总数据，按品种统计；
3.当天订单明细，按订单统计，有品种、有客户，最细粒度数据；
Ps：无法查询应收边际利润数据！
此板块无法解决时，回退到父级路由！_
- `i7c4z1` **观远BI-乐药查询** `Skill包` → `library/assets/i7c4z1/`（1 个子节点）
  - _有任何业务数据需求时！可使用此资产。适用于各类数据查询，可自定义筛选、聚合、计算数据，但其数据只截止到前一天，时效性较滞后，具体见主文档。_
  - `h4dsa6` **BI-自助取数Ultra板块** `Skill包` → `library/assets/i7c4z1/vendor/optimizers/BI-出库统计Ultra查询v1.08/h4dsa6/`
    - _出库统计！自定义筛选、聚合以及计算字段、指标等，万能取数端口，筛选支持批量传值，大部分取数场景适用。此板块无法解决时，回退到父级路由！_

## 维护

```text
python library/engine.py                      # 重绘本地图 + 契约校验（挂载/id；入口文档缺失仅提示）
python library/engine.py add --id <新id> --type <类型> --title "<标题>" [--parent <父id>] [--mount 挂载] [--description "<何时用>"]
python library/engine.py remove --id <节点id>
python library/engine.py move --id <节点id> [--parent <父id>]   # 移动节点（省略即移到根）
python library/engine.py update --id <节点id> [--title 新标题] [--mount 挂载] [--description "<何时用>"]
python library/admin/console.py                     # 可视化管理台（推荐给日常维护）
```
