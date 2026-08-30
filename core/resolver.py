"""两段式路由：廉价召回只读元数据，全量 skill 描述永不每 query 入 ctx。

召回字段的依据——Anthropic Agent Skills 官方规范：
frontmatter 只有 name + description 两个必填项，且 description 被明确定义为
召回字段（"The description field enables Skill discovery ... Claude uses it to
choose the right Skill from potentially 100+ available Skills"）。生态里 49 万+
skill 绝大多数只有 name + description，没有 triggers。

结论：只匹配 triggers/domain 会让任何符合官方规范的 skill 召回率为零——
这不是"少一个召回维度"，而是与官方生态的兼容断裂。

因此本模块按信号强度分层召回：
  triggers / domain  框架扩展的高精度信号（作者显式声明，权重最高）
  name / description 官方规范信号（保证标准 skill 可被召回）
两者加权融合；description 走 2-gram 覆盖度而非命中计数，避免长文本占优。
"""

import re

_CJK = "一-龥"
_TOKEN_RE = re.compile(r"[a-z0-9]+|[" + _CJK + r"]+")

# 英文停用词：只让实词参与召回匹配，避免 "use / when / this" 之类高频虚词
# 制造虚假交集。
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those is are was were be been being
do does did doing have has had having i me my we our you your he she it its they them
from to of in on at by for with without into onto as about after before over under
again when where why how what which who whom whose all any both each few more most
other some such no nor not only own same so too very can will just should now
use used uses using
""".split())

W_TRIGGER = 2.0
W_NAME = 1.5
W_DOMAIN = 1.0
W_DESC_SCALE = 2.0
# 用户历史频次的上限。刻意小于一个 trigger 命中（2.0）：个性化只能在同一档内调序，
# 不能让「你以前用过」推翻「这个更相关」。
W_USAGE_SCALE = 0.5

# 仅 name/description 命中时的准入门槛。2-gram 存在偶发噪音
# （"销售数据" 切出 "售数"），单个 token 命中不足以证明相关性。
MIN_WEAK_HITS = 2

# 归一化后短于该长度的词项不参与归一化匹配：`C++` 会退化成 `c`，
# 匹配任何含 c 的查询，制造大规模误召。
MIN_NORM_LEN = 2

# description 的 2-gram 集合对同一 entry 是不变量，但直接在 recall 里
# 「每个 query × 每个 entry」重算一次会吃掉绝大多数耗时——实测 N=200 时
# 单次 7.624ms 中有 7.472ms 花在这里，约 98%。按内容缓存即可归零。
# 约定：返回集合只读，调用方不得原地修改。
_EMPTY = frozenset()
_DESC_TOKENS = {}
# 归一化结果同样缓存：触发词是 entry 的不变量，却在「每个 query × 每个 entry × 每个词项」
# 上被反复归一化。不缓存的话归一化要吃掉约 27% 的耗时。
_NORMED = {}
_CACHE_LIMIT = 4096

# 归一化：剥掉空格 / 标点 / 连字符。中文输入里夹空格、英文里带连字符都是常态，
# 纯子串匹配会让「促销 毛利」召不回触发词「促销毛利」。
_NOISE_RE = re.compile(r"[^0-9a-z" + _CJK + r"]+")


def norm(text):
    return _NOISE_RE.sub("", str(text or "").lower())


def _normed(term):
    """词项的归一化结果按原文缓存。返回的字符串只读。"""
    got = _NORMED.get(term)
    if got is None:
        if len(_NORMED) >= _CACHE_LIMIT:
            _NORMED.clear()
            _DESC_TOKENS.clear()
        got = _NORMED[term] = norm(term)
    return got


def _desc_tokens(entry):
    desc = str(entry.get("description") or "")
    if not desc:
        return _EMPTY
    cached = _DESC_TOKENS.get(desc)
    if cached is None:
        if len(_DESC_TOKENS) >= _CACHE_LIMIT:
            _DESC_TOKENS.clear()
        cached = _DESC_TOKENS[desc] = tokens(desc)
    return cached


def _matches(term, q_low, nq):
    """词项是否出现在查询里：先试原文子串，落空再试归一化子串。

    归一化只会**增加**命中、不会减少，因此不改变既有行为，纯补漏。
    """
    t = str(term or "").lower()
    if t and t in q_low:
        return True
    nt = _normed(t)
    return len(nt) >= MIN_NORM_LEN and nt in nq


def _stem(word):
    """极简英文复数还原，零依赖。

    不做完整 Porter 词干化——只处理最常见的复数后缀，解决
    spreadsheet/spreadsheets、table/tables 这类单复数不匹配导致的漏召回。
    """
    if len(word) <= 3 or not word.isascii():
        return word
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ss"):
        return word
    if word.endswith("s"):
        return word[:-1]
    return word


def tokens(text, min_len=2):
    """中英文混合切词，零依赖。英文按词边界 + 复数还原，中文按 2-gram（bigram indexing）。"""
    out = set()
    for seg in _TOKEN_RE.findall(str(text or "").lower()):
        if len(seg) < min_len:
            continue
        if seg.isascii():
            if seg in _STOPWORDS:
                continue
            out.add(_stem(seg))
        else:
            for i in range(len(seg) - min_len + 1):
                out.add(seg[i:i + 2])
    return out


def scope_specificity(scope):
    parts = [p for p in str(scope or "").split(".") if p]
    concrete = sum(1 for p in parts if p != "*")
    return concrete * 10 + len(parts)


def recall(entries, query):
    q = str(query).lower()
    nq = norm(query)
    qt = tokens(query)
    cands = []
    for e in entries:
        if not e.get("enabled", True):
            continue
        if any(_matches(t, q, nq) for t in e.get("negative_triggers", [])):
            continue
        trigger_hits = [t for t in e.get("triggers", []) if _matches(t, q, nq)]
        domain_hits = [d for d in e.get("domain", []) if _matches(d, q, nq)]

        name = str(e.get("name") or "")
        name_l = name.lower()
        name_hits = []
        if name_l:
            if _matches(name, q, nq):
                name_hits = [name]
            elif any(t in name_l for t in qt):
                name_hits = [name]

        desc_hits = qt & _desc_tokens(e)

        # 高精度信号（作者显式声明）直接准入；仅靠官方规范信号时要求多重印证。
        strong = trigger_hits or domain_hits or name_hits
        if not strong and len(desc_hits) < MIN_WEAK_HITS:
            continue

        cands.append({
            "entry": e,
            "trigger_hits": trigger_hits,
            "domain_hits": domain_hits,
            "name_hits": name_hits,
            "desc_hits": sorted(desc_hits),
        })
    return cands


def experience_boost(rules, entry, q, nq):
    boost = 0.0
    for r in rules or []:
        if r.get("target") != entry["id"]:
            continue
        state = r.get("state")
        if state == "candidate":
            continue
        tokens_ = r.get("pattern", [])
        if not tokens_ or not all(_matches(t, q, nq) for t in tokens_):
            continue
        weight = 1.5 if state == "locked" else 1.0
        boost += -weight if r.get("kind") == "avoid" else weight
    return boost


def usage_boost(usage, entry):
    """用户历史使用频次的小幅加成：用得多的往前排，但不许压过语义信号。

    `usage` 形如 {skill_id: 次数}。按「相对最常用」归一化，上限 W_USAGE_SCALE。
    零历史时恒为 0——新装套件的行为与完全没有这个特性时一致。
    """
    if not usage:
        return 0.0
    peak = 0.0
    for count in usage.values():
        if isinstance(count, (int, float)) and count > peak:
            peak = float(count)
    if peak <= 0:
        return 0.0
    mine = usage.get(entry.get("id"), 0)
    if not isinstance(mine, (int, float)) or mine <= 0:
        return 0.0
    return round(W_USAGE_SCALE * (float(mine) / peak), 4)


def sort_key(item):
    e = item["entry"]
    return (item["score"], e.get("priority", 0), scope_specificity(e.get("scope")), e["id"])


def rank(cands, query, experience=None, usage=None):
    q = str(query).lower()
    nq = norm(query)
    qt = tokens(query)
    if not qt:
        qt = {q} if q.strip() else set()
    denom = float(len(qt)) if qt else 1.0
    scored = []
    for c in cands:
        e = c["entry"]
        # description 用覆盖度而非命中数：长 description 不该仅因为词多而占优。
        coverage = len(c.get("desc_hits", [])) / denom
        score = (len(c["trigger_hits"]) * W_TRIGGER
                 + len(c.get("name_hits", [])) * W_NAME
                 + len(c["domain_hits"]) * W_DOMAIN
                 + coverage * W_DESC_SCALE)
        score += e.get("priority", 0) * 0.1
        score += scope_specificity(e.get("scope")) * 0.05
        score += experience_boost(experience, e, q, nq)
        score += usage_boost(usage, e)
        scored.append({
            "entry": e,
            "trigger_hits": c["trigger_hits"],
            "domain_hits": c["domain_hits"],
            "name_hits": c.get("name_hits", []),
            "desc_hits": c.get("desc_hits", []),
            "score": round(score, 4),
        })
    scored.sort(key=sort_key, reverse=True)
    return scored


def resolve(entries, query, experience=None, usage=None):
    return rank(recall(entries, query), query, experience, usage)
