# 系统页面、筛选项与查询字段

本文件是页面筛选、请求枚举、响应结构和字段释义的详细事实源，技术基线复核日期为 2026-08-10。标为“实测”的行为不是永久接口合同，服务端升级后应重新验证。

## 目录

- [页面粒度](#页面粒度)
- [公共筛选项](#公共筛选项)
- [请求枚举](#请求枚举)
- [页面独有筛选项](#页面独有筛选项)
- [页面操作](#页面操作)
- [汇总结果](#汇总结果)
- [品种明细结果](#品种明细结果)
- [订单明细结果](#订单明细结果)
- [能力边界](#能力边界)

## 页面粒度

| 页面 | 查询接口 | 一行表示 | JSON 结构 |
|---|---|---|---|
| 汇总 | `orderGoods/pv1461` | 一个排名区间的汇总结果 | `data: [...]` |
| 品种明细 | `orderGoodsDetail/pv1461` | 一个品种聚合结果 | `data.records` 分页 |
| 订单明细 | `pageOrderDetail/pv1461` | 一个订单中的一个品种行 | `data.records` 分页 |

汇总序号 1 的“全部”是全部排名区间总计。品种明细不包含客户身份；订单明细同时包含客户和品种，是最细粒度。

## 公共筛选项

| 页面文案 | API 参数 | 值和使用规则 |
|---|---|---|
| 排名区间设置 | `rangeDivideList` | `1 -（自定义）-（自定义）-（自定义）-（自定义）- 正无穷`；边界按正整数升序传入。 |
| 排名区间筛选 | - | 按排名区间设置显示可选区间，也支持选择“全部”；请求没有独立参数，查询后读取目标区间行。 |
| 公司 | `providerId` | 默认当前凭证所属账号的公司；集团账号可能有多个公司可选。脚本按公司分别执行并保留公司标签。 |
| 发货仓 | `warehouseIds` | 默认当前公司的发货仓范围，可选择一个或多个；显式仓库必须属于当前公司。`--all-warehouses` 与 `--warehouse-id` 不能同时使用，切换公司时不复用原公司的缓存仓库。 |
| 品种负责人 | `staffName` | 单值，传入准确、完整的负责人名称。 |
| 药师帮ID | `djbh` | 平台商品管理码；单值，传入准确的 ID。 |
| 商品 | `goodsCode` | 单值，支持准确的商品编码或完整商品名称。 |
| 业务类型 | `businessType` | 多选；全部、普品、首推、集采、摇钱树、控销。空数组表示全部。 |
| 活动类型 | `deliverTypesV1070` | 多选；全部、一口价、特价、拼团、整件购、批购包邮、连锁优选、其他。空数组表示全部。 |
| 客户类型 | `clientType` | 多选；全部、零售单体、第三终端、批零一体、连锁加盟、连锁总部、商业公司、其他。空数组表示全部。 |
| 支付时间 | `payTimeStart`、`payTimeEnd` | `yyyy-MM-dd HH:mm:ss`；单次跨度最多 48 小时。两项均未传时脚本使用中国时区当天，只传一项时报错。 |
| 地区 | `provinceName`、`cityName` | 多选，可选择省份及其城市；传入省市全称，空数组表示全部。 |

## 请求枚举

本节只提供脚本和接口需要的编码，不改变公共筛选项的页面语义。

| 筛选项 | 请求参数 | 编码 |
|---|---|---|
| 业务类型 | `businessType` | `0` 普品、`1` 首推、`3` 摇钱树、`4` 控销、`5` 集采 |
| 活动类型 | `deliverTypesV1070` | `0` 其他、`1` 一口价、`2` 特价、`3` 拼团、`5` 批购包邮、`6` 连锁优选、`7` 整件购 |
| 客户类型 | `clientType` | `0` 零售单体、`1` 第三终端、`2` 连锁总部（批零一体）、`3` 连锁加盟、`4` 连锁总部（纯连锁）、`5` 商业公司、`6` 其他 |

请求 `deliverTypesV1070` 与响应 `wholesaleType` 使用不同编码体系，不得互换。

## 页面独有筛选项

| 页面 | 文案 | API 参数 | 值和使用规则 |
|---|---|---|---|
| 品种、订单 | 运营负责人 | `operatorId` | 单值；`--operator-name` 接收准确名称并由选项接口映射为 userId，或直接传 `--operator-id`；空值=全部。 |
| 订单 | 自动化运营标签 | `autoOpFlags` | 空数组=全部；`1` 自动跟价、`2` 自动调价、`3` 自动运营、`4` 手动运营、`5` 临时手动运营。 |
| 订单 | 随心购 | `beSuiXinGou` | `""` 全部、`"1"` 是、`"0"` 否。 |
| 订单 | 链接类型 | `linkFlags` | 空数组=全部；`0` 无、`1` 长期链接、`2` 短期链接。 |
| 订单 | 维价类型 | `wholesaleSpecialFlags` | 空数组=全部；`0` 无、`1` 集团维价、`2` 集采维价、`4` 采购维价。 |

响应 `wholesaleSpecialFlag` 可能含组合/展示编码，不能直接作为请求值。

## 页面操作

- 页签：汇总、品种明细、订单明细。
- 公共按钮：重置、搜索、导出、收起。
- 品种和订单页显示“导出任务列表”；任务存在不等于文件已生成。
- “统计指标说明”是页面帮助入口，不是响应字段。
- 品种结果的“查询订单明细”是进入订单粒度的入口。
- 订单结果的“自动调价”“查询活动定价”是页面操作，不属于本 Skill 的只读查询动作。

## 汇总结果

页面列顺序：序号、排名区间、销售、预估 P1、预估 P4、预估 P8、预估补贴·费用。

| 页面字段 | API 字段 | 释义 |
|---|---|---|
| 序号 | UI 生成 | 页面行号；序号 1 的“全部”是总计。 |
| 公司/排名 | `providerName`、`providerId`、`proStoreCnt30rankRange`、`rank` | 公司名称/ID、排名区间文本和区间行标识。 |
| 销售数量 | `num` | 统计范围销售数量。 |
| 销售金额 | `taxAmount`、`taxAmountAlias` | 含税销售金额及别名字段。 |
| 销售品种数 | `goodsNum` | 统计范围销售品种数量。 |
| 销售占比 | `saleProportion` | 当前区间销售金额占全部区间的比例。 |
| 销售客户数 | `customerCount` | 去重成交客户数量，不含客户身份。 |
| P1 成本 | `p1`、`p1TaxAmount`、`estiP1TaxAmount` | P1 成本单价、成本金额及预估别名字段。 |
| P1 毛利 | `p1Rate`、`p1RateNum`、`p1GrossProfit` | P1 毛利率展示值、数值值和毛利额。 |
| P4 成本 | `p4TaxAmount`、`estiP4TaxAmount` | P4 成本金额及预估别名字段。 |
| P4 毛利 | `p4Rate`、`p4RateNum`、`p4GrossProfit` | P4 毛利率展示值、数值值和毛利额。 |
| P4 返利 | `rebateAmount` | 预估应收返利金额，不是已兑现返利。 |
| P8 成本 | `p8`、`p8TaxAmount` | P8 成本单价和成本金额。 |
| P8 毛利 | `p8Rate`、`p8RateNum`、`p8GrossProfit` | P8 毛利率展示值、数值值和毛利额。 |
| P8 返利 | `p8RebatePrice`、`p8RebateAmount` | P8 返利单价和金额。 |
| 补贴 | `detailSubsidyPay`、`goodsDetailSubsidyPay`、`orderDetailSubsidyPay` | 补贴合计、品种补贴、订单补贴。 |
| 费用 | `feeTaxAmount` | 首推费/费用金额字段。 |
| 大调相关 | `dtP1TaxAmount`、`dtP4TaxAmount` | 大调相关 P1/P4 金额。 |

汇总页不能返回具体品种、客户名单或订单。即使按商品关键词筛选，结果仍是排名区间聚合。

## 品种明细结果

页面列顺序：序号、品种负责人、品种、销售、预估 P1、预估 P4、预估 P8、预估补贴·费用。

| 页面字段 | API 字段 | 释义 |
|---|---|---|
| 序号 | UI 生成 | 当前结果行号，不是业务主键。 |
| 公司 | `providerName`、`providerId` | 公司名称和 ID。 |
| 负责人 | `contactorErpName`、`opErpName`、`opName` | 品种负责人、运营负责人。 |
| 具体排名 | `proStoreCnt30rank` | 品种具体排名/门店数排名。 |
| 商品身份 | `goodsName`、`goodsSpec`、`manufacturer` | 商品名称、规格、生产厂家。 |
| 商品编码 | `drugId`、`goodsCode`、`productCode` | 药师帮 ID、商品编码、乐药编码。 |
| 销售 | `num`、`taxAmount`、`price` | 销售数量、含税销售金额、销售均价。 |
| 实付 | `realTaxAmount`、`realPrice` | 客户实付金额、实付均价。 |
| 客户数 | `customerCount` | 购买该品种的去重客户数量，不含客户身份。 |
| P1 | `p1`、`p1TaxAmount`、`p1Rate`、`p1GrossProfit` | P1 成本单价、成本金额、毛利率、毛利额。 |
| P4 | `p4`、`p4TaxAmount`、`p4Rate`、`p4GrossProfit` | P4 成本单价、成本金额、毛利率、毛利额。 |
| P4 返利 | `rebatePrice`、`rebateAmount` | 预估应收返利单价和金额。 |
| P8 | `p8`、`p8TaxAmount`、`p8Rate`、`p8GrossProfit` | P8 成本单价、成本金额、毛利率、毛利额。 |
| P8 返利 | `p8RebatePrice`、`p8RebateAmount` | P8 返利单价和金额。 |
| 补贴/费用 | `detailSubsidyPay`、`goodsDetailSubsidyPay`、`orderDetailSubsidyPay`、`feeTaxAmount` | 补贴合计、品种补贴、订单补贴、首推费/费用。 |
| 其它成本 | `dtP1TaxAmount`、`dtP4TaxAmount`、`estiP1TaxAmount`、`estiP4TaxAmount` | 大调和预估成本相关字段；保留原值，不自行替代主字段。 |

品种页只能回答客户数量。需要跨日唯一客户数或客户名单时，使用订单明细。

## 订单明细结果

页面列顺序：序号、单据、客户、品种、销售、预估 P1、预估 P4、预估 P8、预估补贴·费用。

| 页面字段 | API 字段 | 释义 |
|---|---|---|
| 序号 | UI 生成 | 当前分页行号，不是订单号。 |
| 公司 | `providerName`、`providerId` | 公司名称和 ID。 |
| 订单 | `djbh`、`payTime` | 药师帮单号、支付时间。`djbh` 一单多品时重复。 |
| 活动 | `wholesaleId`、`deliverType`、`wholesaleType` | 活动 ID、中文类型、响应编码。 |
| 仓库 | `warehouseId`、`warehouseName` | 发货仓 ID 和名称。 |
| 客户身份 | `customerCode`、`ysbCustomerId`、`drugstoreName` | 乐药客户编码、药店 ID、客户名称。 |
| 客户类型 | `clientType` | 客户类型展示值。 |
| 客户地区 | `provinceName`、`cityName`、`districtName`、`provinces` | 省、市、区和完整地区文本。 |
| 客户负责人 | `customerPersionName` | 客户负责人。 |
| 品种排名 | `proStoreCnt30rank` | 品种具体排名。 |
| 商品编码 | `drugId`、`goodsCode`、`productCode` | 药师帮 ID、商品编码、乐药编码。 |
| 商品身份 | `goodsName`、`goodsSpec`、`manufacturer` | 名称、规格、厂家。 |
| 品种负责人 | `contactorName` | 品种负责人。 |
| 运营负责人 | `opErpName`、`opName` | 运营负责人 ERP 名称/展示名称。 |
| 销售 | `num`、`taxAmount`、`taxPrice` | 销售数量、销售金额、销售均价。 |
| 实付 | `realPrice`、`realTaxAmount` | 客户实付均价和金额。 |
| P1 | `p1`、`p1TaxAmount`、`p1Rate`、`p1GrossProfit` | P1 成本单价、成本金额、毛利率、毛利额。 |
| P4 | `p4`、`p4TaxAmount`、`p4Rate`、`p4GrossProfit` | P4 成本单价、成本金额、毛利率、毛利额。 |
| P4 返利 | `rebatePrice`、`rebateAmount` | 预估返利单价和金额。 |
| P8 | `p8`、`p8TaxAmount`、`p8Rate`、`p8GrossProfit` | P8 成本单价、成本金额、毛利率、毛利额。 |
| P8 返利 | `p8RebatePrice`、`p8RebateAmount` | P8 返利单价和金额。 |
| 补贴 | `goodsDetailSubsidyPay`、`orderDetailSubsidyPay`、`detailSubsidyPay` | 品种补贴、订单补贴、补贴合计。 |
| 费用 | `subsidyBillType`、`feeTaxAmount` | 补贴单据类型、首推费/费用金额。 |
| 业务 | `businessType` | 业务类型响应值。 |
| 运营标签 | `opFlag`、`opFlagStr`、`autoOpFlag`、`autoOpFlagStr` | 运营标识和自动化运营的编码/展示值。 |
| 随心购 | `beSuiXinGou`、`beSuiXinGouStr` | 随心购编码和展示值。 |
| 链接 | `linkFlag`、`linkFlagStr` | 链接类型编码和展示值。 |
| 维价 | `wholesaleSpecialFlag`、`wholesaleSpecialFlagStr` | 维价编码和展示值。 |

空值或页面 `--` 必须如实保留，不能从其它页面推测补写。

## 能力边界

- 汇总：回答整体/排名段；不能回答具体品种、客户或订单。
- 品种：回答品种指标和客户数量；不能回答客户身份。
- 订单：回答客户-品种-订单行；不能把同一 `djbh` 的多品种行压成一行。
- 当前实测分页合并键：`(djbh, goodsCode, drugId)`；它不是服务端声明的永久业务主键。同键记录的活动、仓库、数量、金额或时间不同则保留并标记复核。
- 平台订单数：按 `djbh` 去重；不要与订单行去重混用。
- 查询失败或字段为空不是零值；按错误和空值处理。
