#!/usr/bin/env python3
"""任务层演练（常驻 · 零联网 · 零写入）：推进控制机制在**真实文档**上的在场回归 + 模板可用性校验。

诚实边界：本演练是**静态与模板级校验**（机制是否在场、是否互相指路、模板能否直接落地为四区工作区）；
真实执行行为由实战轨迹（过程记录第 6 节 + D 区）继续校准。

用法：
  python evolution/tests/run_task_drill.py          # 跑全部断言
输出：逐条 PASS/FAIL + 末尾 JSON 汇总（ok / passed / total / failed），退出码 0/1。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # leyao-seed-core/
sys.dont_write_bytecode = True                      # 运行期零写包（不在包内生成 __pycache__）
CONTROL = ROOT / "processor" / "control.md"
PROCESSOR = ROOT / "processor" / "PROCESSOR.md"
PLAN = ROOT / "processor" / "flow" / "2-plan.md"
EXECUTE = ROOT / "processor" / "flow" / "3-execute.md"
ACCEPT = ROOT / "processor" / "flow" / "4-accept.md"
TEMPLATE = ROOT / "processor" / "templates" / "过程记录.md"
README = ROOT / "evolution" / "tests" / "README.md"
ZONES = ("01-原始材料区", "02-任务执行区", "03-结果交付区", "04-归档区")

checks: list = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else "  | " + detail))


def text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------- A 机制在场（control.md = 推进控制硬判据源） ----------
control = text(CONTROL)
check("A1 control.md 含推进控制三节（循环上限 / 停滞检测 / 回退与重走）",
      all(k in control for k in ("循环上限", "停滞检测", "回退与重走")))
check("A2 上限：必须设 + 超限动作明确 + 不写死数字",
      "不得突破" in control and "超限动作" in control and "本文件不写死数字" in control)
check("A3 停滞判据=三条件（等价 / 无新假设 / 无状态改变）且动作=回退",
      all(k in control for k in ("连续 **2 步**", "等价", "无新假设", "无状态改变"))
      and "触发**回退**" in control)
check("A4 回退四类触发齐备 + 三条约束（留痕 / 复用已验证 / 计入上限）",
      all(k in control for k in ("停滞（上节）", "判据冲突", "资产不匹配", "验收不通过"))
      and all(k in control for k in ("留痕", "复用已验证结论", "计入上限")))

execute = text(EXECUTE)
check("A5 防冲突闭环：执行步「路线不改」与「换工具=回退」互指",
      "路线不改" in execute and "回退与重走" in execute)
task_set_src = text(ROOT / "evolution" / "tests" / "run_task_set.py")
check("A6 台账只写用户区：用 paths.TASK_SET_RESULTS_F 且不再写包内用例文件（红线）",
      "paths.TASK_SET_RESULTS_F" in task_set_src
      and "SET_FILE.write_text" not in task_set_src
      and "TASK_SET_RESULTS_F" in text(ROOT / "evolution" / "paths.py"))

# ---------- B 各步判据齐备 ----------
plan = text(PLAN)
check("B1 2-plan 出口判据含：不适用条件 / 事实与猜测 / 上限",
      all(k in plan for k in ("不适用条件", "事实与猜测分开", "上限已定")))
check("B2 3-execute 含工具循环三要素 + 满足即停 + 停止理由留证",
      all(k in execute for k in ("工具循环三要素", "满足即停", "停止 / 回退理由已留证")))
accept = text(ACCEPT)
check("B3 4-accept 含红线单列 + 过程成本对照上限",
      all(k in accept for k in ("红线单列", "过程成本已记录", "与规划上限对照")))
deliver = text(ROOT / "processor" / "flow" / "5-deliver.md")
check("B4 5-deliver 含「任务集台账登记」动作与命令（交付后原生入口）",
      "任务集台账已登记" in deliver and "run_task_set.py --record" in deliver)
check("B5 多阶段/长任务：2-plan 出口判据含需求契约表 · 3-execute 含阶段出口勾选（防目标漂移）",
      "需求契约表" in plan and "阶段出口勾选" in execute and "全局目标此刻仍成立" in execute)
check("B6 验收 rubric + 不可逆步骤前置标识（2-plan）· rubric 对照（4-accept）",
      all(k in plan for k in ("验收 rubric", "不可逆步骤已前置标识")) and "对照 rubric" in accept)
check("B7 用户纠正必须采集（5-deliver 硬判据：--override 是最强学习信号，漏采=白丢）",
      "用户纠正必须采集" in deliver and "--override" in deliver and "最强" in deliver)
check("B8 outcome 必须如实（三档定义 + 禁止 false pass + 成功需可执行证据）",
      all(k in deliver for k in ("outcome 必须如实", "false pass", "可执行证据", "不诚实")) and "warn" in text(ROOT / "evolution" / "grow.py"))

# ---------- C 模板可用性（复制即用） ----------
tpl = text(TEMPLATE)
check("C1 模板含第 6 节四行（循环 / 停滞 / 回退 / 成本与红线）",
      all(k in tpl for k in ("## 6. 执行循环与回退", "循环：第", "停滞：", "回退：", "成本与红线")))
tmp = Path(tempfile.mkdtemp(prefix="task_drill_"))
try:
    for z in ZONES:
        (tmp / z).mkdir()
    (tmp / ZONES[1] / "过程记录.md").write_text(tpl, encoding="utf-8")
    check("C2 模板可直接落地为四区工作区（根只四区 + 过程记录就位）",
          all((tmp / z).is_dir() for z in ZONES)
          and (tmp / ZONES[1] / "过程记录.md").is_file()
          and not [p for p in tmp.iterdir() if p.name not in ZONES])
finally:
    shutil.rmtree(tmp, ignore_errors=True)
check("C3 形状与模板含需求契约表（R1 行 + 状态列 + 第 8 节形状）",
      "需求契约表" in tpl and "R1" in tpl and "状态" in tpl
      and "## 8. 需求契约表" in text(ROOT / "processor" / "shapes.md"))

# ---------- D 文档 ↔ 文档指路闭环 ----------
check("D1 PROCESSOR 实时控制节已指路推进控制（三术语在场）",
      all(k in text(PROCESSOR) for k in ("循环上限", "停滞检测", "回退与重走")))
check("D2 README 已登记本演练（命令清单与人工清单）",
      "run_task_drill.py" in text(README))

failed = [c["name"] for c in checks if not c["ok"]]
print(json.dumps({"ok": not failed, "passed": len(checks) - len(failed),
                  "total": len(checks), "failed": failed}, ensure_ascii=False))
sys.exit(1 if failed else 0)
