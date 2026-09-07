"""同一首轮证据上比较预先确定的静态补搜与 Coordinator 补搜。运行会产生 API 费用。"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from paperscout.coordinator import coordinate
from paperscout.corpus import build_evidence_board, merge_corpora
from paperscout.harness import make_harness
from paperscout.planner import validate_plan
from paperscout.run_store import RunStore, code_fingerprint, write_json
from run_pipeline import ROOT, _search_round


def static_tasks(plan: dict) -> list[dict]:
    # 在读取 Coordinator 决策前，仅根据原计划固定两项任务，避免人为挑选有利基线。
    tasks = []
    for slot in range(2):
        topics = plan['subtopics'][slot::2]
        queries = list(dict.fromkeys(q for topic in topics for q in topic['queries']))[:3]
        tasks.append({'name': f'静态补搜-{slot}', 'scope': '；'.join(t['scope'] for t in topics),
                      'rationale': '沿原计划继续搜索，未使用共享证据板决定方向',
                      'exclude': '', 'queries': queries, 'known_paper_ids': []})
    return tasks


def metrics(first: list[dict], added: list[dict], gold: set[str]) -> dict:
    before, after = merge_corpora(first), merge_corpora(first + added)
    before_ids, after_ids = {p['id'] for p in before}, {p['id'] for p in after}
    return {'unique_papers': len(after), 'new_papers': len(after_ids - before_ids),
            'gold_recall': len(after_ids & gold) / len(gold) if gold else None,
            'note': '新增论文数不等于召回率；未提供人工 gold set 时 recall 为 null'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True, help='包含已验证首轮 corpus_i.json 的运行目录')
    parser.add_argument('--gold', type=Path, help='人工相关论文 ID 的 JSON 列表')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'runs' / 'experiments')
    args = parser.parse_args()
    plan = validate_plan(json.loads((args.source_run / 'plan.json').read_text()))
    first = [json.loads((args.source_run / f'corpus_{i}.json').read_text()) for i in range(len(plan['subtopics']))]
    if any(not p.get('provenance') for p in merge_corpora(first)):
        parser.error('首轮缺少逐篇 provenance，请先运行新版 pipeline')
    baseline = static_tasks(plan)
    gold = set(json.loads(args.gold.read_text())) if args.gold else set()
    store = RunStore.create(args.output_dir, plan, code_fingerprint(ROOT))
    write_json(store.path / 'static_tasks_precommitted.json', baseline)
    write_json(store.path / 'first_round.json', first)
    board = build_evidence_board(first)
    try:
        with make_harness(artifact_dir=store.path / 'coordinator', use_aminer=False) as harness:
            adaptive, decision = coordinate(harness, plan['area'], board)
        write_json(store.path / 'adaptive_tasks.json', adaptive)
        # 静态组只接收额度数量，不接收自适应组的查询内容或选择依据。
        baseline = [dict(task, queries=task['queries'][:len(adaptive[i]['queries'])]) for i, task in enumerate(baseline[:len(adaptive)])]
        for i, task in enumerate(adaptive):
            common = min(len(task['queries']), len(baseline[i]['queries']))
            task['queries'] = task['queries'][:common]
            baseline[i]['queries'] = baseline[i]['queries'][:common]
        result = {'source_run': str(args.source_run.resolve()),
                  'tool_budget_per_arm': {'search_paper': sum(len(t['queries']) for t in adaptive), 'get_paper_detail': len(adaptive) * 2},
                  'coordinator_cost': decision.cost.__dict__,
                  'limitations': '匹配补搜工具额度和每任务输出上限，未强行匹配实际 token；调度成本单列。单次结果不能证明稳定收益。',
                  'arms': {}}
        known = [p for p in merge_corpora(first) if p.get('read_detail')]
        for name, tasks in [('static', baseline), ('adaptive', adaptive)]:
            arm = RunStore.create(store.path / name, plan, code_fingerprint(ROOT))
            start = time.perf_counter()
            added = _search_round(arm, plan, tasks, 'followup_corpus', known, 2)
            complete = len(added) == len(tasks)
            manifest = arm.finish('complete' if complete else 'failed')
            result['arms'][name] = {**metrics(first, added, gold), 'status': manifest['status'],
                                    'cost': manifest['cost'], 'wall_seconds': round(time.perf_counter() - start, 2),
                                    'run': str(arm.path)}
            write_json(arm.path / 'corpus.json', merge_corpora(first + added))
        result['comparison_valid'] = all(a['status'] == 'complete' for a in result['arms'].values())
        write_json(store.path / 'comparison.json', result)
        store.finish('complete' if result['comparison_valid'] else 'failed')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception:
        store.finish('failed')
        raise


if __name__ == '__main__':
    main()
