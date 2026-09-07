"""将模型论文笔记与真实工具返回逐条对齐，不把模型填写的 ID 当作证据。"""
from __future__ import annotations

import json
import re

from .validation import OutputError


def _paper_records(value):
    if isinstance(value, dict):
        if isinstance(value.get('id'), str) and isinstance(value.get('title'), str):
            yield value
        for child in value.values():
            yield from _paper_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _paper_records(child)


def retrieval_records(events: list, session_id: str) -> dict[str, list[dict]]:
    calls = {e['data']['callId']: e['data'] for e in events
             if e.get('type') == 'tool/call' and e.get('data', {}).get('name', '').startswith('mcp__aminer__')}
    records: dict[str, list[dict]] = {}
    for event in events:
        if event.get('type') != 'tool/result':
            continue
        for block in event.get('data', {}).get('message', {}).get('content', []):
            call_id = block.get('toolCallId')
            if call_id not in calls or block.get('isError'):
                continue
            for content in block.get('content', []):
                if content.get('type') != 'text':
                    continue
                try:
                    raw = json.loads(content['text'])
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict) and (raw.get('success') is False or raw.get('code', 200) != 200):
                    continue
                for paper in _paper_records(raw):
                    records.setdefault(paper['id'], []).append({
                        'session_id': session_id, 'tool_call_id': call_id,
                        'tool': calls[call_id]['name'], 'result_seq': event.get('seq'),
                        'record': paper,
                    })
    return records


def _title_key(title: str) -> str:
    return re.sub(r'[^\w]', '', title.casefold())


def ground_corpus(corpus: dict, events: list, session_id: str, known_papers: list[dict]) -> dict:
    records = retrieval_records(events, session_id)
    for known in known_papers:
        records.setdefault(known['id'], []).extend(known.get('provenance', []))
    for paper in corpus['papers']:
        sources = [s for s in records.get(paper['id'], [])
                   if _title_key(s['record']['title']) == _title_key(paper['title'])]
        if not sources:
            raise OutputError(f"论文 {paper['id']} / {paper['title']} 未匹配到真实工具返回，不能进入已验证语料")
        paper['provenance'] = sources
        # 元数据与精读标记由原始返回决定；模型只负责总结文字。
        detail = [s for s in sources if s['tool'].endswith('__get_paper_detail')
                  and (s['record'].get('abstract') or s['record'].get('abstract_zh'))]
        authoritative = {}
        for source in sources + detail:
            authoritative.update(source['record'])
        for target, source in [('title', 'title'), ('year', 'year'), ('citation_bucket', 'n_citation_bucket'), ('doi', 'doi')]:
            paper.pop(target, None)
            if source in authoritative:
                paper[target] = authoritative[source]
        paper['read_detail'] = bool(detail)
        paper['evidence_source'] = 'abstract' if detail else 'search_result'
        paper['limitation_kind'] = 'inference' if paper.get('limitation') else 'unknown'
    return corpus


def traceability(papers: list[dict], events_by_session: dict[str, list]) -> dict:
    records = {session: retrieval_records(events, session) for session, events in events_by_session.items()}
    verified = []
    for paper in papers:
        if any(_title_key(r['record']['title']) == _title_key(paper['title'])
               for session in records.values() for r in session.get(paper['id'], [])):
            verified.append(paper['id'])
    return {'papers': len(papers), 'verified_exist': len(verified),
            'traceable_to_retrieval': len(verified), 'unresolved': len(papers) - len(verified),
            'verified_ids': verified}
