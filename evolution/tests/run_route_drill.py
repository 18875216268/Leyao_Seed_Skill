#!/usr/bin/env python3
"""路由演练（常驻 · 零联网 · 零写入）：判据链在**真实描述**上的分支回归 + 模板静态校验。

诚实边界：本演练是**规则化模拟**（词面 → 不适用 → 输入前置 → 父子取子 → 覆盖核对 → 门槛），
验证"判据链能否给出正确分支"；真实 AI 行为由实战轨迹（过程记录 D 区四格）继续校准。

用法：
  python evolution/tests/run_route_drill.py          # 跑全部断言
输出：逐条 PASS/FAIL + 末尾 JSON 汇总（ok / passed / total / failed），退出码 0/1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # leyao-seed-core/
sys.dont_write_bytecode = True                      # 运行期零写包（不在包内生成 __pycache__）
FIELDS = ("【何时用】", "【不适用】", "【别名】", "【输入前置】", "【时效性】", "【回退】")
PARENT = {"psol5x": "p3nes3", "h4dsa6": "i7c4z1"}
OLD_PHRASE = "有任何业务数据需求时"                 # 旧模板措辞（应已清除）


def load_nodes():
    d = json.loads((ROOT / "library" / "routes.json").read_text(encoding="utf-8"))
    out = {}

    def walk(ns):
        for n in ns:
            out[n["id"]] = n
            walk(n.get("children") or [])

    walk(d["nodes"])
    return out


NODES = load_nodes()
DESC = {k: (v.get("description") or "") for k, v in NODES.items()}
TITLE = {k: (v.get("title") or "") for k, v in NODES.items()}
# 描述分级（与 engine.desc_state 同判据）：structured=六段齐备（判据链全能力）；free=自由文本（降级匹配）；
# empty=未写（不可路由）。六段属**推荐**——A 组只对结构化节点断言字段，自由节点改判"降级标注在场"。
STRUCT = [k for k, d in DESC.items() if all(f in d for f in FIELDS)]
FREE = [k for k, d in DESC.items() if d.strip() and k not in STRUCT]
EMPTY = [k for k, d in DESC.items() if not d.strip()]
ROUTES_TXT = (ROOT / "library" / "ROUTES.md").read_text(encoding="utf-8")
ROUTES_LINES = ROUTES_TXT.splitlines()


def _flag_ok(keys, marker: str) -> bool:
    """每个 key 节点在 ROUTES.md 的**自身行**都必须带降级标注（不静默降级）。"""
    return all(any(marker in ln and f"`{k}`" in ln for ln in ROUTES_LINES) for k in keys)


def field(desc: str, name: str) -> str:
    m = re.search(re.escape(name) + r"([^｜|【\n]*)", desc or "")
    return (m.group(1).strip(" 　") if m else "")


def kw_hits(desc: str, kws) -> list:
    hay = (field(desc, "【何时用】") + "｜" + field(desc, "【别名】")).lower()
    return [k for k in kws if k.lower() in hay]


def _cover(label_kws, nid):
    hay = field(DESC[nid], "【何时用】").lower()
    return any(k.lower() in hay for k in label_kws)


def simulate(kws, needs=(), have=("凭证", "原始素材", "URL", "git")):
    """判据链：1a 圈候选 → 1b 筛（父子按贴合度 / 语义化不适用 / 输入前置）→ 1c 覆盖核对 → 2/4 门槛。"""
    cand = [nid for nid, d in DESC.items() if kw_hits(d, kws)]
    if not cand:
        return "none", "候选=空集 → 亲做 + 提案"
    # 1b-1 父子竞争：按贴合度（子专项覆盖数 vs 父覆盖数；相当取子）
    for child, parent in PARENT.items():
        if child in cand and parent in cand:
            ch = sum(1 for _lb, cov in needs if _cover(cov, child))
            ph = sum(1 for _lb, cov in needs if _cover(cov, parent))
            drop = parent if ch >= ph else child
            cand = [n for n in cand if n != drop]
    cand = sorted(set(cand))
    # 1b-2 不适用排除（语义化：自己也声称过的词 → 视为边界说明；"非 X" 负向提及 → 不排除）
    excl = []
    for n in cand:
        notok = field(DESC[n], "【不适用】").lower()
        own = field(DESC[n], "【何时用】").lower()
        for k in kws:
            kl = k.lower()
            if kl not in notok or kl in own:
                continue
            if re.search(r"[非不]\s*" + re.escape(kl), notok):
                continue
            excl.append(n)
            break
    cand = [n for n in cand if n not in excl]
    if not cand:
        return "none", "候选被【不适用】排除（%s）→ 亲做" % excl
    # 1b-3 输入前置（"无需 X" 表示不需要；不可得才算挡）
    blocked = []
    for n in cand:
        pre = field(DESC[n], "【输入前置】")
        need = [x for x in ("凭证", "原始素材", "URL", "git")
                if x in pre and not re.search(r"[无不用]\s*(需|要)?\s*" + re.escape(x), pre) and x not in have]
        if need:
            blocked.append(n)
    if blocked and len(blocked) == len(cand):
        return "prereq", "候选 %s 的输入前置不可得 → 先索要 / 退回亲做" % blocked
    cand = [n for n in cand if n not in blocked]
    # 1c 覆盖核对
    uncovered = [lb for lb, cov in needs
                 if not any(any(k.lower() in field(DESC[n], "【何时用】").lower() for k in cov) for n in cand)]
    if uncovered:
        return "ask", "核心需求无人覆盖（%s）→ 问用户（可含组合调用）｜剩余候选 %s" % (uncovered, cand)
    # 门槛
    if len(cand) >= 2:
        return "ask", "≥2 个都成立（%s）→ 问用户" % cand
    return "single", cand[0]


# ---------------- A. 静态校验（模板与旧措辞） ----------------
A = [
    ("A1 每个节点有非空描述（空描述不可路由）",
     all(DESC[n].strip() for n in NODES),
     [n for n in NODES if not DESC[n].strip()]),
    ("A2 无旧模板措辞残留（'%s'）" % OLD_PHRASE,
     all(OLD_PHRASE not in DESC[n] for n in NODES),
     [n for n in NODES if OLD_PHRASE in DESC[n]]),
    ("A3 结构化节点【回退】非空（自由描述节点不适用）",
     all(field(DESC[n], "【回退】") for n in STRUCT), None),
    ("A4 结构化节点【别名】非空（无别名写「无」）",
     all(field(DESC[n], "【别名】") for n in STRUCT), None),
    ("A5 别名 ≠ 标题", all(field(DESC[n], "【别名】").strip() != TITLE[n].strip() for n in NODES),
     [n for n in NODES if field(DESC[n], "【别名】").strip() == TITLE[n].strip()]),
    ("A6 业务两节点写明互斥与并选指引（p3nes3/i7c4z1）",
     all(("并选" in DESC[n] or "同时成立" in DESC[n]) for n in ("p3nes3", "i7c4z1")), None),
    ("A7 判据链含 1c 覆盖核对（flow/3）",
     "1c. **覆盖核对" in (ROOT / "processor" / "flow" / "3-execute.md").read_text(encoding="utf-8"), None),
    ("A8 过程记录形状含路由决策记录（shapes.md 第 6 节）",
     all(k in (ROOT / "processor" / "shapes.md").read_text(encoding="utf-8")
         for k in ("## 6. 路由决策记录", "四格", "成因")), None),
    ("A9 自由/无描述节点在 ROUTES.md 带降级标注（不静默降级）",
     _flag_ok(FREE, "（自由描述·降级匹配）") and _flag_ok(EMPTY, "（无描述·不可路由）"), None),
]

# ---------------- B. 场景矩阵（判据链分支） ----------------
# (名称, 关键词, 核心需求[(标签,[覆盖词])], 可用输入, 期望分支, 期望命中id)
B = [
    ("B01 出库统计自定义（父子同时像 → 取子）", ["出库统计", "自定义"],
     [("出库统计", ["出库统计"]), ("自定义", ["自定义"])], ("凭证", "原始素材"), "single", "h4dsa6"),
    ("B02 当天促销毛利明细（子节点专项）", ["促销毛利", "当天"],
     [("当天实时", ["当天实时", "当天数据"])], ("凭证",), "single", "psol5x"),
    ("B03 PMS 内置参数查询（父节点承接）", ["PMS", "板块"],
     [("板块参数", ["内置板块参数", "板块参数"])], ("凭证",), "single", "p3nes3"),
    ("B04 实时+自定义并存（覆盖不全 → 问用户）", ["含税", "毛利", "自定义"],
     [("当天实时", ["当天实时", "当天数据"]), ("自定义", ["自定义", "聚合"])], ("凭证",), "ask", None),
    ("B05 只接受 T+1（时效性硬约束）", ["前一天", "自定义"],
     [("自定义", ["自定义"])], ("凭证",), "single", "h4dsa6"),
    ("B06 要应收边际利润（psol5x 不适用 → 亲做）", ["促销毛利", "边际"],
     [("边际利润", ["边际利润"])], ("凭证",), "none", None),
    ("B07 无凭证（输入前置挡下）", ["促销毛利"], [("促销毛利", ["促销毛利"])], ("原始素材",), "prereq", None),
    ("B08 流程图（套件类唯一命中）", ["流程图"], [("流程图", ["流程图"])], ("原始素材",), "single", "bvix9o"),
    ("B09 GitHub 不可达（访问层唯一命中）", ["GitHub", "打不开"],
     [("连通", ["GitHub", "连通", "加速"])], ("URL", "git"), "single", "fmcq5n"),
    ("B10 组合需求（取数 + 画图 → 问用户）", ["出库统计", "流程图"],
     [("取数", ["出库统计"]), ("画图", ["流程图"])], ("凭证", "原始素材"), "ask", None),
    ("B11 模糊任务（'看看数据' → 先澄清）", ["数据"],
     [("数据", ["数据"])], ("凭证",), "ask", None),
    ("B12 无命中（PDF 转 Word）", ["PDF", "Word"], [], ("原始素材",), "none", None),
    ("B13 别名命中（'BI 查询' → 父级）", ["BI 查询"],
     [("取数", ["业务数据", "自定义"])], ("凭证",), "single", "i7c4z1"),
    ("B14 别名词面（'乐药 BI' → 父级）", ["乐药 BI"],
     [("取数", ["业务数据", "自定义"])], ("凭证",), "single", "i7c4z1"),
]

# ---------------- C. 过程记录 D 区形状（可写性） ----------------
D_TEMPLATE = ("- D-nn ｜ 路由：<场景> ｜ 候选：<…> ｜ 判定依据：<…> ｜ 结论：<…> ｜ "
              "复核：<…> ｜ 必要性：<…> ｜ 四格：<…> ｜ 成因：<…>")
C = [
    ("C1 D 区条目模板字段齐备（候选/依据/结论/复核/必要性/四格/成因）",
     all(k in D_TEMPLATE for k in ("候选", "判定依据", "结论", "复核", "必要性", "四格", "成因")), None),
]


def section_d():
    """D 段：把一条"路由决策记录"按 shapes 第 6 节落到四区工作区的过程记录里（临时目录，跑完即清）。"""
    import shutil
    import tempfile
    out = []
    tmp = Path(tempfile.mkdtemp(prefix="leyao_routedrill_"))
    try:
        wd = tmp / "work-20260912-路由留证"
        for z in ("01-原始材料区", "02-任务执行区", "03-结果交付区", "04-归档区"):
            (wd / z).mkdir(parents=True)
        (wd / "02-任务执行区" / "临时产物区").mkdir()
        rec = ("# 过程记录 · 路由留证\n\n## 1. 需求原文\n\n要上月出库明细，可自定义字段（T+1 可接受）\n\n"
               "## 2. 进度（SOAP 式）\n\n- [完成] 路由已定（D-01）\n\n"
               "## 3. 关键信息与口径（KI）\n\n- KI-01 ｜ 数据截止前一天 ｜ 口径：自然月 ｜ 来源：用户口述\n\n"
               "## 4. 决策记录（D）\n\n" + D_TEMPLATE
               .replace("<场景>", "上月出库明细，可自定义字段")
               .replace("<…>", "依据：词面命中'自定义'＋【时效性】T+1 可接受；结论：直接走 i7c4z1；复核：触发条件对照通过；"
                                "必要性：必要（自带判据做不了业务库取数）；四格：命中；成因：—")
               + "\n\n## 5. 产物与归档指针（P / A）\n\n- P-01 ｜ `02-任务执行区/临时产物区/outbound-202608.csv` ｜ 用途：草稿\n")
        (wd / "02-任务执行区" / "过程记录.md").write_text(rec, encoding="utf-8")
        top = sorted(x.name for x in wd.iterdir())
        out.append(("D1 四区工作区根目录只有四区",
                    top == ["01-原始材料区", "02-任务执行区", "03-结果交付区", "04-归档区"], str(top)))
        body = (wd / "02-任务执行区" / "过程记录.md").read_text(encoding="utf-8")
        out.append(("D2 过程记录五节齐备", all(s in body for s in ("## 1. 需求原文", "## 2. 进度", "## 3. 关键信息", "## 4. 决策记录", "## 5. 产物与归档")), None))
        out.append(("D3 路由决策记录七字段可落位（候选/依据/结论/复核/必要性/四格/成因）",
                    all(k in body for k in ("候选", "判定依据", "结论", "复核", "必要性", "四格", "成因")), None))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.parse_args()
    items = []

    def ok(name, good, extra=None):
        items.append({"name": name, "ok": bool(good)})
        print("%s  %s%s" % ("PASS" if good else "FAIL", name, ("  | " + str(extra)) if (extra and not good) else ""))

    print("== A 静态（模板与旧措辞）==")
    for name, good, extra in A:
        ok(name, good, extra)

    print("== B 场景矩阵（判据链分支）==")
    for name, kws, needs, have, exp, exp_id in B:
        br, why = simulate(kws, needs=needs, have=have)
        good = (br == exp) and (exp_id is None or exp_id in str(why))
        ok(name, good, "期望 %s/%s 实得 %s ｜ %s" % (exp, exp_id, br, why))

    print("== C 过程记录 D 区形状 ==")
    for name, good, extra in C:
        ok(name, good, extra)

    print("== D 端到端：路由留证落到四区工作区 ==")
    for name, good, extra in section_d():
        ok(name, good, extra)

    failed = [x["name"] for x in items if not x["ok"]]
    total = len(items)
    done = total - len(failed)
    print("\n%s" % json.dumps({"ok": not failed, "passed": done, "total": total, "failed": failed},
                              ensure_ascii=False, indent=1))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
