"""生成检索计划；原始 Agent 输出保存在 runs/standalone/agents。"""
from __future__ import annotations

import argparse
from pathlib import Path

from paperscout.harness import make_harness
from paperscout.planner import plan
from paperscout.run_store import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('seeds', nargs='+', help='1–2 个种子论文标题')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'reports' / 'plan.json')
    args = parser.parse_args()
    if not 1 <= len(args.seeds) <= 2:
        parser.error('需要 1–2 篇种子论文')
    with make_harness() as harness:
        obj, result = plan(harness, args.seeds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, obj)
    print(f'计划: {args.output}\n原始记录: {result.session_id}\n成本: {result.cost}')


if __name__ == '__main__':
    main()
