"""M1 入口：跑 Planner，打印 plan + 成本，并 dump 事件供排查。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from paperscout.harness import make_harness
from paperscout.planner import plan

SEEDS = [
    "Training-Free Industrial Defect Generation with Diffusion Models",
    "AnomalyDiffusion: Few-Shot Anomaly Image Generation with Diffusion Model",
]


def main() -> None:
    seeds = sys.argv[1:] or SEEDS
    reports = Path(__file__).resolve().parent / "reports"
    reports.mkdir(exist_ok=True)

    harness = make_harness()
    print(">>> 启动 runtime，运行 Planner ...", flush=True)
    with harness:
        plan_obj, result = plan(harness, seeds)

    # dump 事件供排查 token 记账字段
    (reports / "_debug_planner_events.json").write_text(
        json.dumps(result.events, ensure_ascii=False, indent=2)
    )

    print("\n===== Planner 原始输出 =====")
    print(result.text)

    print("\n===== 解析出的 plan =====")
    if plan_obj is None:
        print("⚠️ 没能解析出 JSON，请看上面原始输出")
    else:
        (reports / "plan.json").write_text(json.dumps(plan_obj, ensure_ascii=False, indent=2))
        print(json.dumps(plan_obj, ensure_ascii=False, indent=2))
        print(f"\n(plan 已存到 {reports / 'plan.json'})")

    c = result.cost
    print("\n===== 成本 =====")
    print(f"AMiner 调用次数: {c.aminer_calls}  明细: {c.aminer_tools}")
    print(f"token: 输入 {c.input_tokens} / 输出 {c.output_tokens}")
    print(f"finish_reason: {result.finish_reason}")


if __name__ == "__main__":
    main()
