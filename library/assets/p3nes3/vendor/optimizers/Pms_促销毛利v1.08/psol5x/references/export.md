# 导出与表格字段

## 目录

- [选择导出](#选择导出)
- [命令](#命令)
- [汇总和品种同步导出](#汇总和品种同步导出)
- [订单异步导出](#订单异步导出)
- [文件验证](#文件验证)
- [汇总导出列](#汇总导出列)
- [品种明细导出列](#品种明细导出列)
- [订单明细导出列](#订单明细导出列)

## 选择导出

| 类型 | 接口 | 机制 | 类型码 | 适用情况 |
|---|---|---|---|---|
| 汇总 | `orderGoodsExport/pv1461` | 同步 xlsx，无密码 | 无 | 用户明确需要 PMS 原始汇总 Excel |
| 品种 | `orderGoodsDetailExport/pv1461` | 同步 xlsx，无密码 | `59` | 全量品种、批量商品分析 |
| 订单 | `exportBigOrderDetail/pv22110` | 异步加密 zip + 加密 Office | `58` | 全量客户/订单行、客户-品种分析 |

少量汇总和少量明细优先查询。不要为了少量结果提交异步订单任务。

是否导出以及批量单值条件、长周期和本地处理的取数方式统一按 `scenarios.md` 决策；本文件只规定导出、解密和文件验证机制。

## 命令

```bash
python scripts/pms_export.py summary --output summary.xlsx --start "2026-08-08 00:00:00" --end "2026-08-08 23:59:59"
python scripts/pms_export.py goods --count-first --output goods.xlsx --start "2026-08-08 00:00:00" --end "2026-08-08 23:59:59"
python scripts/pms_export.py orders --output orders.xlsx --start "2026-08-08 00:00:00" --end "2026-08-08 23:59:59"
```

三个命令共享 `scenarios.md` 和 `system.md` 中的筛选项。使用 `--help` 查看全部参数。

默认限制 48 小时。只有确认服务端支持时才显式使用 `--allow-long`；服务端返回 `42053` 时按日分别导出，不能把失败当成空数据。

## 汇总和品种同步导出

- 请求 `size` 固定为 `60000`，它是单次请求上限，不是完整性证明。
- 汇总直接返回标准 xlsx。
- 需要完整品种数据时使用 `--count-first` 调用 `countOrderDetail(type=59)` 估算行数；估算超过 60000 时脚本在导出前停止，应缩小或拆分范围。
- 响应必须以 `PK` 开头，并通过 `openpyxl` 打开验证后才报告成功。
- 输出路径已存在时必须显式传 `--overwrite`。
- 工作簿能够打开只证明文件有效；还要结合预估行数、筛选范围和拆分计划判断数据是否完整。实时数据可能变化，不要求计数与导出行数严格相等。

## 订单异步导出

1. 提交前读取一次 `type=58` 任务列表，记录已有任务 ID。
2. 提交一次 `type=58` 任务。`lineUpNumber` 只表示共享队列排号，不是任务 ID，也不会出现在任务列表记录中。
3. 轮询 `listExportInfoByType/pv583`，计算当前任务 ID 与提交前 ID 的差集；只接受唯一新增任务。
4. 发现新增任务后立即锁定其 `id`，后续只按该 ID 轮询；不重新绑定，不选择历史任务或列表中的“最新任务”。
5. 锁定任务达到 `fileStatus=1` 且具有 URL 后，使用其 `id` 调用 `selectExportPassword/pv583`。
6. 通过 HTTPS 流式下载密码 Zip；同一密码用于 Zip 解压和 Office 解密。
7. Zip 中必须恰有一个 `.xlsx`。脚本只读取该成员并写入受控临时文件，不展开其它成员或原始路径。
8. 使用 `msoffcrypto-tool` 解密，再用 `openpyxl` 验证工作簿；验证成功后原子发布目标文件。

订单导出进入共享队列。不要高频重复提交；超时应返回错误并保留明确的后续动作。脚本不打印密码。传 `--raw-zip PATH` 才额外保存原始加密 zip。

同一凭证的订单导出任务必须串行提交。只有当前任务完成下载、解密和 xlsx 验证后，才提交下一任务；不要并发调用多个 `pms_export.py orders`。若提交后同时出现多个新增任务，脚本会返回归属不唯一错误，不猜测其中任何一条。

## 文件验证

- 同步文件：OOXML 文件头 `PK`，且 `openpyxl.load_workbook(..., read_only=True)` 成功。
- 订单下载：外层为密码 Zip；内部解密后为可读 xlsx。
- Excel 至少包含一个工作表；验证失败时删除本次生成的不完整目标文件。
- PMS 导出的工作表可能把尺寸元数据错误声明为 `A1:A1`。需要统计行列或读取全表时，先调用只读工作表的 `reset_dimensions()`，再使用 `iter_rows()`；不能只信任 `max_row/max_column`。
- 导出金额可能是字符串，读取后使用 Decimal/数值转换；不要用二进制浮点直接累计金额。
- 中文表头可能有空格，匹配前先规范化表头。
- JSON 响应或 HTML 错误页不能保存成 xlsx。

## 汇总导出列

|列|中文表头|API 字段|
|---:|---|---|
|1|公司|`providerName`|
|2|排名区间|`proStoreCnt30rankRange`|
|3|销售数量|`num`|
|4|销售金额|`taxAmount`|
|5|销售品种数|`goodsNum`|
|6|销售占比|`saleProportion`|
|7|销售客户数|`customerCount`|
|8|预估P1毛利率|`p1Rate`|
|9|预估P1毛利额|`p1GrossProfit`|
|10|预估P1成本金额|`p1TaxAmount`|
|11|预估P4毛利率|`p4Rate`|
|12|预估P4毛利额|`p4GrossProfit`|
|13|预估P4成本金额|`p4TaxAmount`|
|14|预估返利|`rebateAmount`|
|15|P8毛利率|`p8Rate`|
|16|P8毛利额|`p8GrossProfit`|
|17|P8成本金额|`p8TaxAmount`|
|18|P8预估返利|`p8RebateAmount`|
|19|品种补贴|`goodsDetailSubsidyPay`|
|20|订单补贴|`orderDetailSubsidyPay`|
|21|首推费|`feeTaxAmount`|

## 品种明细导出列

|列|中文表头|API 字段|
|---:|---|---|
|1|公司|`providerName`|
|2|品种负责人|`contactorErpName`|
|3|运营负责人|`opErpName`|
|4|具体排名|`proStoreCnt30rank`|
|5|商品名称|`goodsName`|
|6|商品规格|`goodsSpec`|
|7|生产厂家|`manufacturer`|
|8|药品ID|`drugId`|
|9|商品编码|`goodsCode`|
|10|乐药编码|`productCode`|
|11|销售数量|`num`|
|12|销售金额|`taxAmount`|
|13|销售均价|`price`|
|14|实付均价|`realPrice`|
|15|客户数|`customerCount`|
|16|预估P1毛利率|`p1Rate`|
|17|预估P1毛利额|`p1GrossProfit`|
|18|预估P1成本单价|`p1`|
|19|预估P1成本金额|`p1TaxAmount`|
|20|预估P4毛利率|`p4Rate`|
|21|预估P4毛利额|`p4GrossProfit`|
|22|预估P4成本单价|`p4`|
|23|预估P4成本金额|`p4TaxAmount`|
|24|预估应收返利单价|`rebatePrice`|
|25|预估应收返利|`rebateAmount`|
|26|P8毛利率|`p8Rate`|
|27|P8毛利额|`p8GrossProfit`|
|28|P8成本金额|`p8TaxAmount`|
|29|P8成本单价|`p8`|
|30|P8预估返利|`p8RebateAmount`|
|31|P8预估返利单价|`p8RebatePrice`|
|32|品种补贴|`goodsDetailSubsidyPay`|
|33|订单补贴|`orderDetailSubsidyPay`|
|34|首推费|`feeTaxAmount`|

## 订单明细导出列

|列|中文表头|API 字段|
|---:|---|---|
|1|公司|`providerName`|
|2|药师帮单号|`djbh`|
|3|支付时间|`payTime`|
|4|活动ID|`wholesaleId`|
|5|活动类型|`deliverType`|
|6|仓库名称|`warehouseName`|
|7|客户编码|`customerCode`|
|8|药店ID|`ysbCustomerId`|
|9|名称|`drugstoreName`|
|10|类型|`clientType`|
|11|地区|`provinces`|
|12|客户负责人|`customerPersionName`|
|13|品种负责人|`contactorName`|
|14|运营负责人|`opErpName`|
|15|具体排名|`proStoreCnt30rank`|
|16|药师帮ID|`drugId`|
|17|商品名称|`goodsName`|
|18|商品规格|`goodsSpec`|
|19|生产厂家|`manufacturer`|
|20|商品编码|`goodsCode`|
|21|乐药编码|`productCode`|
|22|销售数量|`num`|
|23|销售金额|`taxAmount`|
|24|销售均价|`taxPrice`|
|25|实付均价|`realPrice`|
|26|预估P1毛利率|`p1Rate`|
|27|P1成本金额毛利额|`p1GrossProfit`|
|28|预估P1成本单价|`p1`|
|29|预估P1成本金额|`p1TaxAmount`|
|30|实付金额|`realTaxAmount`|
|31|预估P4毛利率|`p4Rate`|
|32|P4成本金额毛利额|`p4GrossProfit`|
|33|预估P4成本单价|`p4`|
|34|预估P4成本金额|`p4TaxAmount`|
|35|预估返利单价|`rebatePrice`|
|36|预估返利|`rebateAmount`|
|37|P8毛利率|`p8Rate`|
|38|P8毛利额|`p8GrossProfit`|
|39|P8成本金额|`p8TaxAmount`|
|40|P8成本单价|`p8`|
|41|P8预估返利|`p8RebateAmount`|
|42|P8预估返利单价|`p8RebatePrice`|
|43|品种补贴|`goodsDetailSubsidyPay`|
|44|订单补贴|`orderDetailSubsidyPay`|
|45|首推费|`feeTaxAmount`|
|46|自动化运营标签|`autoOpFlagStr`|
|47|是否随心购订单|`beSuiXinGouStr`|
|48|维价标签|`wholesaleSpecialFlagStr`|

导出不包含全部 JSON 字段。例如 `wholesaleType`、`subsidyBillType`、`opFlag`、`linkFlag`、`districtName` 可能只存在于查询响应。
