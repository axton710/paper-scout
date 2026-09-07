"""直接问模型与检索语料的溯源对照；查无结果记为 unresolved，不等于编造。运行会产生 API 费用。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from experiments.audit_traceability import audit_run
from paperscout.harness import extract_json, make_harness, run_agent
from paperscout.provenance import _title_key, retrieval_records
from paperscout.run_store import write_json
from paperscout.validation import object_value, list_value, text_value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan = json.loads((args.source_run / 'plan.json').read_text())
    prompt = ('仅凭已有知识，列出与以下种子高度相关的 20 篇真实论文；输出 JSON '
              '{"papers":[{"title":"英文标题","year":2024}]}。不要调用工具。\n'
              + json.dumps(plan['seeds'], ensure_ascii=False))
    with make_harness(artifact_dir=args.output_dir, use_aminer=False) as harness:
        response = run_agent(harness, prompt, 'control-memory')
    papers = list_value(object_value(extract_json(response.text), 'control').get('papers'), 'control.papers')
    titles = list(dict.fromkeys(text_value(object_value(p, 'paper').get('title'), 'paper.title') for p in papers))
    if len(titles) != 20:
        raise ValueError(f'对照组需要 20 个不重复标题，实际 {len(titles)}')
    verify_prompt = ('逐个调用 search_paper_by_title 检索以下标题，每个只调用一次；最后输出核验完成。\n'
                     + json.dumps(titles, ensure_ascii=False))
    with make_harness(artifact_dir=args.output_dir) as harness:
        verification = run_agent(harness, verify_prompt, 'control-retrieval', tool_limits={'search_paper_by_title': len(titles)})
    records = retrieval_records(verification.events, verification.session_id)
    matched_titles = {_title_key(s['record']['title']) for sources in records.values() for s in sources}
    verified = sum(_title_key(t) in matched_titles for t in titles)
    result = {'control_llm': {'papers_listed': len(titles), 'verified_exist': verified,
                             'unresolved': len(titles) - verified, 'traceable_at_generation': 0,
                             'hallucination_rate_pct': None},
              'paper_scout': audit_run(args.source_run), 'raw_llm_papers': papers,
              'note': '未命中不证明论文虚构；严格标题匹配未通过时保留 unresolved，禁止预设零幻觉。'}
    write_json(args.output_dir / 'comparison.json', result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
