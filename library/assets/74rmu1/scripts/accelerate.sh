#!/usr/bin/env bash
# ============================================================
# github-accelerator v1.1.0 —— 诊断驱动的小白 GitHub 加速器 (macOS / Linux)
# 纯 bash 零依赖（兼容 macOS 自带 bash 3.2）
#
# 子命令:
#   check     只诊断不修改（无需管理员权限）
#   apply     诊断 + 写入 hosts + git/SSH 兜底（需 root；授权弹窗由 Agent 负责触发）
#   rollback  回滚到最近的备份并清理 git/SSH 配置（hosts 部分需 root）
#   status    查看当前配置与连通性
#
# 设计原则:
#   1. 什么都不预设——每个域名、每个 IP、每个镜像都在用户本机运行时实测
#   2. 只写入实测通过的 hosts 条目（不盲信任何上游源）
#   3. hosts 修改仅限标记块内，绝不触碰用户其他内容；写入前自动备份（保留 3 份）
#   4. 全灭时诚实报告死因，不假装修复成功
#
# 测试钩子: 环境变量 HOSTS_FILE 可重定向 hosts 路径（供 dry-run 测试用）
# ============================================================

VERSION="1.1.0"
MARK_START="# GitHub-Accelerator Start"
MARK_END="# GitHub-Accelerator End"
MARK_SSH_START="# GitHub-Accelerator SSH Start"
MARK_SSH_END="# GitHub-Accelerator SSH End"
HOSTS_FILE="${HOSTS_FILE:-/etc/hosts}"
BACKUP_KEEP=3
UA="Mozilla/5.0 (compatible; GitHub-Accelerator/$VERSION)"
CURL_TIMEOUT=6
LOCK_DIR="${TMPDIR:-/tmp}/github-accelerator.lock"

# 关键: 切到 / 再干活。macOS 上 osascript 以 root 运行时，若 cwd 位于
# TCC 保护区(如 ~/Desktop)，git getcwd 都会 fatal，相对路径操作也不可靠
cd /

# 单实例锁: 防止并发 apply/rollback 交错写 hosts 导致内容丢失
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  # 超过 10 分钟的锁视为僵尸锁，清理后重试
  if [ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +10 2>/dev/null)" ] && rmdir "$LOCK_DIR" 2>/dev/null && mkdir "$LOCK_DIR" 2>/dev/null; then
    :
  else
    printf '%s\n' "错误: 另一个实例正在运行，请稍后再试"
    exit 1
  fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null' EXIT

# 需要修复的域名清单（GitHub 核心系）
DOMAINS="github.com api.github.com codeload.github.com gist.github.com raw.githubusercontent.com gist.githubusercontent.com objects.githubusercontent.com avatars.githubusercontent.com camo.githubusercontent.com user-images.githubusercontent.com cloud.githubusercontent.com desktop.githubusercontent.com"
# Fastly 系（.133 四胞胎大概率可用）
FASTLY_POOL="185.199.108.133 185.199.109.133 185.199.110.133 185.199.111.133"
# GitHub 官方已知网段候选（github.com / api / codeload 等）
GH_POOL="140.82.112.3 140.82.112.4 140.82.113.3 140.82.113.4 140.82.114.3 140.82.114.4 140.82.116.3 140.82.116.4 140.82.121.3 140.82.121.4 140.82.122.3 140.82.122.4 20.205.243.166 20.205.243.168 20.27.177.113 20.200.245.247"
# 镜像候选（URL 前缀拼接风格统一为: 镜像/完整原始URL）
MIRRORS="https://ghfast.top https://gh-proxy.com https://ghproxy.net https://ghproxy.cc https://github.moeyy.xyz https://ghps.cc"
# 镜像测速用的真实小文件（约 2.5KB）
MIRROR_TEST_PATH="/https://raw.githubusercontent.com/521xueweihan/GitHub520/main/hosts"
# DoH 服务器（国内必可达，绕过本地 DNS 污染拿真实 IP）
DOH_A="https://223.5.5.5/resolve"
DOH_B="https://1.12.12.12/resolve"

c() { curl --noproxy '*' -s -A "$UA" "$@"; }

# 沙箱代理检测: WorkBuddy 等 Agent 的 Bash 沙箱会向命令注入 127.0.0.1 本地代理，
# git/curl 等默认服从该代理，可能导致"hosts 修好了但沙箱里 Agent 的 git 仍不通"
SANDBOX_PROXY=""
for _pv in https_proxy HTTPS_PROXY http_proxy HTTP_PROXY; do
  _pval=$(printenv "$_pv" 2>/dev/null)
  case "$_pval" in
    http://127.0.0.1:*|http://localhost:*) SANDBOX_PROXY="$_pval"; break ;;
  esac
done

# 经指定代理测试域名（模拟 Agent 默认路径）
domain_ok_via() {
  local code
  code=$(curl -s -x "$2" -A "$UA" -m "$CURL_TIMEOUT" -o /dev/null -w '%{http_code}' "https://$1/" 2>/dev/null)
  [ -n "$code" ] && [ "$code" != "000" ]
}

log()  { printf '%s\n' "$*"; }
hr()   { printf '%s\n' "------------------------------------------------------------"; }

# 域名直连测试: 返回 0 = 可达（任何 2xx/3xx/4xx HTTP 响应都算活着）
# 用法: domain_ok <domain>
domain_ok() {
  local code
  code=$(c -m "$CURL_TIMEOUT" -o /dev/null -w '%{http_code}' "https://$1/" 2>/dev/null)
  [ -n "$code" ] && [ "$code" != "000" ]
}

# 指定 IP 测试域名: 用 --resolve 模拟 hosts，不动系统
# 用法: ip_ok <domain> <ip>
ip_ok() {
  local code
  code=$(c -m "$CURL_TIMEOUT" -o /dev/null -w '%{http_code}' --resolve "$1:443:$2" "https://$1/" 2>/dev/null)
  [ -n "$code" ] && [ "$code" != "000" ]
}

# TCP 端口测试
tcp_ok() {
  nc -z -G 5 "$1" "$2" >/dev/null 2>&1
}

# 多源拉取某域名的候选 IP（去重）:
#   源1: GitHub520 hosts（国内直连源）
#   源2: 阿里 DoH / DNSPod DoH（绕过本地 DNS 污染）
#   源3: 硬编码候选池（按域名归属选池）
# 用法: candidates_for <domain>
candidates_for() {
  local d="$1" pool="" line ips=""
  # 源1: GitHub520
  line=$(c -m 10 "https://raw.hellogithub.com/hosts" 2>/dev/null | grep -E "^[0-9a-fA-F.:]+[[:space:]]+$d([[:space:]]|\$)" | head -1 | awk '{print $1}')
  [ -n "$line" ] && ips="$line"
  # 源2: DoH（两家，哪家活着用哪家）
  for doh in "$DOH_A" "$DOH_B"; do
    local ans
    ans=$(c -m 6 "$doh?name=$d&type=A" 2>/dev/null | grep -o '"data":"[0-9.]*"' | cut -d'"' -f4)
    for ip in $ans; do
      case " $ips " in *" $ip "*) ;; *) ips="$ips $ip";; esac
    done
  done
  # 源3: 硬编码池
  case "$d" in
    *githubusercontent.com|*.github.io) pool="$FASTLY_POOL" ;;
    *) pool="$GH_POOL" ;;
  esac
  for ip in $pool; do
    case " $ips " in *" $ip "*) ;; *) ips="$ips $ip";; esac
  done
  echo $ips
}

# 为域名找到第一个实测可用 IP
# 用法: find_ip <domain>  → 输出 IP 或空
find_ip() {
  local d="$1" ip
  for ip in $(candidates_for "$d"); do
    if ip_ok "$d" "$ip"; then
      echo "$ip"
      return 0
    fi
  done
  return 1
}

# 收集某域名最多 N 个实测可用 IP（多 IP 冗余，抗线路抖动）
# 用法: find_ips <domain> <max>  → 每行一个 IP
find_ips() {
  local d="$1" max="$2" ip n=0
  for ip in $(candidates_for "$d"); do
    if ip_ok "$d" "$ip"; then
      echo "$ip"
      n=$((n+1))
      [ "$n" -ge "$max" ] && return 0
    fi
  done
  return 0
}

# 镜像竞速: 返回第一个实测可用的镜像 URL（按列表顺序=按预期可靠度）
# 用法: find_mirror → 输出镜像 base URL 或空
find_mirror() {
  local m code
  for m in $MIRRORS; do
    code=$(c -m 10 -o /dev/null -w '%{http_code}' "$m$MIRROR_TEST_PATH" 2>/dev/null)
    if [ "$code" = "200" ]; then
      echo "$m"
      return 0
    fi
  done
  return 1
}

# ============ check（只诊断，无需 root）============
do_check() {
  log "GitHub-Accelerator v$VERSION —— 诊断报告（只读，未修改任何系统配置）"
  log "时间: $(date '+%F %T')    主机: $(uname -s) $(uname -m)"
  hr
  log "[1/4] 域名直连状态"
  local d ok_list="" bad_list="" pass ip
  for d in $DOMAINS; do
    if domain_ok "$d"; then
      ok_list="$ok_list $d"
      printf '  ✓ %-45s 直连正常\n' "$d"
    else
      bad_list="$bad_list $d"
      printf '  ✗ %-45s 直连失败\n' "$d"
    fi
  done
  hr
  # [1.5/4] Agent 沙箱环境双路检测（v1.1.0 新增）
  if [ -n "$SANDBOX_PROXY" ]; then
    log "[1.5/4] Agent 沙箱环境检测（检测到注入代理: $SANDBOX_PROXY）"
    if domain_ok_via github.com "$SANDBOX_PROXY"; then
      log "  · 沙箱代理路径: github.com 可通 → Agent 自己的 git/curl 可保留默认环境"
    else
      log "  · 沙箱代理路径: github.com 不通（沙箱代理拦截 GitHub，Agent 默认路径会失败）"
      if domain_ok github.com; then
        log "  · 对策: Agent 自己的 git/curl 操作必须绕开代理，命令前缀:"
        log "    env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY <命令>"
      else
        log "  · 直连与代理路径均不通 → 继续看下方 hosts/镜像修复"
      fi
    fi
    log "  · 注: hosts 修复对两条路径都生效（沙箱代理进程也使用本机系统解析）"
    hr
  fi
  log "[2/4] SSH 通道"
  if tcp_ok github.com 22; then log "  ✓ git@github.com:22 可通"; else log "  ✗ 22 端口不通"; fi
  if tcp_ok ssh.github.com 443; then log "  ✓ ssh.github.com:443 可通（SSH-over-443 兜底可用）"; else log "  ✗ ssh.github.com:443 不通"; fi
  hr
  log "[3/4] 为失败域名寻找可用 IP（逐条实测，可能需要 1-2 分钟）"
  local fixable="" unfixable=""
  for d in $bad_list; do
    pass=""
    if ip=$(find_ip "$d"); then
      fixable="$fixable $d:$ip"
      printf '  ✓ %-45s 可修复 → %s\n' "$d" "$ip"
    else
      unfixable="$unfixable $d"
      printf '  ✗ %-45s 所有候选 IP 均不通\n' "$d"
    fi
  done
  hr
  log "[4/4] 镜像通道 + git 兜底判断"
  local mirror
  if mirror=$(find_mirror); then
    log "  ✓ 可用镜像: $mirror"
  else
    log "  ✗ 所有镜像均不可用"
  fi
  local insteadof_val
  insteadof_val=$(git config --global --get-regexp 'url\..*\.insteadof' 2>/dev/null || true)
  if [ -n "$insteadof_val" ]; then
    log "  - git insteadOf: 已配置"
  else
    log "  - git insteadOf: 未配置"
  fi
  if [ -f "$HOME/.ssh/config" ] && grep -q "$MARK_SSH_START" "$HOME/.ssh/config" 2>/dev/null; then
    log "  - SSH-over-443 兜底: 已配置"
  else
    log "  - SSH-over-443 兜底: 未配置"
  fi
  hr
  # 结论
  if [ -z "$bad_list" ]; then
    log "结论: 所有域名直连正常，无需任何修复。"
    return 0
  fi
  log "结论:"
  [ -n "$fixable" ] && log "  · ${fixable} 中的失败域名可通过写入 hosts 修复"
  [ -n "$unfixable" ] && log "  · $unfixable 无法通过 hosts 修复（候选 IP 全灭）"
  log "执行 apply 前请用户确认。完整命令由 Agent 按引导执行。"
  return 0
}

# ============ hosts 标记块构建 ============
# v1.0.2: 对所有域名（不只失败域名）固定最多 3 个实测通过的 IP。
# 理由: 国内对 GitHub 的阻断/丢包是间歇性抖动的，直连此刻正常不代表 5 分钟后正常；
# hosts 同域名写多行时系统会依次尝试，形成自动备胎链。
MAX_IPS_PER_DOMAIN=3
build_block() {
  local d ip
  echo "$MARK_START"
  echo "# 由 github-accelerator v$VERSION 于 $(date '+%F %T') 写入"
  echo "# 每个域名最多 $MAX_IPS_PER_DOMAIN 个本机实测通过的 IP（多行=自动备胎）；重复运行会覆盖更新此块"
  for d in $DOMAINS; do
    for ip in $(find_ips "$d" "$MAX_IPS_PER_DOMAIN"); do
      printf '%-16s %s\n' "$ip" "$d"
    done
  done
  # ssh.github.com 兜底条目（22 被封但 443 活着时需要稳定解析）
  if ! tcp_ok github.com 22 && tcp_ok ssh.github.com 443; then
    for ip in $(find_ips "ssh.github.com" 1); do
      printf '%-16s %s\n' "$ip" "ssh.github.com"
    done
  fi
  echo "$MARK_END"
}

backup_hosts() {
  local ts bak
  ts=$(date +%Y%m%d-%H%M%S)
  bak="$HOSTS_FILE.ghacc.bak.$ts"
  cp -p "$HOSTS_FILE" "$bak" 2>/dev/null || { log "错误: 无法创建备份 $bak"; return 1; }
  log "已备份: $bak"
  # 只保留最近 BACKUP_KEEP 份
  ls -t "$HOSTS_FILE".ghacc.bak.* 2>/dev/null | tail -n +$((BACKUP_KEEP+1)) | while read -r old; do rm -f "$old"; done
  return 0
}

# ============ apply（需 root 或 HOSTS_FILE 重定向测试）============
do_apply() {
  if [ "$(id -u)" -ne 0 ] && [ "$HOSTS_FILE" = "/etc/hosts" ]; then
    log "错误: apply 需要管理员权限（root）。请通过授权弹窗或 sudo 运行。"
    exit 1
  fi
  [ -f "$HOSTS_FILE" ] || touch "$HOSTS_FILE"
  backup_hosts || exit 1

  # 生成新 hosts: 去掉旧标记块 + 追加新块（含内容保护，绝不丢用户原有内容）
  local orig_size tmp
  orig_size=$(wc -c < "$HOSTS_FILE" 2>/dev/null | tr -d ' ' || echo 0)
  tmp=$(mktemp) || { log "错误: 无法创建临时文件"; exit 1; }
  awk -v s="$MARK_START" -v e="$MARK_END" '
    index($0, s) == 1 { inblk=1; next }
    index($0, e) == 1 { inblk=0; next }
    !inblk { print }
  ' "$HOSTS_FILE" > "$tmp" 2>/dev/null
  # 内容保护: 原文件非空但 awk 输出为空 → 读取异常，拒绝写入
  if [ "$orig_size" -gt 0 ] && [ ! -s "$tmp" ]; then
    rm -f "$tmp"
    log "错误: 读取原 hosts 内容异常，已中止写入（原文件未动）"
    exit 1
  fi
  build_block >> "$tmp"
  cp -f "$tmp" "$HOSTS_FILE"
  rm -f "$tmp"
  log "hosts 已更新（仅标记块内容，其余未动）。"

  # 刷新 DNS 缓存
  dscacheutil -flushcache 2>/dev/null
  killall -HUP mDNSResponder 2>/dev/null || true
  log "DNS 缓存已刷新。"

  # SSH-over-443 兜底（22 被封 && ssh.github.com:443 活着时才配置）
  if ! tcp_ok github.com 22 && tcp_ok ssh.github.com 443; then
    local sshcfg="$HOME/.ssh/config"
    mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
    if [ -f "$sshcfg" ] && ! grep -q "$MARK_SSH_START" "$sshcfg" 2>/dev/null; then
      cp -p "$sshcfg" "$sshcfg.ghacc.bak" 2>/dev/null
    fi
    if ! grep -q "$MARK_SSH_START" "$sshcfg" 2>/dev/null; then
      {
        echo "$MARK_SSH_START"
        echo "Host github.com"
        echo "  Hostname ssh.github.com"
        echo "  Port 443"
        echo "  User git"
        echo "$MARK_SSH_END"
      } >> "$sshcfg"
      log "已启用 SSH-over-443 兜底（22 端口被封时的 git push/pull 通道）。"
    fi
  fi

  # git insteadOf 镜像兜底：仅当 github.com 直连仍失败且存在可用镜像时写入
  if domain_ok github.com; then
    log "github.com 直连正常，跳过 git 镜像配置（不引入不必要的第三方中转）。"
  else
    local mirror
    if mirror=$(find_mirror); then
      git config --global "url.$mirror/https://github.com/.insteadOf" "https://github.com/"
      git config --global "github-accelerator.mirror" "$mirror"
      log "git 已配置镜像加速: $mirror（clone/pull 自动走镜像；如需关闭运行 rollback 或 --no-git）"
    else
      log "警告: github.com 直连失败且所有镜像不可用，git 镜像兜底未能配置。"
    fi
  fi

  # 端到端验证
  hr
  log "端到端验证（写入后实测）:"
  local d fail=0
  for d in $DOMAINS; do
    if domain_ok "$d"; then
      printf '  ✓ %s\n' "$d"
    else
      printf '  ✗ %s（hosts 已写入但仍不通，见诊断报告的出路指引）\n' "$d"
      fail=1
    fi
  done
  if [ "$fail" -eq 0 ]; then log "全部域名修复成功 ✓"; else log "部分域名未能修复，详见上方报告。"; fi
  # 沙箱环境提示（v1.1.0）: hosts 修好了但 Agent 沙箱代理仍拦 GitHub 的情况
  if [ -n "$SANDBOX_PROXY" ] && ! domain_ok_via github.com "$SANDBOX_PROXY" && domain_ok github.com; then
    hr
    log "沙箱提示: 检测到 Agent 沙箱代理拦截 GitHub（直连已修复）。"
    log "Agent 后续执行 git clone / curl / 下载安装时务必绕开代理，命令前缀:"
    log "  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY <命令>"
  fi
  return $fail
}

# ============ rollback ============
do_rollback() {
  # hosts 还原
  if [ "$(id -u)" -eq 0 ] || [ "$HOSTS_FILE" != "/etc/hosts" ]; then
    local latest
    latest=$(ls -t "$HOSTS_FILE".ghacc.bak.* 2>/dev/null | head -1)
    if [ -n "$latest" ]; then
      cp -p "$latest" "$HOSTS_FILE"
      log "hosts 已还原自: $latest"
      dscacheutil -flushcache 2>/dev/null
      killall -HUP mDNSResponder 2>/dev/null || true
    else
      # 无备份则仅移除标记块
      local tmp; tmp=$(mktemp) || { log "错误: 无法创建临时文件"; return 1; }
      awk -v s="$MARK_START" -v e="$MARK_END" '
        index($0, s) == 1 { inblk=1; next }
        index($0, e) == 1 { inblk=0; next }
        !inblk { print }
      ' "$HOSTS_FILE" > "$tmp" 2>/dev/null && [ -s "$tmp" ] && cp -f "$tmp" "$HOSTS_FILE" && rm -f "$tmp"
      log "未找到备份，已移除标记块内容。"
    fi
  fi
  # git 清理
  local mirror
  mirror=$(git config --global --get github-accelerator.mirror 2>/dev/null)
  if [ -n "$mirror" ]; then
    git config --global --unset "url.$mirror/https://github.com/.insteadOf" 2>/dev/null
    git config --global --unset github-accelerator.mirror 2>/dev/null
    log "git 镜像配置已移除。"
  fi
  # ssh 配置清理
  local sshcfg="$HOME/.ssh/config"
  if [ -f "$sshcfg" ] && grep -q "$MARK_SSH_START" "$sshcfg" 2>/dev/null; then
    local tmp; tmp=$(mktemp)
    awk -v s="$MARK_SSH_START" -v e="$MARK_SSH_END" '
      index($0, s) == 1 { inblk=1; next }
      index($0, e) == 1 { inblk=0; next }
      !inblk { print }
    ' "$sshcfg" > "$tmp" && cp "$tmp" "$sshcfg" && rm -f "$tmp"
    log "SSH-over-443 兜底配置已移除。"
  fi
  log "回滚完成。"
}

# ============ status ============
do_status() {
  log "GitHub-Accelerator v$VERSION —— 当前状态"
  hr
  if [ -f "$HOSTS_FILE" ] && grep -q "$MARK_START" "$HOSTS_FILE" 2>/dev/null; then
    log "hosts 标记块内容:"
    awk -v s="$MARK_START" -v e="$MARK_END" 'index($0,s)==1{p=1} p{print} index($0,e)==1{exit}' "$HOSTS_FILE" | sed 's/^/  /'
  else
    log "hosts 中无本工具标记块（未安装或已回滚）。"
  fi
  hr
  do_check
}

case "${1:-check}" in
  check)    do_check ;;
  apply)    do_apply ;;
  rollback) do_rollback ;;
  status)   do_status ;;
  *) echo "用法: $0 {check|apply|rollback|status}"; exit 2 ;;
esac
