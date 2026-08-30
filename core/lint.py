"""Skill 质量门控（lint）。

依据 Anthropic Agent Skills 规范（name 须匹配父目录名、description 为官方召回字段，
name ≤ 64 字符 / description ≤ 1024 字符 / body < 500 行）与 OWASP Agentic Skills
Top 10（权限最小化、网络域名白名单、身份文件默认保护）做静态检查。

在 discover 阶段对子 skill 执行，仅报告不阻断——已注册的 skill 不因 lint 失败被拒收。

安全扫描的设计约束（重要）：
lint 报告会被 AI 读进上下文。因此命中凭证时**只报告文件、行号、规则名，
绝不回显匹配到的具体内容**——否则扫描器本身就成了把密钥送进模型上下文的通道。
"""

import math
import os
import re

from core.frontmatter import parse_frontmatter

TRIGGER_HINTS = ("当", "如", "需", "查询", "用于", "遇到", "请求", "要", "请")

# OWASP Agentic Skills Top 10：身份文件默认受保护，写权限须显式授予。
IDENTITY_FILES = ("SOUL.md", "MEMORY.md", "AGENTS.md", "IDENTITY.md", "USER.md")

SCANNABLE_EXT = (
    ".py", ".js", ".ts", ".mjs", ".cjs", ".sh", ".bash", ".ps1",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".env", ".md", ".txt",
)
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", "dist", "build"}
# 测试目录整支跳过（凭证扫描与能力探测都跳）。
#
# 凭证扫描：测试夹具天然包含合成凭证（否则测不出检出能力），扫它只会产出一条
# 必然被人工忽略的 error——而一个总被忽略的 error 通道等于没有 error 通道。
# 能力探测：测试代码里 import requests / subprocess 不能证明 skill 运行时会出网或起
# 子进程，据此报警是误导。
#
# 需要"连测试文件一起扫"的 CI 级覆盖，请用 gitleaks（--no-git 全量）或
# TruffleHog（--only-verified），那才是为这个场景设计的工具。
TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "fixtures", "testdata"}
MAX_FILE_BYTES = 512 * 1024

# 凭证规则集（形态参照 gitleaks 内置规则）。每条 (code, 正则, 人类可读名称)。
# 只用于判定"这里有个疑似凭证"，不提取值。
SECRET_PATTERNS = [
    ("secret.openai_key", re.compile(r"sk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9]{20,}"), "OpenAI API key"),
    ("secret.anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"), "Anthropic API key"),
    ("secret.aws_akid", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key id"),
    ("secret.github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
    ("secret.slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "Slack token"),
    ("secret.google_api", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"), "Google API key"),
    ("secret.stripe_live", re.compile(r"\bsk_live_[0-9a-zA-Z]{24,}\b"), "Stripe live key"),
    ("secret.private_key", re.compile(r"-----BEGIN (?:RSA |OPENSSH |DSA |EC |PGP )?PRIVATE KEY-----"), "private key"),
    ("secret.jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "JWT"),
    ("secret.assigned", re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|secret[_-]?key|password|passwd|access[_-]?token|auth[_-]?token|client[_-]?secret)\b"
        r"\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"), "hardcoded credential assignment"),
]

# 占位符 / 环境变量引用：命中即视为非真实凭证，用于抑制误报。
PLACEHOLDER = re.compile(
    r"(?i)(example|placeholder|dummy|sample|your[_-]|<[a-z_]+>|xxx+|changeme|fake|"
    r"test[_-]?(key|token|secret)|redacted|\*{4,}|todo|insert[_-]?here|none|null|undefined)"
)
ENV_REF = re.compile(r"(?i)(os\.environ|getenv|process\.env|\$\{?[A-Z_][A-Z0-9_]*\}?|%[A-Z_]+%|vault|secrets?\.get)")

HIGH_ENTROPY = re.compile(r"[A-Za-z0-9+=]{32,}")
ENTROPY_THRESHOLD = 4.0

# 高熵字符集刻意不含 `_` / `-` / `/`。
#
# 含 `_` 时会把 snake_case 标识符整段吞成一个"凭证"——实测误报全部来自这里：
#   test_ip_proxy_falls_back_to_dns_for_unmapped      （函数名，44 字符，熵 4.03）
#   overall_deadline=DEFAULT_OVERALL_DEADLINE         （赋值，41 字符，熵 4.29）
# 含 `/` 时会吞掉 URL 路径：
#   com/18875216268/Leyao_Seed_Skill                 （URL 路径，32 字符，熵 4.20）
# 真实凭证（hex / base64 / 字母数字 key）不会用下划线或连字符做词分隔。
#
# 代价：base64url 形态（含 `-` `_`）与含 `/` 的 base64 不再被泛型熵检测覆盖。
# 这是可接受的取舍——真正的凭证几乎总会赋值给 api_key / password / token 之类的变量，
# 由 secret.assigned 规则无条件检出，与字符集无关。gitleaks 的 generic 规则做同样取舍。

# 能力探测：声明与实现是否一致（OWASP AST03 最小权限 / AST04 诚实元数据）。
NET_CALL = re.compile(r"(?i)\b(requests\.|urllib|httpx|http\.client|fetch\(|axios|curl |wget )")
SHELL_CALL = re.compile(r"(?i)\b(subprocess|os\.system|os\.popen|shell\s*=\s*True|pty\.spawn)")
WRITE_CALL = re.compile(r"(?i)\b(open\([^)]*['\"](w|a|r\+)|write_text|write_bytes|fs\.writeFile|>>\s*)")


def _iter_files(skill_dir):
    for dirpath, dirnames, filenames in os.walk(skill_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d not in TEST_DIRS]
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() in SCANNABLE_EXT:
                path = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(path) > MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                yield path


def _shannon(text):
    if not text:
        return 0.0
    counts = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = float(len(text))
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _is_benign(matched):
    """占位符或从环境读取的值不算真实凭证。"""
    return bool(PLACEHOLDER.search(matched)) or bool(ENV_REF.search(matched))


def _scan_secrets(skill_dir):
    """扫描目录内疑似凭证。返回 issues，**不携带匹配内容**。"""
    issues = []
    reported = set()
    for path in _iter_files(skill_dir):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        rel = os.path.relpath(path, skill_dir).replace("\\", "/")
        for lineno, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            for code, pattern, label in SECRET_PATTERNS:
                m = pattern.search(line)
                if not m:
                    continue
                # 取分组1（赋值型规则的捕获值）；无分组则取整段匹配。
                payload = m.group(1) if m.groups() else m.group(0)
                if _is_benign(payload) or _is_benign(line):
                    continue
                key = (rel, lineno, code)
                if key in reported:
                    continue
                reported.add(key)
                issues.append({
                    "level": "error",
                    "code": code,
                    "message": "%s:%d 疑似硬编码 %s（内容已省略，请人工核销并改用环境变量）" % (rel, lineno, label),
                })
            for m in HIGH_ENTROPY.finditer(line):
                blob = m.group(0)
                if _shannon(blob) < ENTROPY_THRESHOLD or _is_benign(blob) or _is_benign(line):
                    continue
                key = (rel, lineno, "secret.high_entropy")
                if key in reported:
                    continue
                reported.add(key)
                issues.append({
                    "level": "warn",
                    "code": "secret.high_entropy",
                    "message": "%s:%d 存在高熵字符串（熵值 %.2f），若为凭证请改用环境变量" % (rel, lineno, _shannon(blob)),
                })
    return issues


def _scan_capabilities(skill_dir, fm):
    """声明与实现一致性：网络 / shell / 写权限是否有对应声明（OWASP AST03、AST04）。"""
    issues = []
    if "network" not in fm and "shell" not in fm and not any(k in fm for k in ("permissions", "risk_tier")):
        # 未采用 OWASP 声明式 frontmatter 的旧式 skill 不做能力对账，
        # 只提示一次，避免对生态内大量存量 skill 刷屏。
        pass

    net_hit = shell_hit = write_hit = False
    for path in _iter_files(skill_dir):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        rel = os.path.relpath(path, skill_dir).replace("\\", "/")
        if rel.lower() == "skill.md":
            # SKILL.md 里的命令示例属文档，不作为能力判据
            body = text
            net_hit = net_hit or bool(NET_CALL.search(body))
            continue
        net_hit = net_hit or bool(NET_CALL.search(text))
        shell_hit = shell_hit or bool(SHELL_CALL.search(text))
        write_hit = write_hit or bool(WRITE_CALL.search(text))

    network = fm.get("network")
    if net_hit and network is None:
        issues.append({
            "level": "warn",
            "code": "undeclared_network",
            "message": "代码中存在网络调用但 frontmatter 未声明 network；建议声明域名白名单（最小权限）",
        })
    if isinstance(network, bool):
        issues.append({
            "level": "warn",
            "code": "network_is_bool",
            "message": "network 建议为域名白名单对象（{allow: [...], deny: \"*\"}），布尔开关无法约束出网范围",
        })
    if isinstance(network, dict):
        allow = network.get("allow") or []
        if "*" in allow:
            # 不因 deny 而豁免：allow 与 deny 是两件事——deny 约束的是"白名单之外
            # 的默认动作"，而 allow 含通配符时白名单已覆盖全部域名，deny 无从生效。
            # 旧实现加了 `and deny != "*"` 的逃逸条件，而 deny:"*" 恰是文档推荐的
            # 默认拒绝写法，于是这条规则对任何照文档写的 skill 都永不触发。
            issues.append({
                "level": "warn",
                "code": "network_allow_all",
                "message": "network.allow 含通配符，等价于无限制出网；"
                           "若确实需要任意域名，请将 risk_tier 提到 L2 并接受该风险",
            })
            tier = str(fm.get("risk_tier") or "").upper()
            if tier not in ("L2", "L3"):
                # 与 shell_tier_mismatch 同构：无限制出网却自称低风险，是对账不一致。
                issues.append({
                    "level": "warn",
                    "code": "network_tier_mismatch",
                    "message": "network.allow 含通配符（无限制出网）但 risk_tier=%r；"
                               "无限制出网的 skill 风险等级不应低于 L2" % (tier or "未声明"),
                })

    shell = fm.get("shell")
    if shell_hit and shell is None:
        issues.append({
            "level": "warn",
            "code": "undeclared_shell",
            "message": "代码中存在 shell/子进程调用但 frontmatter 未声明 shell；显式声明便于审计与沙箱决策",
        })
    if shell is True:
        tier = str(fm.get("risk_tier") or "").upper()
        if tier not in ("L2", "L3"):
            issues.append({
                "level": "warn",
                "code": "shell_tier_mismatch",
                "message": "shell=true 但 risk_tier=%r；可执行 shell 的 skill 风险等级不应低于 L2" % (tier or "未声明"),
            })

    if write_hit:
        perms = fm.get("permissions") or {}
        deny = perms.get("deny_write") if isinstance(perms, dict) else None
        if deny is None:
            issues.append({
                "level": "warn",
                "code": "undeclared_write",
                "message": "代码存在写操作但未声明 permissions.deny_write；建议显式保护 "
                           + "/".join(IDENTITY_FILES[:3]) + " 等身份文件",
            })

    return issues


def lint_skill(skill_dir, root=None):
    """返回 issues 列表，每项 {level: error|warn|info, code, message}。level=error 表示阻断级缺陷。"""
    issues = []
    fm_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(fm_path):
        return [{"level": "error", "code": "missing_skill_md", "message": "skill_dir 缺少 SKILL.md"}]

    with open(fm_path, encoding="utf-8") as f:
        text = f.read()
    fm = parse_frontmatter(text)

    # 先取绝对路径再取 basename：相对路径（`.`、`skills/foo`）直接 basename 会得到
    # `.` 之类的伪目录名，让 name_mismatch 对完全合规的 skill 误报。
    # 误报的代价是作者/AI 反过来去改 name，把本来一致的两处改坏。
    dir_name = os.path.basename(os.path.abspath(skill_dir.rstrip("/\\")))
    name = fm.get("name")
    if name and name != dir_name:
        issues.append({
            "level": "warn",
            "code": "name_mismatch",
            "message": "frontmatter name=%r 与目录名=%r 不一致（规范建议一致以便可审计）" % (name, dir_name),
        })
    if name and len(str(name)) > 64:
        issues.append({
            "level": "warn",
            "code": "name_too_long",
            "message": "name 超过 64 字符上限（Anthropic 规范）",
        })

    triggers = fm.get("triggers") or []
    if not triggers:
        issues.append({
            "level": "warn",
            "code": "no_triggers",
            "message": "未声明 triggers；当前仅靠 name/description 召回（符合官方规范但精度较低），"
                       "补充 triggers 可显著提升路由命中率",
        })

    # mode 未声明时由 distiller 自动判定（有 handler.py → native，否则 llm），
    # 因此"缺失"是合法写法，只有显式写了非法值才算缺陷。
    mode = fm.get("mode")
    if mode is not None and mode not in ("llm", "native"):
        issues.append({"level": "error", "code": "bad_mode", "message": "mode 应为 llm/native，当前=%r" % mode})

    desc = str(fm.get("description") or "")
    if len(desc) > 1024:
        issues.append({
            "level": "warn",
            "code": "desc_too_long",
            "message": "description 超过 1024 字符上限（Anthropic 规范），超出部分可能被截断",
        })
    if len(desc) < 20:
        issues.append({"level": "warn", "code": "desc_short", "message": "description 过短，应包含 WHAT+WHEN 触发语义"})
    elif not any(h in desc for h in TRIGGER_HINTS):
        issues.append({
            "level": "info",
            "code": "desc_no_trigger_hint",
            "message": "description 建议含触发词(当/如/需/查询)以明确使用场景",
        })

    lines = text.splitlines()
    if len(lines) > 500:
        issues.append({
            "level": "warn",
            "code": "too_long",
            "message": "SKILL.md %d 行超限(>500)，违背渐进式披露，建议拆 references/" % len(lines),
        })

    domain = [str(d).lower() for d in (fm.get("domain") or [])]
    triggers_l = [str(t).lower() for t in triggers]
    if domain and triggers_l and set(domain) == set(triggers_l):
        issues.append({"level": "warn", "code": "domain_dup_triggers", "message": "domain 与 triggers 完全重复，违反单一事实源"})

    issues.extend(_scan_capabilities(skill_dir, fm))
    issues.extend(_scan_secrets(skill_dir))
    return issues
