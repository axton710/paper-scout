"""隔离运行、有限补搜与阶段恢复：python run_pipeline.py --help。"""
from __future__ import annotations

import argparse
import fcntl
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from paperscout.coordinator import coordinate
from paperscout.corpus import build_evidence_board, merge_corpora, rank, render_compact
from paperscout.gap import reflect_gaps
from paperscout.harness import make_harness
from paperscout.planner import plan as make_plan, validate_plan
from paperscout.report import assemble
from paperscout.run_store import RunStore, code_fingerprint, write_json
from paperscout.searcher import search

ROOT = Path(__file__).resolve().parent
FOLLOWUP_DETAIL_READS = 2
MAX_PARALLEL_SEARCHERS = 3


def _annotate_corpus(corpus: dict, subtopic: dict, agent: str) -> dict:
    return {**corpus, 'agent': agent, 'scope': subtopic.get('scope', ''),
            'exclude': subtopic.get('exclude', ''), 'queries': subtopic.get('queries', [])}


def _cache_matches_subtopic(corpus: dict, subtopic: dict) -> bool:
    return (corpus.get('subtopic') == subtopic.get('name')
            and all(corpus.get(k) == subtopic.get(k, [] if k == 'queries' else '')
                    for k in ('scope', 'exclude', 'queries')))


def _search_with_own_harness(area, subtopic, seed_titles, session_id,
                             known_papers=None, max_detail_reads=4, artifact_dir=None):
    # RPC 客户端不共享；每个并行检索有独立运行时及预算。
    with make_harness(artifact_dir=artifact_dir) as harness:
        return search(harness, area, subtopic, seed_titles, session_id,
                      known_papers=known_papers, max_detail_reads=max_detail_reads)


def _search_round(store, plan, tasks, prefix, known=None, max_detail_reads=4):
    corpora = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_SEARCHERS, len(tasks)) or 1) as pool:
        pending = {}
        for i, task in enumerate(tasks):
            name = f'{prefix}_{i}'
            def operation(task=task, name=name):
                corpus, _ = _search_with_own_harness(
                    plan['area'], task, [s['title'] for s in plan['seeds']], name,
                    known or [], max_detail_reads, store.path)
                return _annotate_corpus(corpus, task, name)
            pending[pool.submit(store.stage, name, operation)] = i
        for future in as_completed(pending):
            i = pending[future]
            try:
                corpora[i] = future.result()
            except Exception as error:
                print(f'[{prefix}_{i}] 失败，报告将标注缺失方向: {error}', flush=True)
    return [c for c in corpora if c is not None]


def execute(store: RunStore, plan: dict) -> Path:
    stages = store.manifest['stages']
    # 首轮证据改变后，所有依赖该证据的阶段必须失效；完整阶段本身仍可复用。
    if any(stages.get(f'corpus_{i}', {}).get('status') != 'complete' for i in range(len(plan['subtopics']))):
        store.invalidate([name for name in stages if name != 'planning' and not name.startswith('corpus_')])
    elif stages.get('followups', {}).get('status') != 'complete' or any(name.startswith('followup_corpus_') and value['status'] != 'complete' for name, value in stages.items()):
        store.invalidate(['synthesis', 'gaps'])
    if (store.path / 'gaps.json').exists() and any(v['verdict'] == 'unverified' for v in json.loads((store.path / 'gaps.json').read_text())['verified']):
        store.invalidate(['gaps'])
    first = _search_round(store, plan, plan['subtopics'], 'corpus')
    if not first or not merge_corpora(first):
        raise RuntimeError('首轮没有可用论文；已保留失败记录，修复后用 --resume 恢复')
    board = build_evidence_board(first)
    write_json(store.path / 'evidence_board.json', board)

    def schedule():
        with make_harness(artifact_dir=store.path, use_aminer=False) as harness:
            tasks, _ = coordinate(harness, plan['area'], board)
            return tasks
    try:
        followups = store.stage('followups', schedule)
    except Exception as error:
        print(f'[followups] 调度失败，交付首轮部分结果: {error}', flush=True)
        followups = []
    known = [p for p in merge_corpora(first) if p.get('read_detail')]
    followup_corpora = _search_round(store, plan, followups, 'followup_corpus', known, FOLLOWUP_DETAIL_READS)
    ranked = rank(merge_corpora(first + followup_corpora))
    write_json(store.path / 'corpus.json', ranked)
    write_json(store.path / 'final_evidence_board.json', build_evidence_board(first + followup_corpora))
    corpus_text = render_compact(ranked)

    def synthesis():
        from paperscout.synthesizer import synthesize
        with make_harness(max_tokens=32000, artifact_dir=store.path, use_aminer=False) as harness:
            return synthesize(harness, plan['area'], corpus_text).text
    survey = store.stage('synthesis', synthesis)

    def gaps():
        with make_harness(max_tokens=32000, artifact_dir=store.path, use_aminer=False) as harness:
            raw, verified, _, rounds = reflect_gaps(harness, survey, corpus_text)
            return {'raw': raw, 'verified': verified, 'rounds': rounds}
    gap_result = store.stage('gaps', gaps)
    incomplete = any(s['status'] != 'complete' for s in store.manifest['stages'].values())
    incomplete |= any(v['verdict'] == 'unverified' for v in gap_result['verified'])
    manifest = store.finish('partial' if incomplete else 'complete')
    manifest['search_coverage'] = {'completed': len(first), 'planned': len(plan['subtopics']),
                                 'followups_completed': len(followup_corpora), 'followups_planned': len(followups)}
    write_json(store.path / 'manifest.json', manifest)
    report = assemble(plan['area'], plan['seeds'], ranked, survey, gap_result['raw'],
                      gap_result['verified'], store.cost()[0], gap_result['rounds'], manifest)
    out = store.path / 'report.md'
    out.write_text(report)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('seeds', nargs='*', help='1–2 个种子论文标题；省略时需要 --plan 或 --resume')
    parser.add_argument('--plan', type=Path, help='复用已有计划路径')
    parser.add_argument('--resume', type=Path, help='恢复 runs/<run-id>，复用成功阶段')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'runs')
    args = parser.parse_args()
    if args.resume and (args.plan or args.seeds):
        parser.error('--resume 使用运行内的冻结计划，不能同时提供种子论文或 --plan')
    if args.plan and args.seeds:
        parser.error('种子论文会自动生成计划，不能同时提供 --plan')
    fingerprint = code_fingerprint(ROOT)
    plan = None
    seed_titles = None
    if args.resume:
        store = RunStore(args.resume.resolve())
        if store.manifest['fingerprint'] != fingerprint:
            parser.error('代码或提示词已变化，请新建运行，避免复用旧证据')
        plan_path = store.path / 'plan.json'
        if plan_path.exists():
            plan = validate_plan(json.loads(plan_path.read_text()))
            if RunStore._plan_hash(plan) != store.manifest.get('plan_hash'):
                parser.error('运行内计划已被修改，请用 --plan 新建运行')
        else:
            seed_titles = store.manifest.get('seed_titles')
            if not isinstance(seed_titles, list) or not 1 <= len(seed_titles) <= 2 or not all(isinstance(title, str) and title.strip() for title in seed_titles):
                parser.error('运行缺少可恢复的冻结计划或种子论文')
    else:
        if args.plan:
            plan = validate_plan(json.loads(args.plan.read_text()))
            store = RunStore.create(args.output_dir.resolve(), fingerprint, plan=plan)
        else:
            if not 1 <= len(args.seeds) <= 2:
                parser.error('需要 1–2 篇种子论文，或使用 --plan / --resume')
            seed_titles = args.seeds
            store = RunStore.create(args.output_dir.resolve(), fingerprint, seed_titles=seed_titles)
    with (store.path / '.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('该运行已有进程执行，请等待结束')
        try:
            if plan is None:
                def planning():
                    with make_harness(artifact_dir=store.path) as harness:
                        generated, _ = make_plan(harness, seed_titles)
                        return generated
                plan = store.stage('planning', planning)
                store.set_plan(plan)
            out = execute(store, plan)
        except Exception:
            store.finish('failed')
            raise
    print(f'报告: {out}\n状态: {store.manifest["status"]}\n恢复命令: python run_pipeline.py --resume {store.path}')


if __name__ == '__main__':
    main()
