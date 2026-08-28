"""用户建模：沉淀跨 skill 整体画像，回灌路由个性化与引导渐进揭示。与子 skill 自有 SEM 隔离。"""

FAMILIARITY_THRESHOLDS = ((50, "expert"), (10, "familiar"))


class UserModeler:
    def __init__(self, store):
        self.store = store

    def observe(self, traces):
        profile = self.store.user_model() or {}
        profile.setdefault("runs", 0)
        profile.setdefault("corrections", 0)
        profile.setdefault("domains", {})
        profile.setdefault("overrides", {})
        for trace in traces:
            if not isinstance(trace, dict):
                continue
            profile["runs"] += 1
            routed = trace.get("routed_skill")
            if routed:
                profile["domains"][routed] = profile["domains"].get(routed, 0) + 1
            override = trace.get("user_override")
            if override:
                profile["corrections"] += 1
                profile["overrides"][override] = profile["overrides"].get(override, 0) + 1
        self.store.set_user_model(profile)
        return profile

    def profile(self):
        return self.store.user_model() or {}

    def preferred_skill(self):
        overrides = self.profile().get("overrides", {})
        if not overrides:
            return None
        return max(overrides.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def familiarity(self):
        runs = self.profile().get("runs", 0)
        for threshold, label in FAMILIARITY_THRESHOLDS:
            if runs >= threshold:
                return label
        return "novice"
