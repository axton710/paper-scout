"""单个 Searcher 调试，使用独立输出目录，不覆盖 pipeline 的运行证据。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from paperscout.harness import make_harness
from paperscout.planner import validate_plan
from paperscout.run_store import write_json
from paperscout.searcher import search


def main():
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('index', type=int, nargs='?', default=0)
    parser.add_argument('--plan', type=Path, default=root / 'reports' / 'plan.json')
    args = parser.parse_args()
    plan = validate_plan(json.loads(args.plan.read_text()))
    if not 0 <= args.index < len(plan['subtopics']):
        parser.error('子方向编号超出计划范围')
    with make_harness() as harness:
        corpus, result = search(harness, plan['area'], plan['subtopics'][args.index],
                                [s['title'] for s in plan['seeds']], f'searcher-{args.index}')
    out = root / 'runs' / 'standalone' / f'{result.session_id}.corpus.json'
    write_json(out, corpus)
    print(f'语料: {out}\n成本: {result.cost}')


if __name__ == '__main__':
    main()
