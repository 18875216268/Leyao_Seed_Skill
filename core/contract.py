"""统一契约：路由表条目 schema（llm / native 双模式）与 native handler 校验。"""

REQUIRED = ["id", "name", "domain", "triggers", "mode", "path", "priority", "scope"]

MODES = ("llm", "native")

NATIVE_FUNCS = ("describe", "can_handle", "invoke", "health")

# 注意：这里的每个字段都必须有真实消费点。
#
# 此前 `auth` 与 `health` 是死字段——写进条目却无人读取。它们的危害不只是"多两个键"：
# 读注册表的人（包括 AI）会以为框架在跟踪子 skill 的鉴权要求与健康度，
# 从而基于不存在的信息做判断。宁可没有这个字段，也不要一个有字段名却无语义的字段。
#
# `health` 容易混淆：它是 native handler 的**契约函数**（见 NATIVE_FUNCS，executor 真实调用），
# 那是活的；死的只是本表里那个永远为 1.0 的条目字段。
DEFAULTS = {
    "domain": [],
    "version_pin": "0.0.0",
    "enabled": True,
    "depends": [],
    "negative_triggers": [],
    "description": "",
}


def normalize(entry):
    out = dict(DEFAULTS)
    out.update(entry)
    for key in ("domain", "triggers", "negative_triggers"):
        if isinstance(out.get(key), str):
            out[key] = [out[key]]
    return out


def validate(entry):
    missing = [k for k in REQUIRED if k not in entry]
    if missing:
        raise ValueError("registry entry missing fields: " + ",".join(missing))
    if entry["mode"] not in MODES:
        raise ValueError("mode must be one of %s, got %r" % (list(MODES), entry["mode"]))
    if not isinstance(entry["triggers"], list):
        raise ValueError("triggers must be a list")
    if not isinstance(entry["domain"], list):
        raise ValueError("domain must be a list")
    if not isinstance(entry["negative_triggers"], list):
        raise ValueError("negative_triggers must be a list")
    if not isinstance(entry["priority"], (int, float)):
        raise ValueError("priority must be numeric")
    # 可召回性门禁（Anthropic Agent Skills 规范对齐）：
    # 规范只把 name + description 列为 frontmatter 必填项，且 description 才是召回字段
    # （name 是 64 字符的标识符，用于引用与讨论，不承担匹配职责）。
    # 生态里符合官方规范的 skill 往往没有 triggers——要求 triggers 非空等于把这些
    # skill 全部挡在门外。故放宽为：triggers 非空 或 description 非空，二者有一即可召回。
    # 两者皆空的 entry 永不命中，与其让它静默进注册表，不如在准入时拒绝。
    if not entry["triggers"] and not str(entry.get("description") or "").strip():
        raise ValueError(
            "entry must expose recallable metadata: non-empty triggers or description "
            "(per Anthropic spec, name is an identifier, not a recall signal)"
        )
    return True


def validate_native_module(mod, skill_id):
    missing = [f for f in NATIVE_FUNCS if not callable(getattr(mod, f, None))]
    if missing:
        raise ValueError("native skill %s missing %s" % (skill_id, ",".join(missing)))
    return True
