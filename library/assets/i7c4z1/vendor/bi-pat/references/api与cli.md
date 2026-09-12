# PAT 通道技术契约（AI 助手读）

## 1. 鉴权（与 Cookie 通道严格区分）

- 请求头必须带：`X-Personal-Token: <gdpat_令牌>` + `X-Guandata-Client: guancli`。
- `Authorization: Bearer` 实测必 401；Cookie（`uIdToken`）与 PAT 是两套平行体系，**绝不混用**。
- 令牌在 BI 页面 `personal-access-token` 自助领取，仅特定权限用户可领；权限 = 用户自身数据权限。

## 2. guancli 工具链（Node ≥ 20）

四件套：`@guandata/guancli`、`guanetl`、`guanvis`、`guands`。缺失时安装：winget 优先，无权限则便携版解压到 `~/node-portable`。

```bash
# 登录（令牌只经命令行传入，不落文档）
guancli auth login --url https://bi.leyopharm.com --pat <令牌> --profile guanbi --default
guancli auth status     # 登录态
guancli auth whoami     # 身份确认（必须报出用户本人账号名）

# 查数（首选，秒级）
guancli ds execute-sql --inputs <dsId> --sql "..."
```

`pat_call.py` 已封装上述流程：凭证窗口取令牌 → 检查登录态 → 执行。临时 ETL（多步处理）：

```bash
guanetl create --parent-dir ob1c755e04b194f9caffb2df --output-parent-dir a7acdf1e5c5af49b1af19778
```

产物用完即清。所有查数走线上数据集，禁止本地静态文件。

## 3. REST 等价端点（2026-09-09 首轮探测）

自建脚本调 API 必须带 `X-Personal-Token` 头。`pat_call.py --probe` 候选端点探测结果：

| 端点 | 状态 | 结论 |
| --- | --- | --- |
| `GET /api/user/profile` | 401 "personal access token is invalid"（假令牌） | ✅ **已探明支持 PAT 鉴权**——真令牌可直接 whoami（免 guancli） |
| `GET /public-api/v2/user/info` | 403 no token provided | 开放平台鉴权层在线，待真令牌复测 |
| `POST /public-api/dataset/execute-sql` | 500 "No static resource" | ❌ 该候选端点不存在，execute-sql REST 形态待探 |

**未探明 execute-sql 的 REST 形态前，生产取数走 guancli；whoami 类校验可走 REST。**

## 4. 数据集字典（常用）

| 数据集 | dsId | 要点 |
| --- | --- | --- |
| dwm_边际利润_月汇总表 | `xce9c00142e3c4be49aeb142` | 月粒度，自带线上口径，月度边际利润首选 |
| dwm_saleout_总代 | `dcbb0d9e082eb47ac9047591` | 日粒度，问"线上"须显式过滤销售类型 |
| dwm_出库宽表ultra-线上 | `ebc37edf77c8540cdad40b62` | 亿级行，禁止全表扫描；与 Cookie 通道出库统计Ultra 板块同源 |

新数据集在使用前先经 `SELECT … LIMIT 1` 或探名列确认结构与权限，再登记进本表。

## 5. 校验四道闸（强制）

1. **维度探名**：查不到先 `SELECT DISTINCT <维度列> … LIKE '%关键字%'` 探实际注册名，禁止直接报"没有数据"。
2. **口径拆解**：含销售类型的表先 GROUP BY 拆开，防内部调拨混入。
3. **时效检查**：聚合带 MIN/MAX 日期列；关注 isTruncated 截断标记；月度数据 T+1。
4. **重大数字双路径验证**：`execute-sql` 与临时 ETL（或与 Cookie 卡片通道）交叉一致才采信；跨通道验证按路由文档 §6 第 5 条——路径不确定先询问用户，耗时长提醒用户是否自助验证。

## 6. 错误速查

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| 401 | 令牌无效/过期/漏字符 | 引导用户重新领取，更新凭证窗口 |
| 403 no token provided | 未带 X-Personal-Token 头 | 检查请求头 |
| 查询无结果 | 大概率是探名/口径问题 | 走四道闸第 1/2 条，不要直接报"没有数据" |
| guancli 命令不存在 | Node/四件套缺失 | 按 §2 安装，或 `pat_call.py --probe` 试 REST |
