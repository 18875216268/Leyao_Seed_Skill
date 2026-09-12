#!/usr/bin/env python3
"""字段同步：把运行时发现的字段/筛选器同步进内置文档（显式人工维护）。

引擎每次查询都实时拉取页面元数据（按当前用户会话），语义字典（catalog）外的新字段
可直接使用（引擎自动并入并告警）。本命令把这类"动态发现的参数"显式同步回内置文档，
保持文档与运行时一致——对齐取数口径准则 3（内置参数非固定、可扩展）。

用法：
  python scripts/sync_fields.py            # 校对：报告 新增/消失（不写盘）
  python scripts/sync_fields.py --write    # 同步：catalog 入册 + parameters.md 更新

行为：
- 新增（运行时有、语义字典无）→ 入册 catalog（annotation 标注"待补业务口径"）并更新
  parameters.md（筛选/维度/指标清单与计数）；业务语义需人工补写（见文档尾部"待补语义"）。
- 消失（语义字典有、运行时无）→ 仅报告，不自动删除（可能是权限差异，需人工确认）。
- catalogVersion 自动 +1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BOARD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BOARD / "scripts"))
from bi_client.catalog import load_catalog  # noqa: E402
from bi_client.errors import BiError  # noqa: E402
from bi_client.profile import load_profile  # noqa: E402
from bi_client.query import QueryService  # noqa: E402

PARAMS_FILE = BOARD / "references" / "parameters.md"
CATALOG_FILE = BOARD / "resources" / "catalog.json"


def _collect():
    profile, catalog = load_profile(), load_catalog()
    service = QueryService(profile, catalog)
    context = service.prepare()
    md = context.metadata
    extras = {
        "dimensions": [f for f in md["dimensions"] if f.get("semantic") is False],
        "metrics": [f for f in md["metrics"] if f.get("semantic") is False],
        "selectors": [s for s in md["selectors"] if s.get("semantic") is False],
    }
    vanished = {
        "dimensions": [f["name"] for f in md["dimensions"] if f.get("semantic") is None and not f.get("runtimeAvailable")],
        "metrics": [f["name"] for f in md["metrics"] if f.get("semantic") is None and not f.get("runtimeAvailable")],
        "selectors": [s["name"] for s in md["selectors"] if s.get("semantic") is None and not s.get("runtimeAvailable")],
    }
    return catalog, md, extras, vanished


def _replace_block(md: str, start_heading: str, next_heading_pattern: str, new_block: str) -> str:
    start = md.find(start_heading)
    if start < 0:
        raise BiError("PARAMS_SECTION_MISSING", f"parameters.md 缺少节：{start_heading}")
    end = md.find("\n## ", start + len(start_heading))
    if end < 0:
        end = len(md)
    return md[:start] + new_block + md[end:]


def _update_params_md(catalog: dict, extras: dict[str, list[dict]]) -> None:
    md = PARAMS_FILE.read_text(encoding="utf-8")
    n_sel = len(catalog["selectors"])
    n_dim = len(catalog["dimensions"])
    n_met = len(catalog["metrics"])

    # ① 筛选字段：计数 + 新筛选器表格行（插到「### 候选值获取」前的最后一个表格行之后）
    f_start = md.find("## 筛选字段（")
    f_end = md.find("## 聚合维度（")
    f_block = md[f_start:f_end]
    f_block = re.sub(r"## 筛选字段（\d+）", f"## 筛选字段（{n_sel}）", f_block, count=1)
    if extras["selectors"]:
        rows = []
        for s in extras["selectors"]:
            stype = str(s.get("selectorType") or "")
            if stype in ("TIME_MACRO", "TIME"):
                rows.append(f"| {s['name']} | 时间区间 | 无候选接口，用查询项 `date` 传值 |")
            elif "TREE" in stype.upper():
                rows.append(f"| {s['name']} | 区域树 | 走 treeSelector；`candidates.py --search` 取子树 |")
            else:
                rows.append(f"| {s['name']} | 动态候选 | `candidates.py --filter` 实时获取 |")
        anchor = f_block.find("### 候选值获取")
        head, tail = f_block[:anchor], f_block[anchor:]
        cut = head.rstrip().rfind("\n")
        f_block = head[:cut] + "\n" + "\n".join(rows) + "\n\n" + tail
    md = md[:f_start] + f_block + md[f_end:]
    md = re.sub(r"\[筛选字段\]\(#筛选字段\d+\)", f"[筛选字段](#筛选字段{n_sel})", md, count=1)

    # ② 聚合维度：计数 + 清单行
    d_start = md.find("## 聚合维度（")
    d_end = md.find("## 指标（")
    d_block = md[d_start:d_end]
    d_block = re.sub(r"## 聚合维度（\d+）", f"## 聚合维度（{n_dim}）", d_block, count=1)
    dim_line = "、".join(str(f["name"]) for f in catalog["dimensions"]) + "。"
    lines = d_block.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("出库月份") and line.endswith("。"):
            lines[i] = dim_line
            break
    d_block = "\n".join(lines) + "\n"
    md = md[:d_start] + d_block + md[d_end:]
    md = re.sub(r"\[聚合维度\]\(#聚合维度\d+\)", f"[聚合维度](#聚合维度{n_dim})", md, count=1)

    # ③ 指标：计数 + 清单行
    m_start = md.find("## 指标（")
    m_end = md.find("## 基础字段定义")
    m_block = md[m_start:m_end]
    m_block = re.sub(r"## 指标（\d+）", f"## 指标（{n_met}）", m_block, count=1)
    met_line = "、".join(str(f["name"]) for f in catalog["metrics"]) + "。"
    lines = m_block.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("含税金额") and line.endswith("。"):
            lines[i] = met_line
            break
    m_block = "\n".join(lines) + "\n"
    md = md[:m_start] + m_block + md[m_end:]
    md = re.sub(r"\[指标\]\(#指标\d+\)", f"[指标](#指标{n_met})", md, count=1)

    # ④ 待补语义节（新指标的口径需人工补写）
    if extras["metrics"]:
        todo = "、".join(f"`{f['name']}`" for f in extras["metrics"])
        section = (
            "\n\n## 待补语义（sync_fields 同步，待人工确认业务口径）\n\n"
            f"- 指标：{todo}\n"
        )
        if "## 待补语义" in md:
            s2 = md.find("## 待补语义")
            md = md[:s2] + section.lstrip("\n")
        else:
            md = md.rstrip() + section

    PARAMS_FILE.write_text(md, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行时字段同步进内置文档（显式维护）")
    parser.add_argument("--write", action="store_true", help="同步写入 catalog.json 与 parameters.md")
    args = parser.parse_args()

    try:
        catalog, md, extras, vanished = _collect()
        total_new = sum(len(v) for v in extras.values())
        for group, items in extras.items():
            if items:
                print(f"[sync] 新增{group}: {'、'.join(str(f['name']) for f in items)}")
        for group, names in vanished.items():
            if names:
                print(f"[sync] 运行时缺失（不自动删除，请人工确认）: {group}: {'、'.join(names)}")
        if not total_new and not any(vanished.values()):
            print("[sync] 无差异：运行时字段与语义字典一致。")
            return 0
        if not args.write:
            print("[sync] 校对模式（不写盘）；确认后加 --write 同步。")
            return 0

        # catalog 入册
        for f in extras["dimensions"]:
            entry = dict(f)
            entry["annotation"] = "待补业务口径（sync_fields 同步）"
            entry["semantic"] = True
            catalog["dimensions"].append(entry)
        for f in extras["metrics"]:
            entry = dict(f)
            entry["annotation"] = "待补业务口径（sync_fields 同步）"
            entry["semantic"] = True
            catalog["metrics"].append(entry)
        for s in extras["selectors"]:
            entry = {k: s.get(k) for k in ("cdId", "name", "selectorType", "filterType", "fields", "targetField") if s.get(k) is not None}
            entry["enabledForMainQuery"] = True
            entry["aliases"] = []
            entry["annotation"] = "待补业务口径（sync_fields 同步）"
            entry["semantic"] = True
            catalog["selectors"].append(entry)
        catalog["catalogVersion"] = int(catalog.get("catalogVersion") or 0) + 1
        CATALOG_FILE.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        _update_params_md(catalog, extras)
        print(f"[sync] 已同步：catalog v{catalog['catalogVersion']}（"
              f"{len(catalog['selectors'])} 筛选/{len(catalog['dimensions'])} 维/{len(catalog['metrics'])} 指标）+ parameters.md")
        return 0
    except BiError as exc:
        print(json.dumps(exc.to_dict(), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
