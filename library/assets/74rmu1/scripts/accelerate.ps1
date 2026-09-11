# ============================================================
# github-accelerator v1.1.0 —— 诊断驱动的小白 GitHub 加速器 (Windows)
# [BETA] 本脚本按 Windows 标准方案编写，尚未经真机验证，请内测后再定版
#
# 用法（由 AI Agent 按引导调用，不建议小白手动操作）:
#   powershell -ExecutionPolicy Bypass -File accelerate.ps1 check     # 只诊断（无需管理员）
#   powershell -ExecutionPolicy Bypass -File accelerate.ps1 apply     # 诊断+修复（自动弹 UAC）
#   powershell -ExecutionPolicy Bypass -File accelerate.ps1 rollback  # 回滚
#   powershell -ExecutionPolicy Bypass -File accelerate.ps1 status    # 查看状态
#
# 纯 PowerShell 零外部依赖（Windows 10+ 自带 curl.exe，缺失时自动降级 Invoke-WebRequest）
# ============================================================

param(
    [Parameter(Position = 0)]
    [ValidateSet('check', 'apply', 'rollback', 'status')]
    [string]$Cmd = 'check'
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$Version = '1.1.0'
$MarkStart = '# GitHub-Accelerator Start'
$MarkEnd = '# GitHub-Accelerator End'
$SysRoot = if ($env:SystemRoot) { $env:SystemRoot } else { 'C:\Windows' }
$HostsPath = if ($env:SystemRoot) { Join-Path $SysRoot 'System32\drivers\etc\hosts' } else { '/etc/hosts' }
$BackupKeep = 3
$UA = "Mozilla/5.0 (compatible; GitHub-Accelerator/$Version)"
$TimeoutSec = 6

# 需要修复的域名清单
$Domains = @(
    'github.com', 'api.github.com', 'codeload.github.com', 'gist.github.com',
    'raw.githubusercontent.com', 'gist.githubusercontent.com', 'objects.githubusercontent.com',
    'avatars.githubusercontent.com', 'camo.githubusercontent.com', 'user-images.githubusercontent.com',
    'cloud.githubusercontent.com', 'desktop.githubusercontent.com'
)
$FastlyPool = @('185.199.108.133', '185.199.109.133', '185.199.110.133', '185.199.111.133')
$GhPool = @('140.82.112.3', '140.82.112.4', '140.82.113.3', '140.82.113.4', '140.82.114.3', '140.82.114.4',
    '140.82.116.3', '140.82.116.4', '140.82.121.3', '140.82.121.4', '140.82.122.3', '140.82.122.4',
    '20.205.243.166', '20.205.243.168', '20.27.177.113', '20.200.245.247')
$Mirrors = @('https://ghfast.top', 'https://gh-proxy.com', 'https://ghproxy.net', 'https://ghproxy.cc',
    'https://github.moeyy.xyz', 'https://ghps.cc')
$MirrorTestPath = '/https://raw.githubusercontent.com/521xueweihan/GitHub520/main/hosts'
$DohServers = @('https://223.5.5.5/resolve', 'https://1.12.12.12/resolve')

# 沙箱代理检测（v1.1.0）: WorkBuddy 等 Agent 的沙箱会注入 127.0.0.1 本地代理，
# git/curl 默认服从该代理，可能导致"hosts 修好了但沙箱里 Agent 的 git 仍不通"
$SandboxProxy = $null
foreach ($pname in @('HTTPS_PROXY', 'https_proxy', 'HTTP_PROXY', 'http_proxy')) {
    $pv = [Environment]::GetEnvironmentVariable($pname)
    if ($pv -match '^http://(127\.0\.0\.1|localhost):\d+$') { $SandboxProxy = $pv; break }
}

function Write-Hr { Write-Host ('-' * 60) }

function Test-HttpOk {
    param([string]$Url, [string]$Resolve = $null, [int]$Sec = $TimeoutSec)
    # 优先系统 curl（Windows: System32\curl.exe；macOS/Linux: PATH 中的 curl），否则降级 IWR
    $curlExe = $null
    if ($env:SystemRoot -and (Test-Path (Join-Path $env:SystemRoot 'System32\curl.exe'))) {
        $curlExe = Join-Path $env:SystemRoot 'System32\curl.exe'
    }
    elseif (Get-Command curl -ErrorAction SilentlyContinue) { $curlExe = 'curl' }
    if ($curlExe) {
        $devNull = if ($env:SystemRoot) { 'NUL' } else { '/dev/null' }
        $args2 = @('--noproxy', '*', '-s', '-A', $UA, '-m', $Sec, '-o', $devNull, '-w', '%{http_code}')
        if ($Resolve) { $args2 += @('--resolve', $Resolve) }
        $args2 += $Url
        $code = & $curlExe @args2 2>$null
        return ($code -and $code -ne '000' -and $code -match '^[234]')
    }
    else {
        try {
            $prev = [System.Net.WebRequest]::DefaultWebProxy
            [System.Net.WebRequest]::DefaultWebProxy = $null
            $resp = Invoke-WebRequest -Uri $Url -TimeoutSec $Sec -UseBasicParsing -DisableKeepAlive -UserAgent $UA -ErrorAction Stop
            [System.Net.WebRequest]::DefaultWebProxy = $prev
            return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
        }
        catch {
            [System.Net.WebRequest]::DefaultWebProxy = $prev
            if ($_.Exception.Response) {
                $sc = [int]$_.Exception.Response.StatusCode
                return ($sc -ge 200 -and $sc -lt 500)
            }
            return $false
        }
    }
}

function Test-DomainOk { param([string]$Domain) Test-HttpOk -Url "https://$Domain/" }

# 经指定代理测试（模拟 Agent 沙箱默认路径）
function Test-HttpOkVia {
    param([string]$Url, [string]$Proxy, [int]$Sec = $TimeoutSec)
    $curlExe = $null
    if ($env:SystemRoot -and (Test-Path (Join-Path $env:SystemRoot 'System32\curl.exe'))) {
        $curlExe = Join-Path $env:SystemRoot 'System32\curl.exe'
    }
    elseif (Get-Command curl -ErrorAction SilentlyContinue) { $curlExe = 'curl' }
    if ($curlExe) {
        $devNull = if ($env:SystemRoot) { 'NUL' } else { '/dev/null' }
        $code = & $curlExe -s -x $Proxy -A $UA -m $Sec -o $devNull -w '%{http_code}' $Url 2>$null
        return ($code -and $code -ne '000' -and $code -match '^[234]')
    }
    else {
        try {
            $resp = Invoke-WebRequest -Uri $Url -Proxy $Proxy -TimeoutSec $Sec -UseBasicParsing -DisableKeepAlive -UserAgent $UA -ErrorAction Stop
            return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
        }
        catch {
            if ($_.Exception.Response) {
                $sc = [int]$_.Exception.Response.StatusCode
                return ($sc -ge 200 -and $sc -lt 500)
            }
            return $false
        }
    }
}

function Test-IpOk {
    param([string]$Domain, [string]$Ip)
    Test-HttpOk -Url "https://$Domain/" -Resolve "$Domain`:443:$Ip"
}

function Test-TcpPort {
    param([string]$Host2, [int]$Port)
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($Host2, $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(5000, $false) -and $client.Connected) { $client.Close(); return $true }
        $client.Close(); return $false
    }
    catch { return $false }
}

function Get-Candidates {
    param([string]$Domain)
    $ips = @()
    # 源1: GitHub520 国内直连源
    try {
        $txt = (Invoke-WebRequest -Uri 'https://raw.hellogithub.com/hosts' -TimeoutSec 10 -UseBasicParsing -UserAgent $UA -Proxy $null).Content
        $m = [regex]::Match($txt, "(?m)^([0-9a-fA-F.:]+)\s+$([regex]::Escape($Domain))\s")
        if ($m.Success) { $ips += $m.Groups[1].Value }
    }
    catch {}
    # 源2: DoH（阿里 / DNSPod，绕过本地 DNS 污染）
    foreach ($doh in $DohServers) {
        try {
            $j = Invoke-RestMethod -Uri "$doh`?name=$Domain&type=A" -TimeoutSec 6 -UserAgent $UA -Proxy $null
            foreach ($a in $j.Answer) { if ($a.data -match '^\d+\.\d+\.\d+\.\d+$' -and $ips -notcontains $a.data) { $ips += $a.data } }
        }
        catch {}
    }
    # 源3: 硬编码候选池
    if ($Domain -like '*githubusercontent.com' -or $Domain -like '*.github.io') { $pool = $FastlyPool } else { $pool = $GhPool }
    foreach ($ip in $pool) { if ($ips -notcontains $ip) { $ips += $ip } }
    return $ips
}

function Find-Ip {
    param([string]$Domain)
    foreach ($ip in (Get-Candidates -Domain $Domain)) {
        if (Test-IpOk -Domain $Domain -Ip $ip) { return $ip }
    }
    return $null
}

function Find-Mirror {
    foreach ($m in $Mirrors) {
        if (Test-HttpOk -Url "$m$MirrorTestPath" -Sec 10) { return $m }
    }
    return $null
}

function Get-IsAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Backup-Hosts {
    $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
    $bak = "$HostsPath.ghacc.bak.$ts"
    Copy-Item -Path $HostsPath -Destination $bak -Force
    Write-Host "已备份: $bak"
    Get-ChildItem "$HostsPath.ghacc.bak.*" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip $BackupKeep | Remove-Item -Force -ErrorAction SilentlyContinue
}

# ============ check ============
function Invoke-Check {
    Write-Host "GitHub-Accelerator v$Version —— 诊断报告（只读，未修改任何系统配置）"
    Write-Host ("时间: {0:yyyy-MM-dd HH:mm:ss}" -f (Get-Date))
    Write-Hr
    Write-Host '[1/4] 域名直连状态'
    $bad = @()
    foreach ($d in $Domains) {
        if (Test-DomainOk -Domain $d) { Write-Host ("  [OK] {0,-45} 直连正常" -f $d) }
        else { Write-Host ("  [X ] {0,-45} 直连失败" -f $d); $bad += $d }
    }
    Write-Hr
    # [1.5/4] Agent 沙箱环境双路检测（v1.1.0）
    if ($SandboxProxy) {
        Write-Host "[1.5/4] Agent 沙箱环境检测（检测到注入代理: $SandboxProxy）"
        if (Test-HttpOkVia -Url 'https://github.com/' -Proxy $SandboxProxy) {
            Write-Host '  [OK] 沙箱代理路径: github.com 可通 -> Agent 自己的 git/curl 可保留默认环境'
        }
        else {
            Write-Host '  [X ] 沙箱代理路径: github.com 不通（沙箱代理拦截 GitHub，Agent 默认路径会失败）'
            if (Test-DomainOk -Domain 'github.com') {
                Write-Host '  对策: Agent 自己的 git/curl 操作前先清除代理环境变量:'
                Write-Host '    $env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:http_proxy=""; $env:https_proxy=""'
                Write-Host '  若 git 报证书吊销检查失败(0x80092012)，追加: git -c http.schannelCheckRevoke=false clone ...'
            }
            else { Write-Host '  直连与代理路径均不通 -> 继续看下方 hosts/镜像修复' }
        }
        Write-Host '  注: hosts 修复对两条路径都生效（沙箱代理进程也使用本机系统解析）'
        Write-Hr
    }
    Write-Host '[2/4] SSH 通道'
    if (Test-TcpPort -Host2 'github.com' -Port 22) { Write-Host '  [OK] git@github.com:22 可通' } else { Write-Host '  [X ] 22 端口不通' }
    if (Test-TcpPort -Host2 'ssh.github.com' -Port 443) { Write-Host '  [OK] ssh.github.com:443 可通（SSH-over-443 兜底可用）' } else { Write-Host '  [X ] ssh.github.com:443 不通' }
    Write-Hr
    Write-Host '[3/4] 为失败域名寻找可用 IP（逐条实测，可能需要 1-2 分钟）'
    foreach ($d in $bad) {
        $ip = Find-Ip -Domain $d
        if ($ip) { Write-Host ("  [OK] {0,-45} 可修复 -> {1}" -f $d, $ip) }
        else { Write-Host ("  [X ] {0,-45} 所有候选 IP 均不通" -f $d) }
    }
    Write-Hr
    Write-Host '[4/4] 镜像通道'
    $m = Find-Mirror
    if ($m) { Write-Host "  [OK] 可用镜像: $m" } else { Write-Host '  [X ] 所有镜像均不可用' }
    Write-Hr
    if ($bad.Count -eq 0) { Write-Host '结论: 所有域名直连正常，无需任何修复。' }
    else { Write-Host "结论: $($bad -join ', ') 存在问题；执行 apply 前请用户确认。" }
}

# ============ apply ============
function Invoke-Apply {
    if (-not (Get-IsAdmin)) {
        # 自提权重启（弹 UAC）
        $p = Start-Process powershell -Verb RunAs -PassThru -Wait -ArgumentList @(
            '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", 'apply'
        )
        exit $p.ExitCode
    }
    if (-not (Test-Path $HostsPath)) { Set-Content -Path $HostsPath -Value '' -Encoding ASCII }
    Backup-Hosts

    $content = Get-Content -Path $HostsPath -Raw -ErrorAction SilentlyContinue
    if ($null -eq $content) { $content = '' }
    # 移除旧标记块
    $pattern = "(?ms)\r?\n?" + [regex]::Escape($MarkStart) + '.*?' + [regex]::Escape($MarkEnd) + "\r?\n?"
    $content = [regex]::Replace($content, $pattern, "`n")

    # 构建新块（v1.0.2: 每个域名最多 3 个实测通过的 IP，多行=系统自动备胎）
    $block = New-Object System.Collections.Generic.List[string]
    $block.Add($MarkStart)
    $block.Add("# 由 github-accelerator v$Version 于 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 写入")
    $block.Add('# 每个域名最多 3 个本机实测通过的 IP（多行=自动备胎）；重复运行会覆盖更新此块')
    foreach ($d in $Domains) {
        $count = 0
        foreach ($ip in (Get-Candidates -Domain $d)) {
            if ($count -ge 3) { break }
            if (Test-IpOk -Domain $d -Ip $ip) {
                $block.Add(('{0,-16} {1}' -f $ip, $d))
                $count++
            }
        }
    }
    if (-not (Test-TcpPort -Host2 'github.com' -Port 22) -and (Test-TcpPort -Host2 'ssh.github.com' -Port 443)) {
        foreach ($ip in (Get-Candidates -Domain 'ssh.github.com')) {
            if (Test-IpOk -Domain 'ssh.github.com' -Ip $ip) { $block.Add(('{0,-16} {1}' -f $ip, 'ssh.github.com')); break }
        }
    }
    $block.Add($MarkEnd)

    $newContent = $content.TrimEnd() + "`r`n" + ($block -join "`r`n") + "`r`n"
    Set-Content -Path $HostsPath -Value $newContent -Encoding ASCII
    Write-Host 'hosts 已更新（仅标记块内容，其余未动）。'

    # 刷新 DNS
    ipconfig /flushdns | Out-Null
    Write-Host 'DNS 缓存已刷新。'

    # git insteadOf 镜像兜底：仅当 github.com 直连失败且存在可用镜像
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if (Test-DomainOk -Domain 'github.com') {
        Write-Host 'github.com 直连正常，跳过 git 镜像配置（不引入不必要的第三方中转）。'
    }
    elseif ($gitCmd) {
        $m = Find-Mirror
        if ($m) {
            & git config --global "url.$m/https://github.com/.insteadOf" 'https://github.com/'
            & git config --global 'github-accelerator.mirror' $m
            Write-Host "git 已配置镜像加速: $m"
        }
        else { Write-Host '警告: github.com 直连失败且所有镜像不可用，git 兜底未能配置。' }
    }

    Write-Hr
    Write-Host '端到端验证（写入后实测）:'
    $fail = 0
    foreach ($d in $Domains) {
        if (Test-DomainOk -Domain $d) { Write-Host "  [OK] $d" }
        else { Write-Host "  [X ] $d （hosts 已写入但仍不通，见诊断报告的出路指引）"; $fail++ }
    }
    if ($fail -eq 0) { Write-Host '全部域名修复成功 [OK]' } else { Write-Host '部分域名未能修复，详见上方报告。' }
    # 沙箱环境提示（v1.1.0）
    if ($SandboxProxy -and -not (Test-HttpOkVia -Url 'https://github.com/' -Proxy $SandboxProxy) -and (Test-DomainOk -Domain 'github.com')) {
        Write-Hr
        Write-Host '沙箱提示: 检测到 Agent 沙箱代理拦截 GitHub（直连已修复）。'
        Write-Host 'Agent 后续执行 git clone / curl / 下载安装前先清除代理环境变量:'
        Write-Host '  $env:HTTP_PROXY=""; $env:HTTPS_PROXY=""; $env:http_proxy=""; $env:https_proxy=""'
        Write-Host '若 git 报证书吊销检查失败(0x80092012)，追加: git -c http.schannelCheckRevoke=false clone ...'
    }
}

# ============ rollback ============
function Invoke-Rollback {
    if (-not (Get-IsAdmin)) {
        $p = Start-Process powershell -Verb RunAs -PassThru -Wait -ArgumentList @(
            '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", 'rollback'
        )
        exit $p.ExitCode
    }
    $baks = Get-ChildItem "$HostsPath.ghacc.bak.*" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending
    if ($baks) {
        Copy-Item -Path $baks[0].FullName -Destination $HostsPath -Force
        Write-Host "hosts 已还原自: $($baks[0].Name)"
        ipconfig /flushdns | Out-Null
    }
    else {
        $content = Get-Content -Path $HostsPath -Raw
        $pattern = "(?ms)\r?\n?" + [regex]::Escape($MarkStart) + '.*?' + [regex]::Escape($MarkEnd) + "\r?\n?"
        Set-Content -Path $HostsPath -Value ([regex]::Replace($content, $pattern, "`n")) -Encoding ASCII
        Write-Host '未找到备份，已移除标记块内容。'
    }
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if ($gitCmd) {
        $m = & git config --global --get github-accelerator.mirror 2>$null
        if ($m) {
            & git config --global --unset "url.$m/https://github.com/.insteadOf" 2>$null
            & git config --global --unset github-accelerator.mirror 2>$null
            Write-Host 'git 镜像配置已移除。'
        }
    }
    Write-Host '回滚完成。'
}

# ============ status ============
function Invoke-Status {
    Write-Host "GitHub-Accelerator v$Version —— 当前状态"
    Write-Hr
    if (Test-Path $HostsPath) {
        $content = Get-Content -Path $HostsPath -Raw
        if ($content -match [regex]::Escape($MarkStart)) {
            $s = $content.IndexOf($MarkStart); $e = $content.IndexOf($MarkEnd) + $MarkEnd.Length
            Write-Host 'hosts 标记块内容:'
            $content.Substring($s, $e - $s) -split "`r?`n" | ForEach-Object { Write-Host "  $_" }
        }
        else { Write-Host 'hosts 中无本工具标记块（未安装或已回滚）。' }
    }
    Write-Hr
    Invoke-Check
}

switch ($Cmd) {
    'check' { Invoke-Check }
    'apply' { Invoke-Apply }
    'rollback' { Invoke-Rollback }
    'status' { Invoke-Status }
}
