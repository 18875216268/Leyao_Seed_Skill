#!/usr/bin/env python3
"""运营知识库（公共池）客户端 —— 第一优先源（读 + 沉淀写）。

契约（实测 2026-09-12；与池侧原机制一致）：
- 读：GET <endpoint>?q=<关键词>&limit=N[&tier=inject|session][&category=…][&kind=fact|procedure]
  响应 {ok, count, items:[{id, category, title, content, trust, hit_count, adopt_count,
          quality_score, freshness, version, similarity_hash, contributor, status, …}]}
- 写（需共享 token；本 skill 仅显式 contribute / 采纳上报时调用）：
  submit 写池沉淀（服务端三层闸 + 帕累托）· inject 写注入库（authority，仅用户显式要求）
  · record_adopt 采纳价值信号（失败静默）。

设计要点：
- **单请求全取**：默认不带 tier/category 过滤（一次拿全，本地按 trust 排序）→ 少往返 = 快；
  **唯一例外：口径（caliber）只查注入库 tier=inject**（对齐池侧语义：口径必须权威）；
- 只在 need_type ∈ {term, caliber} 时带 category（服务端枚举就这两个对得上，传错会空结果）；
- 超时/重试来自 registry；失败如实返回（不静默）；**知识永不真删**（池侧走状态标记）。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

CATEGORY_MAP = {"term": "term", "caliber": "caliber"}


def _get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "leyao-knowledge/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def search(terms: list, need_type: str, *, endpoint: str, limit: int = 20,
           timeout: float = 6.0, retry: int = 1, tier: str | None = None,
           use_category: bool = False, kind: str | None = None) -> dict:
    """按 terms 依次检索（第一个有命中的即返回，最多 3 个词，预算内）。

    terms 来自 query_norm.search_terms（核心词 → 别名 → 原文兜底）；
    实测依据：整句直发会空，剥离问句后命中（2026-09-12）。
    """
    t0 = time.perf_counter()
    tried, last_err = [], ""
    for term in (terms or [])[:3]:
        params = {"q": term, "limit": str(limit)}
        if tier:
            params["tier"] = tier
        if kind:
            params["kind"] = kind
        if use_category and need_type in CATEGORY_MAP:
            params["category"] = CATEGORY_MAP[need_type]
        url = "%s?%s" % (endpoint.rstrip("/"), urllib.parse.urlencode(params))
        for attempt in range(retry + 1):
            try:
                data = _get(url, timeout)
                items = [it for it in (data.get("items") or []) if it.get("status", "active") == "active"]
                tried.append({"q": term, "count": len(items)})
                if items:
                    return {"ok": True, "items": [_to_possibility(it) for it in items],
                            "ms": int((time.perf_counter() - t0) * 1000), "url": url, "tried": tried}
                break                       # 该词返回空 → 试下一个词（不是错误）
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_err = "%s: %s" % (type(exc).__name__, str(exc)[:120])
                if attempt < retry:
                    time.sleep(0.5)
    return {"ok": bool(tried), "items": [], "ms": int((time.perf_counter() - t0) * 1000),
            "error": last_err, "tried": tried}


def _to_possibility(it: dict) -> dict:
    return {
        "answer": str(it.get("content") or "").strip(),
        "title": str(it.get("title") or "").strip(),
        "need_type": it.get("category") or "",
        "source": "pool",
        "trust": it.get("trust") or "reference",
        "confidence": float(it.get("quality_score") or 0.0),
        "version": it.get("version"),
        "freshness": it.get("freshness"),
        "hit_count": it.get("hit_count"),
        "adopt_count": it.get("adopt_count"),
        "pool_id": str(it.get("id") or ""),      # 池内 id：采纳上报 / 沉淀溯源
        "evidence": ["pool#%s" % it.get("id")],
        "tags": [t for t in (it.get("category"), it.get("distill_type")) if t],
        "score": float(it.get("quality_score") or 0.0),
    }


# ---------- 写路径（沉淀；写接口需共享 token，默认仅显式调用） ----------

def _post(endpoint: str, path: str, body: dict, *, token: str, timeout: float = 10.0) -> dict:
    """POST JSON（零第三方依赖；失败如实返回，不抛）。"""
    url = endpoint.rstrip("/") + path
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "leyao-knowledge/1.0",
                 "X-Contributor-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {"ok": True}
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "HTTP %s" % exc.code}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": "NETWORK",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:120])}


def submit(endpoint: str, *, title: str, content: str, category: str = "experience",
           distill_type: str = "lesson", trust: str = "reference", quality_score: float = 0.5,
           contributor: str = "leyao-knowledge", kind: str = "fact", token: str,
           timeout: float = 10.0) -> dict:
    """写池沉淀（服务端执行三层闸 + 帕累托；本函数只负责协议）。"""
    return _post(endpoint, "", {"title": title, "content": content, "category": category,
                                "distill_type": distill_type, "trust": trust,
                                "quality_score": quality_score, "contributor": contributor,
                                "kind": kind}, token=token, timeout=timeout)


def inject(endpoint: str, *, title: str, content: str, category: str = "experience",
           kind: str = "fact", quality_score: float = 0.9, contributor: str = "user",
           distill_type: str | None = None, token: str, timeout: float = 10.0) -> dict:
    """写注入库（authority；**仅用户显式要求注入时调用**，不得自动触发）。"""
    return _post(endpoint, "/inject", {"title": title, "content": content, "category": category,
                                       "kind": kind, "quality_score": quality_score,
                                       "contributor": contributor, "distill_type": distill_type},
                 token=token, timeout=timeout)


def record_adopt(endpoint: str, pool_id: str, *, token: str, timeout: float = 5.0) -> dict:
    """采纳价值信号（需 token 防伪造；短超时、失败静默，不阻塞主流程）。"""
    if not pool_id:
        return {"ok": False, "error": "MISSING_ID"}
    return _post(endpoint, "/adopt", {"id": pool_id}, token=token, timeout=timeout)


def probe(endpoint: str, timeout: float = 6.0) -> dict:
    """doctor 用：最小请求探活（q 为空 → 默认热度列表）。"""
    t0 = time.perf_counter()
    try:
        data = _get("%s?limit=1" % endpoint.rstrip("/"), timeout)
        return {"ok": bool(data.get("ok")), "ms": int((time.perf_counter() - t0) * 1000),
                "count": data.get("count")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "ms": int((time.perf_counter() - t0) * 1000),
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:120])}
