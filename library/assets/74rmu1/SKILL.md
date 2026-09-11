---
name: github-accelerator
description: 小白友好的一键诊断并修复 GitHub 访问问题（hosts 修复 + 镜像兜底 + SSH-over-443）。当用户反馈"打不开 GitHub / GitHub 加载不出来 / 无法安装 GitHub 上的开源 Skill 或软件 / git clone 失败 / 下载 Release 失败"，或要求"配置 GitHub 加速"时使用。支持 macOS 与 Windows。
---

# GitHub 加速器（github-accelerator）

帮"访问不了 GitHub 的小白"在本机现场诊断并修复。核心原则：**什么都不预设，一切以本机实测为准**——网络阻断是动态的、因人/因 ISP 而异，永远不要凭经验断言"GitHub 挂了"或"这个 IP 一定可用"。

## 脚本位置

- macOS / Linux：`scripts/accelerate.sh`（纯 bash，兼容 macOS 自带 bash 3.2）
- Windows：`scripts/accelerate.ps1`（纯 PowerShell，Win10+ 自带 curl.exe，缺失时自动降级）

## 重要注意事项（开始前必读）

1. **本机诊断必须绕开代理干扰**：脚本内部所有网络测试都已 `--noproxy '*'`（Windows 端置空 DefaultWebProxy）。如果 Agent 自己额外做测试，务必同样绕开代理，否则会误判。
2. **先诊断，再征得用户同意，再动手改系统**。写 hosts 是系统级修改，必须先展示诊断报告并明确告知将做什么。
3. **绝不盲信任何上游 IP 源**（包括 GitHub520）：脚本只把上游内容当候选池，每条 IP 都在本机实测通过后才写入。
4. **hosts 只动标记块**（`# GitHub-Accelerator Start/End` 之间），用户已有内容绝不触碰；写入前自动备份，保留最近 3 份。

## 沙箱环境处理（关键！WorkBuddy 等 Agent 的 Bash 沙箱场景）

WorkBuddy 等带沙箱功能的 Agent，执行命令时会注入本地代理环境变量（形如 `http_proxy=http://127.0.0.1:<端口>`）。这会造成一个经典陷阱：**hosts 修好了、浏览器能打开 GitHub 了，但沙箱里 Agent 自己执行的 `git clone` / `curl` / 下载安装仍然失败**——因为 git/curl 默认服从代理环境变量，流量被沙箱代理拦截了。

规则（Agent 必须遵守）：

1. **skill 的脚本自身已免疫**（内部全部绕开代理），但 **Agent 自己后续执行的所有 GitHub 相关操作（git clone/pull、curl 下载、安装 skill 的搬运步骤）必须按 check 报告 `[1.5/4]` 节的结论处理**：
   - 报告"沙箱代理路径不通"→ Agent 的命令必须绕开代理：
     - **macOS/Linux 前缀**：`env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY git clone https://github.com/xxx/yyy.git`
     - **Windows PowerShell 先清空再执行**：`$env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:http_proxy=""; $env:https_proxy=""; git clone ...`
     - **curl**：一律加 `--noproxy '*'`
   - 报告"沙箱代理路径可通"→ Agent 可保留默认环境。
   - 若直连与代理路径**均不通**，按失败底牌处理（见下文）。
2. **双路原则**：个别沙箱只允许代理出口、禁止直连。哪种路通就走哪条，不要死磕绕代理。脚本的 `[1.5/4]` 节已自动完成双路实测。
3. **hosts 修复对两条路径都生效**：沙箱代理是本机进程，它自己做 DNS 解析时用的就是系统解析器（含 /etc/hosts）。所以修好 hosts 后，走代理的流量也可能被顺带救活。
4. **Windows 专属**：沙箱内 git 报"证书吊销功能无法检查吊销"（0x80092012）时，追加参数：`git -c http.schannelCheckRevoke=false clone ...`
5. **浏览器不受影响**：浏览器不走命令行代理环境变量，hosts 修复后浏览器是否可访问只取决于直连路径状态。

## 工作流

### 第 1 步：诊断（只读，无需权限）

```bash
# macOS / Linux
bash "<skill目录>/scripts/accelerate.sh" check
```
```powershell
# Windows
powershell -ExecutionPolicy Bypass -File "<skill目录>\scripts\accelerate.ps1" check
```

诊断会输出：各域名直连状态、**Agent 沙箱环境双路检测**（若检测到注入代理，会实测"直连路径"与"沙箱代理路径"哪个通，并给出 Agent 自己执行 git/curl 时的对策）、SSH 通道（22 端口 / ssh.github.com:443）、失败域名的可修复 IP、可用镜像。

### 第 2 步：向用户展示报告并确认

把诊断结果翻译成大白话告诉用户（例："你的网络里 github.com 网页能开，但下载文件用的 raw 通道挂了，我可以把它修好，需要你输一次开机密码授权"），**等用户同意后才进入下一步**。同时告知：如配置 git 镜像，下载会经第三方镜像（如 ghfast.top）中转。

### 第 3 步：执行修复（需要管理员权限）

- **macOS**（触发系统原生授权弹窗，用户输入开机密码即可）：

```bash
osascript -e 'do shell script "bash <skill目录>/scripts/accelerate.sh apply" with administrator privileges'
```

若弹窗方式失败（如远程/无 GUI），降级为让用户自己在终端执行：
`sudo bash "<skill目录>/scripts/accelerate.sh" apply`

- **Windows**：脚本会自动弹 UAC 提权，无需特殊处理：

```powershell
powershell -ExecutionPolicy Bypass -File "<skill目录>\scripts\accelerate.ps1" apply
```

apply 做的事：备份 hosts → 逐域名实测候选 IP（三源：GitHub520 源 + 阿里/DNSPod DoH + 硬编码网段）→ 每个域名写入最多 3 个实测通过的 IP（多行 = 系统自动备胎，抗线路抖动）→ 刷新 DNS 缓存 → 端到端回读验证并输出报告 → 视情况配置 SSH-over-443 兜底与 git 镜像（仅当 github.com 直连失败且镜像可用时才写 git 配置，避免不必要的第三方中转）。

**注意**：国内对 GitHub 的阻断常是间歇性抖动的——同一 IP 此刻通、两分钟后可能挂。因此：① apply 结束码为 1（部分域名验证时恰好抖挂）属正常现象，多 IP 备胎链会在网络相位切换时自动接管，不必反复重跑；② 若用户反馈完全打不开，先跑 check 看整体相位，再跑 apply 刷新 IP 池。

如果用户**不希望改动 git 配置**（担心 push 流量经镜像），加 `--no-git` 语义：即跳过脚本内 git 部分，只执行 hosts 写入——向用户说明后手动跑 apply 并在执行后运行 rollback 中 git 部分清理即可，或直接只执行 check + 手动 hosts 方案。

### 第 4 步：向用户报告结果

- 全部修复：告诉用户"以后打不开 GitHub 就再跑一次这个 skill"，并提醒 WorkBuddy 用户可设置每周定时自动运行一次（幂等，重复运行安全）。
- 部分修复：如实说明哪些域名救不活、原因是什么（如"该域名所有已知 IP 在你的网络下都被阻断"）。

## 失败底牌（当 hosts 和镜像都救不了时）

诚实告知 + 给出两条出路（**只指引，绝不代用户安装**）：

1. **镜像手动拼接法**：在可用的镜像（诊断报告里有）后面拼接完整 GitHub 地址访问/下载，例如
   `https://ghfast.top/https://github.com/作者/仓库名/releases` 可下载文件；git clone 用 `git clone https://<镜像>/https://github.com/作者/仓库名.git`。
2. **本地加速工具指引**：如确需完整浏览 github.com 网页，可自行了解 Watt Toolkit（Steam++）等本地加速工具，由用户自主决定是否安装。

## 一键回滚 / 卸载

```bash
# macOS（需权限弹窗）
osascript -e 'do shell script "bash <skill目录>/scripts/accelerate.sh rollback" with administrator privileges'
```
```powershell
powershell -ExecutionPolicy Bypass -File "<skill目录>\scripts\accelerate.ps1" rollback
```

rollback 会：还原最近一次备份的 hosts（无备份则仅移除标记块）、移除 git 镜像配置、移除 SSH-over-443 兜底。查看当前状态用 `status` 子命令。

## 日常维护建议

- 遇到 GitHub 又打不开 → 重跑 check + apply（幂等，随时可重复）。
- WorkBuddy 用户可创建每周一次的定时自动化：prompt 为"运行 github-accelerator skill，执行 check，若有域名直连失败则执行 apply 并汇报结果"。
- skill 自身更新：主仓库在 Gitee（国内直连），公众号文章附有 SKILL.md 全文可手动更新。
