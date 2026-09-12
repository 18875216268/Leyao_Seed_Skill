#!/usr/bin/env python3
"""need_type 推断（零依赖、规则式、可解释）：term / caliber / policy / course / search。

判据为词面规则（可扩表）；推断不确定时落 search（最宽召回）。
显式 --need-type 永远优先（调用方知道自己要什么）。
"""
from __future__ import annotations

from common import norm_text

RULES = [
    ("caliber", ("怎么算", "如何计算", "计算方式", "算法", "公式", "口径", "取值", "定义是什么")),
    ("term", ("是什么", "什么意思", "含义", "术语", "定义")),
    ("policy", ("制度", "规定", "管理办法", "政策", "流程", "规范", "审批", "权限")),
    ("course", ("课程", "培训", "学习", "教材", "课件", "考试")),
]


def infer(problem: str) -> dict:
    """返回 {need_type, confidence, why}；命中规则的第一条胜出。"""
    n = norm_text(problem)
    for need_type, kws in RULES:
        for kw in kws:
            if norm_text(kw) in n:
                return {"need_type": need_type, "confidence": 0.8, "why": "命中关键词：%s" % kw}
    return {"need_type": "search", "confidence": 0.5, "why": "未命中专门规则 → 走宽召回 search"}
