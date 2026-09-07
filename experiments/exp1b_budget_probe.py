"""实验一补充 · 「预算控制 off」实测探针。

预算控制里真正计费的旋钮是 get_paper_detail 的精读上限（AMiner 按调用计费）。
对同一个子方向跑两次：一次 capped（精读 ≤4，线上默认），一次 uncapped（精读放到 12，
让 agent 把保留的候选尽量读全）。对比 AMiner 计费调用次数与 token，得到去掉上限后的实测增量。

只跑 1 个子方向（成本可控），结果写入 experiments/exp1b_result.json。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from paperscout.harness import Cost, make_harness
from paperscout.searcher import search

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
OUT = Path(__file__).resolve().parent / "exp1b_result.json"

SUBTOPIC_INDEX = 0
UNCAPPED = 12


def _run(subtopic, area, seeds, session, max_detail):
    h = make_harness()
    t0 = time.perf_counter()
    with h:
        corpus, res = search(h, area, subtopic, seeds, session, max_detail_reads=max_detail)
    dt = time.perf_counter() - t0
    reads = sum(1 for p in (corpus or {}).get("papers", []) if p.get("read_detail")) if corpus else 0
    return {
        "max_detail_reads_setting": max_detail,
        "actual_detail_reads": reads,
        "candidates": len((corpus or {}).get("papers", [])) if corpus else 0,
        "aminer_calls": res.cost.aminer_calls,
        "aminer_tools": res.cost.aminer_tools,
        "input_tokens": res.cost.input_tokens,
        "output_tokens": res.cost.output_tokens,
        "wall_seconds": round(dt, 1),
    }


def main() -> None:
    plan = json.loads((REPORTS / "plan.json").read_text())
    area = plan["area"]
    seeds = [s["title"] for s in plan["seeds"]]
    sub = plan["subtopics"][SUBTOPIC_INDEX]

    print(f"探针子方向：{sub['name']}", flush=True)
    print("== capped（精读 ≤4）==", flush=True)
    capped = _run(sub, area, seeds, "probe-capped", 4)
    print(json.dumps(capped, ensure_ascii=False), flush=True)
    print("== uncapped（精读 ≤12）==", flush=True)
    uncapped = _run(sub, area, seeds, "probe-uncapped", UNCAPPED)
    print(json.dumps(uncapped, ensure_ascii=False), flush=True)

    d_detail = uncapped["actual_detail_reads"] - capped["actual_detail_reads"]
    d_aminer = uncapped["aminer_calls"] - capped["aminer_calls"]
    result = {
        "subtopic": sub["name"],
        "capped": capped,
        "uncapped": uncapped,
        "delta_detail_reads": d_detail,
        "delta_aminer_calls": d_aminer,
        "aminer_calls_growth_pct": round(d_aminer / max(capped["aminer_calls"], 1) * 100, 1),
        "note": "单子方向实测；全流程 4 首轮 + 2 补搜同比放大",
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print("\n===== 预算控制 off 探针结果 =====", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
