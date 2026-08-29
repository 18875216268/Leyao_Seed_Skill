"""统一契约：路由表条目 schema（llm / native 双模式）与 native handler 校验。"""

REQUIRED = ["id", "name", "domain", "triggers", "mode", "path", "priority", "scope"]

MODES = ("llm", "native")

NATIVE_FUNCS = ("describe", "can_handle", "invoke", "health")

DEFAULTS = {
    "domain": [],
    "auth": "none",
    "version_pin": "0.0.0",
    "enabled": True,
    "depends": [],
    "health": 1.0,
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
    if not isinstance(entry["triggers"], list) or not entry["triggers"]:
        raise ValueError("triggers must be a non-empty list")
    if not isinstance(entry["domain"], list):
        raise ValueError("domain must be a list")
    if not isinstance(entry["negative_triggers"], list):
        raise ValueError("negative_triggers must be a list")
    if not isinstance(entry["priority"], (int, float)):
        raise ValueError("priority must be numeric")
    return True


def validate_native_module(mod, skill_id):
    missing = [f for f in NATIVE_FUNCS if not callable(getattr(mod, f, None))]
    if missing:
        raise ValueError("native skill %s missing %s" % (skill_id, ",".join(missing)))
    return True
