"""YAML front-matter 解析（零依赖）。

放在 core 的原因：它是通用解析器，被 core / evolution / deploy 三层共同消费。
若归到某一层，其余两层就得反向依赖那一层——那才是产生架构环的根因。

不支持完整 YAML，只覆盖 skill front-matter 实际用到的三种形态：
  key: value                 标量 / 行内 [a, b] / 行内 {k: v}
  key:\\n  - a\\n  - b         块列表
  key:\\n  sub: v\\n  sub2: [a] 嵌套映射（可再含块列表）

必须支持嵌套：OWASP 对齐的权限字段是 `network.allow` / `permissions.deny_write`
两级结构。扁平解析器会把它们读成空字符串，于是作者明明声明了、lint 仍报
undeclared_network——文档承诺的能力实际不可达。
"""

import re

FM_BOOL = {"true": True, "false": False}


def _coerce(value):
    v = str(value).strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [x.strip().strip("'\"") for x in inner.split(",")]
    if v.lower() in FM_BOOL:
        return FM_BOOL[v.lower()]
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v.strip("'\"")


# 行内 flow mapping：`{allow: [github.com], deny: "*"}`。
_FLOW_PAIR = re.compile(r"([A-Za-z0-9_.\-]+)\s*:\s*(\[[^\]]*\]|\"[^\"]*\"|'[^']*'|[^\s,}]+)")


def _coerce_flow(value):
    """在 _coerce 之上补一层行内 mapping，其余原样委派。"""
    v = str(value).strip()
    if v.startswith("{") and v.endswith("}"):
        out = {}
        for m in _FLOW_PAIR.finditer(v[1:-1]):
            out[m.group(1)] = _coerce(m.group(2))
        if out:
            return out
    return _coerce(value)


def _strip_comment(line):
    """去掉行内尾注释，引号内的 # 保留（如 URL fragment）。

    不处理会让 `priority: 0  # 越大越优先` 把注释当成值的一部分。
    """
    out = []
    quote = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out).rstrip()


def _indent(line):
    return len(line) - len(line.lstrip(" \t"))


def _parse_entries(lines, start, indent):
    """解析 indent 层级的 `key: value` 条目，返回 (data, 下一个未消费行号)。"""
    data = {}
    i = start
    while i < len(lines):
        raw = _strip_comment(lines[i])
        if not raw.strip():
            i += 1
            continue
        cur = _indent(raw)
        if cur != indent:
            break
        stripped = raw.strip()
        if stripped.startswith("- ") or ":" not in stripped:
            break
        key, _, value = stripped.partition(":")
        key, value = key.strip(), value.strip()
        if value:
            data[key] = _coerce_flow(value)
            i += 1
            continue
        # 空值：吸收后续所有更深缩进的行作为子块
        j = i + 1
        while j < len(lines):
            nxt = lines[j]
            if not _strip_comment(nxt).strip():
                j += 1
                continue
            if _indent(nxt) <= cur:
                break
            j += 1
        data[key] = _parse_child(lines[i + 1:j])
        i = j
    return data, i


def _parse_child(lines):
    """解析子块：全为 `- item` 即列表；否则按映射解析（可再嵌套一层）。"""
    items = [_strip_comment(x) for x in lines if _strip_comment(x).strip()]
    if not items:
        return ""
    if all(x.strip().startswith("- ") for x in items):
        return [_coerce_flow(x.strip()[2:].strip()) for x in items]
    base = min(_indent(x) for x in items)
    normalized = [x[base:] if len(x) >= base else x.lstrip() for x in items]
    data, _ = _parse_entries(normalized, 0, 0)
    return data if data else [_coerce_flow(x.strip()) for x in items]


def parse_frontmatter(text):
    """解析 SKILL.md 的 YAML front-matter，返回 dict。无 front-matter 时返回空 dict。"""
    lines = str(text).splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    body = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        body.append(line.rstrip())
    data, _ = _parse_entries(body, 0, 0)
    return data
