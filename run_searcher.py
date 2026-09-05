"""M2 入口：在某个子方向上跑一个 Searcher（默认第 0 个），验证检索+精读。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from paperscout.harness import make_harness
from paperscout.searcher import search

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"


def main() -> None:
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    plan = json.loads((REPORTS / "plan.json").read_text())
    area = plan["area"]
    seed_titles = [s["title"] for s in plan["seeds"]]
    subtopic = plan["subtopics"][idx]

    print(f">>> 子方向[{idx}]：{subtopic['name']}", flush=True)
    harness = make_harness()
    with harness:
        corpus, result = search(harness, area, subtopic, seed_titles, session_id=f"searcher-{idx}")

    (REPORTS / f"_debug_searcher_{idx}_events.json").write_text(
        json.dumps(result.events, ensure_ascii=False, indent=2)
    )

    print("\n===== Searcher 原始输出 =====")
    print(result.text)

    print("\n===== 解析出的语料 =====")
    if corpus is None:
        print("⚠️ 没能解析出 JSON")
    else:
        (REPORTS / f"corpus_{idx}.json").write_text(json.dumps(corpus, ensure_ascii=False, indent=2))
        papers = corpus.get("papers", [])
        print(f"共 {len(papers)} 篇；精读 {sum(1 for p in papers if p.get('read_detail'))} 篇")
        for p in papers:
            mark = "★" if p.get("read_detail") else " "
            print(f"  {mark} [{p.get('year')}|{p.get('citation_bucket')}] {p.get('title')}")
        print(f"(语料已存到 {REPORTS / f'corpus_{idx}.json'})")

    c = result.cost
    print("\n===== 成本 =====")
    print(f"AMiner 调用次数: {c.aminer_calls}  明细: {c.aminer_tools}")
    print(f"token: 输入 {c.input_tokens} / 输出 {c.output_tokens}  finish: {result.finish_reason}")


if __name__ == "__main__":
    main()
