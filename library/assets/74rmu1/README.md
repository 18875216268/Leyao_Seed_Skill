# github-accelerator

小白友好的一键诊断并修复 GitHub 访问问题。让浏览器和各类 AI Agent（WorkBuddy、豆包、千问等）都能正常访问 GitHub、安装开源 Skill 和软件。

**为什么需要它**：国内很多网络环境访问 GitHub 不稳定——有时主站挂、有时下载文件用的 raw 通道挂、有时 SSH 被封。而且**每个人的网络状况不一样，今天能用明天可能就挂**。所以这个工具不预设任何"应该能通"的通道，而是在你的电脑上现场逐条实测，什么能用就用什么。

## 核心特性

- **诊断驱动**：先体检后开方。每次运行都会重新实测所有通道，绝不盲信任何上游 IP 源
- **只写实测通过的**：hosts 条目逐条验证后才写入，写入内容严格限制在标记块内，不动你 hosts 里的任何其他内容
- **自动备份 + 一键回滚**：写 hosts 前自动备份（保留 3 份），随时可完全还原
- **多层兜底**：hosts 修复（GitHub520 源 + 阿里/DNSPod DoH + 已知网段三源候选）→ 镜像下载加速（6 家自动竞速切换）→ SSH-over-443（22 端口被封时的 git 通道）
- **纯脚本零依赖**：macOS 用自带 bash，Windows 用自带 PowerShell，不需要安装 Python/Node 或任何其他软件
- **诚实原则**：救不了的会明确告诉你救不了、为什么，并给出替代出路，绝不假装修复成功

## 使用方式

本工具设计为由 AI Agent 引导执行（推荐 WorkBuddy 等）。告诉你的 Agent：

> 我的电脑打不开 GitHub / 装不了 GitHub 上的 skill，请用 github-accelerator 帮我修复

Agent 会按流程：诊断 → 用大白话解释报告 → 你确认 → 弹出系统授权框（输一次开机密码）→ 修复 → 验证 → 报告。

也可以手动运行：

```bash
# macOS / Linux
bash scripts/accelerate.sh check     # 只诊断，不改任何东西
bash scripts/accelerate.sh apply     # 诊断 + 修复（需要 sudo / 管理员权限）
bash scripts/accelerate.sh status    # 查看当前状态
bash scripts/accelerate.sh rollback  # 一键回滚
```

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\accelerate.ps1 check
powershell -ExecutionPolicy Bypass -File scripts\accelerate.ps1 apply
powershell -ExecutionPolicy Bypass -File scripts\accelerate.ps1 rollback
```

## 平台状态

| 平台 | 状态 |
|---|---|
| macOS | v1.0，已真机完整验证 |
| Windows | v1.0 beta，逻辑与 macOS 版对齐，内测中 |

## 隐私与透明说明

- 当 github.com 直连失败时，工具会配置 git 走第三方公益镜像（如 ghfast.top）加速下载，下载内容会经过镜像服务器中转。不希望如此可让 Agent 跳过 git 配置，或用 rollback 移除。
- 工具只读写本机的 hosts / git 配置 / SSH 配置（均有备份与标记），不做任何其他系统改动，不上传任何数据。

## License

MIT
