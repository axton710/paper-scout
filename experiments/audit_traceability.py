"""离线核对逐篇论文与真实调用返回：不产生模型或 AMiner 费用。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from paperscout.provenance import traceability
from paperscout.run_store import write_json


def audit_run(path: Path) -> dict:
    papers = json.loads((path / 'corpus.json').read_text())
    records = [json.loads(p.read_text()) for p in (path / 'agents').glob('*.json')]
    events = {r['session_id']: r['events'] for r in records if 'events' in r}
    return traceability(papers, events)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    args = parser.parse_args()
    result = audit_run(args.run)
    write_json(args.run / 'traceability.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
