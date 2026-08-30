"""规范对齐回归测试：Anthropic Agent Skills 官方规范 + OWASP Agentic Skills Top 10。

锁死两类契约，防止后续改动悄悄退化：

1. 符合官方规范的 skill（frontmatter 只有 name + description，没有 triggers）
   必须能被 discover 登记并被 route 召回。生态里 49 万+ skill 是这种形态；
   一旦退化，本套件对官方生态的召回率归零。
2. lint 必须检出硬编码凭证，且**报告本身不得回显凭证内容**——
   lint 结果会被 AI 读进上下文，回显等于把密钥送进模型。
"""

import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from _harness import setup  # noqa: E402

setup()

from core import resolver  # noqa: E402
from core.lint import lint_skill  # noqa: E402
from core.resolver import recall  # noqa: E402
from evolution import distiller  # noqa: E402
from suite import Suite  # noqa: E402

OFFICIAL_DESC = (
    "description: Extracts text and tables from PDF files, fills forms, and merges documents. "
    "Use when working with PDF documents or when the user mentions PDFs, forms, or document extraction."
)


def make_suite_root():
    tmp = tempfile.mkdtemp(prefix="skill-spec-")
    for sub in ("registry", "skills", "state"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    with open(os.path.join(tmp, "registry", "skills.json"), "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(os.path.join(tmp, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"suite": "LeyaoSeedSkill", "version": "0.1.0", "skills": {}}, f)
    return tmp


def make_skill(root, skill_id, frontmatter, body="用法说明。", extra=None):
    directory = os.path.join(root, "skills", skill_id)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write("---\n%s\n---\n\n%s\n" % (frontmatter, body))
    for rel, content in (extra or {}).items():
        path = os.path.join(directory, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    return directory


def secret_codes(issues):
    return [i["code"] for i in issues if i["code"].startswith("secret.")]


def test_official_spec_skill_is_discovered_and_routed():
    """官方规范形态（只有 name + description）必须能被登记并路由命中。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "pdf-processing", "name: pdf-processing\n" + OFFICIAL_DESC)
        suite = Suite(tmp)
        actions = suite.discover()
        assert ("pdf-processing", "registered", "user_drop") in actions, actions

        got = suite.route("帮我把这个 pdf 里的表格抽出来")
        assert got.get("routed") == "direct", got
        assert (got.get("result") or {}).get("skill_id") == "pdf-processing", got
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_official_spec_skill_english_query():
    """英文 query 同样要走官方规范信号。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "excel-analysis", "name: excel-analysis\n" + (
            "description: Analyze Excel spreadsheets, create pivot tables, generate charts. "
            "Use when analyzing Excel files, spreadsheets, tabular data, or .xlsx files."))
        suite = Suite(tmp)
        suite.discover()
        got = suite.route("build a pivot table from this spreadsheet")
        assert (got.get("result") or {}).get("skill_id") == "excel-analysis", got
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_explicit_triggers_outweigh_description():
    """作者显式声明的 triggers 是高精度信号，优先级高于 description 语义匹配。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "weak-desc", "name: weak-desc\n" + (
            "description: 用于销售报表与销售数据查询、销售图表生成的技能"))
        make_skill(tmp, "strong-trigger", "name: strong-trigger\n" + (
            "description: 用于处理销售相关的各类事务") + "\ntriggers: [销售报表]")
        suite = Suite(tmp)
        suite.discover()
        got = suite.route("查一下销售报表")
        assert (got.get("result") or {}).get("skill_id") == "strong-trigger", got
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_unrelated_query_not_recalled():
    """扩大召回面不等于放开召回：无关 query 不应命中。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "pdf-processing", "name: pdf-processing\n" + OFFICIAL_DESC)
        suite = Suite(tmp)
        suite.discover()
        got = suite.route("帮我订一张明天去上海的高铁票")
        assert got.get("routed") != "direct", got
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_recall_normalizes_spaces_and_punctuation():
    """中文输入夹空格、英文带连字符都是常态，纯子串匹配会整片漏召。

    实测漏召样本：`促销 毛利` / `促销，毛利` / `P-M-S` 全部召不回对应触发词。
    """
    entries = [
        {"id": "a", "name": "a", "triggers": ["促销毛利"], "domain": [], "description": ""},
        {"id": "b", "name": "b", "triggers": ["PMS"], "domain": [], "description": ""},
    ]
    for query, expect_id in [
        ("促销毛利", "a"),
        ("促销 毛利", "a"),
        ("促销，毛利", "a"),
        ("查一下促销 毛利", "a"),
        ("pms", "b"),
        ("P-M-S", "b"),
        ("p m s", "b"),
    ]:
        hits = [c["entry"]["id"] for c in recall(entries, query)]
        assert expect_id in hits, "query=%r hits=%s" % (query, hits)


def test_recall_normalization_never_removes_existing_hits():
    """归一化只能补漏，不能改坏：原本命中的 query 必须仍然命中。

    这条是防止"为了修漏召把召回面收窄"的反向守卫。
    """
    entries = [{"id": "a", "name": "a", "triggers": ["促销毛利", "报表"], "domain": [],
                "description": "销售报表查询"}]
    for query in ("促销毛利", "查促销毛利", "报表", "销售报表"):
        assert recall(entries, query), "query=%r lost its hit" % query


def test_short_normalized_trigger_is_ignored():
    """归一化后短于 2 字符的词项不得参与归一化匹配。

    `C++` 归一化后是 `c`，若不加长度守卫，任何含 c 的 query 都会命中——
    一次漏召修复换来一片误召，比原来的问题更糟。
    """
    entries = [{"id": "cpp", "name": "cpp", "triggers": ["C++"], "domain": [], "description": ""}]
    assert [c["entry"]["id"] for c in recall(entries, "abc")] == []
    assert [c["entry"]["id"] for c in recall(entries, "C++")] == ["cpp"]


def test_negative_triggers_are_normalized_too():
    """排除词与触发词必须走同一套匹配，否则「写了排除却不生效」。

    排除词刻意写成 `去年毛利`，查询写成 `去年 毛利`——原文子串对不上，
    只有归一化后才会命中。若排除分支退回纯子串匹配，这条 query 会被错误召回。
    """
    entries = [{"id": "a", "name": "a", "triggers": ["促销"], "domain": [], "description": "",
                "negative_triggers": ["去年毛利"]}]
    # 触发词命中、排除词只有归一化才对得上 —— 必须被排除。
    assert recall(entries, "促销 去年 毛利") == []
    # 少了排除词里的「去年」，就该正常召回。
    assert [c["entry"]["id"] for c in recall(entries, "促销 毛利")] == ["a"]


def test_description_tokens_are_cached_and_immutable():
    """description 的 2-gram 集合按内容缓存，且缓存不得被调用方污染。

    不变量：同一段 description 无论查询多少次、被多少个 entry 共用，
    召回结果必须完全一致；缓存本身也不得被上一次调用改动。
    """
    desc_a = "用于销售报表与销售数据查询的技能"
    desc_b = "用于库存盘点与采购订单管理的技能"
    entries = [
        {"id": "a", "name": "a", "triggers": [], "domain": [], "description": desc_a},
        {"id": "b", "name": "b", "triggers": [], "domain": [], "description": desc_b},
    ]
    # 1) 缓存值必须等于现场计算值，且两段不同 description 不得串味。
    #    只比对「前后两次快照相等」是不够的——缓存恒空时两边也相等。
    assert resolver._desc_tokens(entries[0]) == resolver.tokens(desc_a)
    assert resolver._desc_tokens(entries[1]) == resolver.tokens(desc_b)
    assert resolver._desc_tokens(entries[0]) != resolver._desc_tokens(entries[1])
    assert resolver._desc_tokens(entries[0]), "缓存为空集等于 description 信号被静默丢弃"

    # 2) 重复查询不得改变结果，也不得改写缓存。
    first = [sorted(c["desc_hits"]) for c in recall(entries, "销售报表")]
    assert first and first[0], "description 弱信号未命中，缓存收益无从验证：%s" % first
    snapshot = set(resolver._desc_tokens(entries[0]))
    for _ in range(5):
        again = [sorted(c["desc_hits"]) for c in recall(entries, "销售报表")]
        assert again == first, "缓存改变了召回结果"
    assert resolver._desc_tokens(entries[0]) == snapshot, "缓存被调用方原地改写了"

    # 3) 无 description 的 entry 走空集分支，不得抛异常也不得命中。
    assert recall([{"id": "c", "name": "c", "triggers": [], "domain": []}], "销售报表") == []


def test_description_cache_survives_eviction():
    """缓存有上限并会清空：达到上限后召回结果不得改变（只影响性能，不影响语义）。

    六个 entry 的 description 刻意两两不同、且各自只被自己的关键词命中——
    若缓存把 A 的 tokens 存到 B 名下，这里必然召回错人。
    """
    original = dict(resolver._DESC_TOKENS)
    try:
        resolver._DESC_TOKENS.clear()
        resolver._CACHE_LIMIT = 2
        topics = ["销售报表", "库存盘点", "采购订单", "客户回访", "发票核验", "物流跟踪"]
        entries = [{"id": "s%d" % i, "name": "s%d" % i, "triggers": [], "domain": [],
                    "description": "用于%s与%s查询的技能" % (topic, topic)}
                   for i, topic in enumerate(topics)]

        def hit_ids(query):
            return [c["entry"]["id"] for c in recall(entries, query)]

        for i, topic in enumerate(topics):
            got = hit_ids(topic)
            assert got == ["s%d" % i], "topic=%s 召回了 %s" % (topic, got)
        # 第二轮：缓存已被换出（上限 2 < 6 个 entry），结果必须逐条一致。
        for i, topic in enumerate(topics):
            got = hit_ids(topic)
            assert got == ["s%d" % i], "缓存换出后召回结果变了：topic=%s got=%s" % (topic, got)
    finally:
        resolver._CACHE_LIMIT = 4096
        resolver._DESC_TOKENS.clear()
        resolver._DESC_TOKENS.update(original)


def test_entry_without_recallable_metadata_still_rejected():
    """name 是标识符不是召回信号：只有 name、无 triggers 无 description 仍应拒绝。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "bare", "name: bare")
        try:
            distiller.derive_entry("bare", tmp)
        except ValueError as ex:
            assert "triggers" in str(ex), str(ex)
            return
        raise AssertionError("expected ValueError for skill without recallable metadata")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_tmp_harness_sweep_only_touches_stale_own_directories():
    """辅助区回收只碰「自家前缀 + 已超龄」的目录。

    回收删的是真实文件，误删的代价远高于残留本身，所以这条守的是回收的
    **边界**而不是回收的收益：非自家前缀不得碰、未超龄的不得碰。
    """
    from _harness import sweep
    tmp = tempfile.mkdtemp(prefix="skill-spec-")
    base = os.path.dirname(tmp)
    own_stale = os.path.join(base, "skill-stale-guard")
    own_fresh = os.path.join(base, "skill-fresh-guard")
    alien = os.path.join(base, "someone-elses-data")
    try:
        for d in (own_stale, own_fresh, alien):
            os.makedirs(d, exist_ok=True)
        now = time.time()
        old = now - 48 * 3600
        for d in (own_stale, alien):
            os.utime(d, (old, old))
        os.utime(own_fresh, (now, now))

        sweep(base, now=now)
        assert not os.path.exists(own_stale), "超龄的自家目录应被回收"
        assert os.path.exists(own_fresh), "未超龄的自家目录不得被回收"
        assert os.path.exists(alien), "非本辅助区前缀的目录绝不能被回收"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        for d in (own_stale, own_fresh, alien):
            shutil.rmtree(d, ignore_errors=True)


def test_name_mismatch_is_not_reported_for_relative_paths():
    """相对路径调用 lint 不得误报 name_mismatch。

    `basename(".")` 得到 `"."`，会把完全合规的 skill 判成 name 与目录名不一致。
    误报的代价不是噪音本身——是 AI 照着这条 warn 去改 name，把本来一致的两处改坏。
    """
    tmp = make_suite_root()
    cwd = os.getcwd()
    try:
        root_name = os.path.basename(tmp)
        fm = "---\nname: %s\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]\n---\n\n用法。\n"
        with open(os.path.join(tmp, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(fm % root_name)
        os.chdir(tmp)
        for form in (".", "./", os.curdir + os.sep):
            codes = {i["code"] for i in lint_skill(form, root=form)}
            assert "name_mismatch" not in codes, "相对路径 %r 误报：%s" % (form, codes)
        # 真写错时必须照样报——只放宽路径写法，不放宽「确实不一致」。
        with open(os.path.join(tmp, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(fm % "totally-different")
        assert "name_mismatch" in {i["code"] for i in lint_skill(".", root=".")}
    finally:
        os.chdir(cwd)
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_detects_hardcoded_secret_without_echoing_it():
    """检出凭证，且报告不回显内容——lint 结果会进模型上下文。"""
    tmp = make_suite_root()
    try:
        leaked = "sk-abcdefghijklmnopqrstuvwx"
        sk = make_skill(
            tmp, "leaky",
            "name: leaky\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]",
            extra={"scripts/run.py": 'API_KEY = "%s"\n' % leaked},
        )
        issues = lint_skill(sk, root=tmp)
        assert secret_codes(issues), issues
        blob = json.dumps(issues, ensure_ascii=False)
        assert leaked not in blob, "lint 报告回显了凭证内容，等同于把密钥送进上下文"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_ignores_placeholders_and_env_refs():
    """占位符与环境变量引用不是真实凭证，不得误报（否则 lint 全是噪音）。"""
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "clean",
            "name: clean\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]",
            extra={"scripts/run.py": "\n".join([
                "import os",
                'API_KEY = os.environ.get("API_KEY")',
                'PASSWORD = "your_password_here"',
                'SAMPLE_TOKEN = "EXAMPLE-placeholder-token"',
                "",
            ])},
        )
        issues = lint_skill(sk, root=tmp)
        assert not secret_codes(issues), issues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_flags_undeclared_network():
    """OWASP AST03 最小权限：有网络调用就该声明域名白名单。"""
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "netty",
            "name: netty\ndescription: 用于查询远端销售报表数据的技能\ntriggers: [报表]",
            extra={"scripts/run.py": "import requests\nrequests.get('https://api.example.com')\n"},
        )
        issues = lint_skill(sk, root=tmp)
        assert "undeclared_network" in {i["code"] for i in issues}, issues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_lint_rejects_bool_network():
    """OWASP：network 应为域名白名单，布尔开关约束不了出网范围。"""
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "boolnet",
            "name: boolnet\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]\nnetwork: true",
        )
        issues = lint_skill(sk, root=tmp)
        assert "network_is_bool" in {i["code"] for i in issues}, issues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_entropy_scan_ignores_snake_case_identifiers():
    """高熵检测不得把 snake_case 函数名/常量名当成凭证。

    实测误报：`test_ip_proxy_falls_back_to_dns_for_unmapped`（44 字符，熵 4.03）、
    `overall_deadline=DEFAULT_OVERALL_DEADLINE`（41 字符，熵 4.29）。
    根因是字符集含 `_`，把整条标识符吞成一个 blob。
    """
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "ident",
            "name: ident\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]",
            extra={"scripts/run.py": "\n".join([
                "def test_ip_proxy_falls_back_to_dns_for_unmapped():",
                "    pass",
                "OVERALL_DEADLINE = DEFAULT_OVERALL_DEADLINE",
                "REPO = 'https://github.com/18875216268/Leyao_Seed_Skill.git'",
                "",
            ])},
        )
        issues = lint_skill(sk, root=tmp)
        assert not secret_codes(issues), issues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_entropy_scan_still_catches_real_keys():
    """收紧字符集不能把真凭证一起放掉：hex / base64 / 字母数字长 key 必须仍被检出。"""
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "entropy",
            "name: entropy\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]",
            extra={"scripts/run.py": "\n".join([
                "HEX_BLOB = '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'",
                "B64_BLOB = 'QWxhZGRpbjpvcGVuIHNlc2FtZUFsYWRkaW46b3Blbg=='",
                "ALNUM_KEY = 'aB3dEf7hIj9kLm2nOp4qRs6tUv8wXy1z0AbCdEfGhIj'",
                "",
            ])},
        )
        issues = lint_skill(sk, root=tmp)
        codes = secret_codes(issues)
        assert "secret.high_entropy" in codes, issues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mode_omitted_is_not_bad_mode():
    """mode 未声明时由 distiller 自动判定（有 handler.py → native，否则 llm）。

    把它判成 error 会与框架自身文档「mode 可选」直接矛盾，且对所有存量 skill 误报。
    """
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "nomode",
            "name: nomode\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]",
        )
        assert "bad_mode" not in {i["code"] for i in lint_skill(sk, root=tmp)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_mode_invalid_value_still_errors():
    """显式写了非法值仍是缺陷——只放宽"缺失"，不放宽"写错"。"""
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "badmode",
            "name: badmode\nmode: magic\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]",
        )
        assert "bad_mode" in {i["code"] for i in lint_skill(sk, root=tmp)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_test_directories_are_not_scanned():
    """测试夹具天然含合成凭证，扫它只产出必然被忽略的 error。

    一个总被忽略的 error 通道等于没有 error 通道。CI 级全量覆盖交给 gitleaks/TruffleHog。
    """
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "withtests",
            "name: withtests\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]",
            extra={
                "tests/fixtures/keys.py": 'LEAKED = "sk-abcdefghijklmnopqrstuvwx"\n',
                "tests/test_run.py": "import requests\nimport subprocess\n",
            },
        )
        issues = lint_skill(sk, root=tmp)
        assert not secret_codes(issues), issues
        assert "undeclared_network" not in {i["code"] for i in issues}, issues
        assert "undeclared_shell" not in {i["code"] for i in issues}, issues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_runtime_code_is_still_scanned():
    """跳过测试目录不能变成"把凭证藏进 tests 就查不出"——运行时代码仍全量扫描。"""
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "runtime",
            "name: runtime\ndescription: 用于查询销售报表数据的技能\ntriggers: [报表]",
            extra={"scripts/run.py": 'LEAKED = "sk-abcdefghijklmnopqrstuvwx"\n'},
        )
        issues = lint_skill(sk, root=tmp)
        assert secret_codes(issues), issues
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_frontmatter_parses_nested_mapping():
    """OWASP 权限字段是两级结构；扁平解析器会把 network 读成空串，导致声明形同虚设。"""
    from evolution.distiller import parse_frontmatter
    fm = parse_frontmatter("\n".join([
        "---",
        "name: x",
        "network:",
        '  allow: ["github.com"]',
        '  deny: "*"',
        "---",
    ]))
    assert fm["network"] == {"allow": ["github.com"], "deny": "*"}, fm


def test_frontmatter_parses_nested_block_list():
    from evolution.distiller import parse_frontmatter
    fm = parse_frontmatter("\n".join([
        "---",
        "name: x",
        "permissions:",
        "  deny_write:",
        "    - SOUL.md",
        "    - MEMORY.md",
        "---",
    ]))
    assert fm["permissions"] == {"deny_write": ["SOUL.md", "MEMORY.md"]}, fm


def test_frontmatter_parses_flow_mapping():
    from evolution.distiller import parse_frontmatter
    fm = parse_frontmatter('---\nname: x\nnetwork: {allow: ["a.com"], deny: "*"}\n---')
    assert fm["network"] == {"allow": ["a.com"], "deny": "*"}, fm


def test_frontmatter_keeps_flat_and_block_list_forms():
    """升级不得破坏既有形态：行内列表、块列表、标量、布尔、数字。"""
    from evolution.distiller import parse_frontmatter
    fm = parse_frontmatter("\n".join([
        "---",
        "name: x",
        "description: 用于查询销售报表数据的技能",
        "triggers:",
        "  - 报表",
        "  - 销售",
        "priority: 3",
        "enabled: true",
        "---",
    ]))
    assert fm["triggers"] == ["报表", "销售"], fm
    assert fm["priority"] == 3 and fm["enabled"] is True, fm
    assert fm["description"] == "用于查询销售报表数据的技能", fm


def test_declared_network_silences_undeclared_warning():
    """真正的收益：作者声明了 network，就不该再报 undeclared_network。

    解析器升级前这条必报——声明读不出来，等于逼作者用 lint 认不了的方式写配置。
    """
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "declared",
            "\n".join([
                "name: declared",
                "description: 用于查询销售报表数据的技能",
                "triggers: [报表]",
                "network:",
                '  allow: ["api.example.com"]',
                '  deny: "*"',
            ]),
            extra={"scripts/run.py": "import requests\nrequests.get('https://api.example.com')\n"},
        )
        codes = {i["code"] for i in lint_skill(sk, root=tmp)}
        assert "undeclared_network" not in codes, codes
        assert "network_allow_all" not in codes, codes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_declared_network_allow_all_is_still_flagged():
    """声明可读之后，危险的"全通"声明才真能被测出来（此前永远测不到）。"""
    tmp = make_suite_root()
    try:
        sk = make_skill(
            tmp, "wideopen",
            "\n".join([
                "name: wideopen",
                "description: 用于查询销售报表数据的技能",
                "triggers: [报表]",
                "network:",
                '  allow: ["*"]',
            ]),
            extra={"scripts/run.py": "import requests\nrequests.get('https://x.com')\n"},
        )
        codes = {i["code"] for i in lint_skill(sk, root=tmp)}
        assert "network_allow_all" in codes, codes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_frontmatter_comment_does_not_truncate_parsing():
    """注释行不得中断解析——否则注释之后的所有字段（version/scope/…）会静默丢失。"""
    from evolution.distiller import parse_frontmatter
    fm = parse_frontmatter("\n".join([
        "---",
        "name: x",
        "# 这是一段注释",
        "# 第二段注释",
        "version: 1.2.3",
        "scope: suite.*",
        "---",
    ]))
    assert fm == {"name": "x", "version": "1.2.3", "scope": "suite.*"}, fm


def test_frontmatter_inline_comment_is_stripped_but_quoted_hash_kept():
    from evolution.distiller import parse_frontmatter
    fm = parse_frontmatter("\n".join([
        "---",
        "name: x",
        "priority: 3  # 越大越优先",
        "url: \"https://a.com/p#frag\"",
        "---",
    ]))
    assert fm["priority"] == 3, fm
    assert fm["url"] == "https://a.com/p#frag", fm


def test_frontmatter_comment_inside_block_list():
    from evolution.distiller import parse_frontmatter
    fm = parse_frontmatter("\n".join([
        "---",
        "name: x",
        "triggers:",
        "  - 报表  # 主召回词",
        "  - 销售",
        "---",
    ]))
    assert fm["triggers"] == ["报表", "销售"], fm


def test_network_allow_all_is_reported_even_with_deny_wildcard():
    """`allow: ["*"]` 不因 `deny: "*"` 而豁免——旧实现的逃逸条件让这条规则永不触发。

    allow 与 deny 管的是两件事：deny 约束"白名单之外的默认动作"，而 allow 含通配符时
    白名单已覆盖全部域名，deny 无从生效。旧实现要求 `deny != "*"`，而 `deny: "*"`
    恰是文档推荐的默认拒绝写法，于是任何照文档写的 skill 都能一行绕过该检查。
    """
    tmp = make_suite_root()
    try:
        # 文档推荐的写法：allow 精确 + deny 通配 —— 应当干净
        make_skill(tmp, "tight", "\n".join([
            "name: tight",
            "description: 只访问单一域名的受限技能",
            "network:",
            '  allow: ["api.example.com"]',
            '  deny: "*"',
            "risk_tier: L1",
        ]))
        codes = [i["code"] for i in lint_skill(os.path.join(tmp, "skills", "tight"), root=tmp)]
        assert "network_allow_all" not in codes, codes

        # 同样的 deny 通配，但 allow 放开 —— 必须照样报警
        make_skill(tmp, "loose", "\n".join([
            "name: loose",
            "description: 需要访问任意域名的技能",
            "network:",
            '  allow: ["*"]',
            '  deny: "*"',
            "risk_tier: L2",
        ]))
        codes = [i["code"] for i in lint_skill(os.path.join(tmp, "skills", "loose"), root=tmp)]
        assert "network_allow_all" in codes, "deny 通配符被当成逃逸条件，安全门禁失效：%s" % codes
        # 已声明 L2（诚实接受风险），不应再叠加对账不一致
        assert "network_tier_mismatch" not in codes, codes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_network_allow_all_with_low_tier_is_flagged_as_mismatch():
    """无限制出网却自称低风险，是对账不一致——与 shell_tier_mismatch 同构。"""
    tmp = make_suite_root()
    try:
        make_skill(tmp, "greedy", "\n".join([
            "name: greedy",
            "description: 声称只读却要访问任意域名的技能",
            "network:",
            '  allow: ["*"]',
            "risk_tier: L0",
        ]))
        codes = [i["code"] for i in lint_skill(os.path.join(tmp, "skills", "greedy"), root=tmp)]
        assert "network_allow_all" in codes, codes
        assert "network_tier_mismatch" in codes, codes

        # 提到 L2 即诚实对账，只剩 allow_all 的风险提示
        make_skill(tmp, "honest", "\n".join([
            "name: honest",
            "description: 需要访问任意域名并如实标注风险等级的技能",
            "network:",
            '  allow: ["*"]',
            "risk_tier: L2",
        ]))
        codes = [i["code"] for i in lint_skill(os.path.join(tmp, "skills", "honest"), root=tmp)]
        assert "network_allow_all" in codes, codes
        assert "network_tier_mismatch" not in codes, codes
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_suite_own_capability_declaration_is_readable():
    """本套件自己的 OWASP 能力声明必须能被解析出来。

    能力声明是 lint 安全门禁的输入：`undeclared_network`、`deny_write` 保护身份文件
    都靠它。一旦 front-matter 解析器回退成扁平解析（历史上 `network:` 被读成空串），
    声明还在文档里、lint 却读不到——门禁静默失效，危险的 `allow: ["*"]` 永远测不出来。
    这条测试把"文档承诺"和"实际可读"锁在一起。
    """
    parent = os.path.dirname(ROOT)
    skill_md = os.path.join(ROOT, "SKILL.md")
    assert os.path.exists(skill_md), "套件缺少 SKILL.md"
    fm = distiller.parse_frontmatter(distiller.read_skill_md(parent, os.path.basename(ROOT)))

    net = fm.get("network")
    assert isinstance(net, dict), "network 被读成标量，能力声明不可读：%r" % (net,)
    assert net.get("allow") == ["github.com"], net
    assert net.get("deny") == "*", net
    assert fm.get("shell") is True, fm.get("shell")
    assert "SOUL.md" in (fm.get("permissions") or {}).get("deny_write", []), fm.get("permissions")
    assert fm.get("risk_tier") == "L2", fm.get("risk_tier")

    # 声明可读还不够：按真实路径 lint 自己必须干净。
    # 注意 lint_skill(skill_dir, root=ROOT) 的 root 形参——传错会走不到套件自身的文件树。
    assert lint_skill(ROOT, root=ROOT) == [], lint_skill(ROOT, root=ROOT)


def main():
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    results = []
    for name, fn in tests:
        try:
            fn()
            results.append((name, True, ""))
        except AssertionError as ex:
            results.append((name, False, "assert: %s" % ex))
        except Exception as ex:
            results.append((name, False, "%s: %s" % (type(ex).__name__, ex)))
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else " -> " + detail))
    print("\n%d/%d passed" % (passed, len(results)))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
