#!/usr/bin/env python3
"""查询侧增强（研究依据：LongMemEval 查询侧扩展 +11.3% recall）：

1) 规范化：全半角/标点/大小写（common.norm_text）；
2) 同义词与别名扩展：把口语词映射到库内常用词（可扩展表，非穷举）；
3) 时间感知：识别「今天/昨天/上个月/本月/今年/去年」→ 给出具体日期提示（用于建议与展示）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from common import CN, norm_text

# 别名/同义词表（**只扩不替换**：原文保留，扩展词用于召回；新词按真实未命中案例补充）
ALIASES = {
    "毛利": ["边际利润", "p4", "毛利额"],
    "口径": ["计算方式", "算法", "公式"],
    "制度": ["规定", "管理办法", "policy"],
    "术语": ["定义", "是什么意思"],
    "流程": ["步骤", "sop", "流程规范"],
    "缺货率": ["缺货品种占比"],
    "客户": ["门店", "药店"],
    "促销": ["活动", "促销活动"],
}

# 问句停用词（发检索前剥离；实测教训：整句"缺货率是什么"直发公共池会空 ✗）
QUESTION_WORDS = ("是什么", "什么意思", "是啥", "怎么算", "如何计算", "怎么计算", "怎么", "如何",
                  "有没有", "是否", "请问", "一下", "帮我查", "查一下", "给我", "找一下",
                  "呢", "吗", "啊", "的", "？", "?")


def core_term(text: str) -> str:
    """剥离问句后的**核心词**（不含别名扩展）——用于本地相关性门槛与排序。

    实测教训（2026-09-12）：别名扩展只该帮"服务器召回"，不能作为"本地判定相关"的依据
    （否则"毛利分析培训课程"会被"术语：边际利润"跑题命中 ✗）。
    """
    core = str(text or "").strip()
    for w in QUESTION_WORDS:
        core = core.replace(w, "")
    return core.strip()


def search_terms(text: str) -> list:
    """产出**用于服务器检索**的词（按优先级）：① 核心词 ② 别名扩展 ③ 原文兜底（最多 3 个）。"""
    raw = str(text or "").strip()
    core = core_term(raw)
    terms = []
    if core and core != raw:
        terms.append(core)
    for t in expand(core or raw):
        if t not in terms:
            terms.append(t)
    if raw not in terms:
        terms.append(raw)
    return terms[:3]


_TIME_RULES = [
    (re.compile(r"今天|今日"), 0),
    (re.compile(r"昨天|昨日"), 1),
    (re.compile(r"前天"), 2),
]


def time_hint(text: str, now: datetime | None = None) -> str:
    """识别相对时间 → 返回「提示文本」（仅用于展示与建议，不改变查询语义）。"""
    now = now or datetime.now(CN)
    for rx, back in _TIME_RULES:
        if rx.search(text):
            return (now - timedelta(days=back)).strftime("%Y-%m-%d")
    m = re.search(r"上(个)?月", text)
    if m:
        first = now.replace(day=1)
        return (first - timedelta(days=1)).strftime("%Y-%m")
    if re.search(r"本月|当月", text):
        return now.strftime("%Y-%m")
    if re.search(r"去年", text):
        return str(now.year - 1)
    if re.search(r"今年", text):
        return str(now.year)
    return ""


def expand(text: str) -> list:
    """返回扩展查询词列表（原词在前，别名随后）。"""
    base = str(text or "").strip()
    n = norm_text(base)
    out = [base]
    for key, alts in ALIASES.items():
        if norm_text(key) in n:
            out.extend(a for a in alts if a not in out)
    return out


def normalize(text: str) -> dict:
    """统一入口：{raw, norm, expansions, time_hint}。"""
    return {
        "raw": str(text or "").strip(),
        "norm": norm_text(text),
        "expansions": expand(text),
        "time_hint": time_hint(text),
    }
