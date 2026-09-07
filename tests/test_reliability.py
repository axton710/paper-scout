import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from paperscout.coordinator import normalize_followups
from paperscout.corpus import merge_corpora, rank, render_compact
from paperscout.gap import align_verdicts, normalize_gaps
from paperscout.harness import Cost, _cost_of, run_agent
from paperscout.planner import validate_plan
from paperscout.provenance import ground_corpus, traceability
from paperscout.report import _gaps, _survey_headings, priority_reading
from paperscout.run_store import RunStore
from paperscout.searcher import normalize_corpus
from paperscout.validation import OutputError


def events(record=None, error=False):
    record = record or {'id': 'a', 'title': 'Paper A', 'year': 2024, 'abstract': 'Test abstract'}
    return [
        {'type': 'tool/call', 'data': {'callId': 'call-1', 'name': 'mcp__aminer__get_paper_detail', 'arguments': '{"id":"a"}'}},
        {'type': 'tool/result', 'seq': 2, 'data': {'message': {'content': [
            {'toolCallId': 'call-1', 'isError': error, 'content': [{'type': 'text', 'text': json.dumps({'code': 200, 'data': record})}]}]}}},
    ]


class ReliabilityTest(unittest.TestCase):
    def test_missing_or_duplicate_verdict_never_passes(self):
        raw = {'gaps': [{'id': 'gap-1', 'gap': 'Question'}]}
        for entries in ([], [{'id': 'wrong', 'verdict': 'keep', 'reason': 'ok'}],
                        [{'id': 'gap-1', 'verdict': 'keep', 'reason': 'ok'}] * 2):
            checked = align_verdicts(raw, {'verified': entries})
            self.assertEqual(checked[0]['verdict'], 'unverified')
            self.assertIn('未核验', _gaps(entries, raw))
            self.assertNotIn('支持继续验证', _gaps(entries, raw))

    def test_verdict_matches_id_despite_rewording(self):
        checked = align_verdicts({'gaps': [{'id': 'g1', 'gap': 'Original'}]},
                                 {'verified': [{'id': 'g1', 'gap': 'Reworded', 'verdict': 'keep', 'reason': 'Evidence'}]})
        self.assertEqual(checked[0]['verdict'], 'keep')
        self.assertEqual(checked[0]['gap'], 'Original')

    def test_merge_upgrades_evidence_without_mutating_input(self):
        first = {'id': 'a', 'title': 'A', 'read_detail': False, 'method': ''}
        second = {**first, 'read_detail': True, 'method': 'Abstract method', 'provenance': [{'tool_call_id': 'c'}]}
        merged = merge_corpora([{'subtopic': 'one', 'papers': [first]}, {'subtopic': 'two', 'papers': [second]}])[0]
        self.assertTrue(merged['read_detail'])
        self.assertEqual(merged['method'], 'Abstract method')
        self.assertEqual(len(merged['observations']), 2)
        self.assertEqual(merged['subtopics'], ['one', 'two'])
        self.assertEqual(first['method'], '')
        self.assertIn('摘要', render_compact([merged]))

    def test_actual_citation_bucket_ranks_classic_above_low_cited(self):
        papers = [{'id': 'classic', 'citation_bucket': '1001-5000', 'year': 2021},
                  {'id': 'new', 'citation_bucket': '1-10', 'year': 2025}]
        self.assertEqual(rank(papers)[0]['id'], 'classic')

    def test_output_boundaries(self):
        with self.assertRaises(OutputError):
            normalize_followups({'followups': [{'name': 'x', 'queries': 'diffusion'}]})
        with self.assertRaises(OutputError):
            normalize_corpus({'papers': [{'id': 'a', 'title': 'A', 'year': '2024'}]}, 'x')
        self.assertEqual(len(normalize_corpus({'papers': [{'id': str(i), 'title': str(i)} for i in range(12)]}, 'x')['papers']), 8)
        with self.assertRaises(OutputError):
            validate_plan({'area': 'x', 'seeds': [{'title': 'seed'}], 'subtopics': []})

    def test_unmatched_paper_or_tool_error_cannot_be_grounded(self):
        paper = {'id': 'a', 'title': 'Invented', 'read_detail': True}
        with self.assertRaises(OutputError):
            ground_corpus({'papers': [paper]}, events(), 's1', [])
        with self.assertRaises(OutputError):
            ground_corpus({'papers': [{'id': 'a', 'title': 'Paper A'}]}, events(error=True), 's1', [])

    def test_grounding_uses_actual_metadata_and_raw_source(self):
        corpus = ground_corpus({'papers': [{'id': 'a', 'title': 'Paper A', 'year': 2030, 'read_detail': False}]}, events(), 's1', [])
        paper = corpus['papers'][0]
        self.assertEqual(paper['year'], 2024)
        self.assertTrue(paper['read_detail'])
        self.assertEqual(paper['provenance'][0]['tool_call_id'], 'call-1')
        self.assertEqual(traceability([paper], {'s1': events()})['verified_exist'], 1)
        self.assertEqual(traceability([paper], {})['verified_exist'], 0)

    def test_token_usage_counts_once_and_preserves_cache(self):
        usage = {'inputTokens': 10, 'outputTokens': 4, 'cacheReadTokens': 20}
        cost = _cost_of([{'type': 'assistant/chunk', 'data': {'usage': usage}},
                         {'type': 'assistant/message', 'data': {'usage': usage}}])
        self.assertEqual((cost.input_tokens, cost.output_tokens, cost.cache_read_tokens), (10, 4, 20))

    def test_truncation_saved_before_error(self):
        with tempfile.TemporaryDirectory() as tmp, patch('paperscout.harness.POLICIES', Path(tmp) / 'policies'):
            harness = SimpleNamespace(artifact_dir=Path(tmp), run=lambda *a, **k: SimpleNamespace(final_response='partial', events=[], finish_reason='failed'))
            with self.assertRaises(OutputError):
                run_agent(harness, 'prompt', 'test')
            saved = list((Path(tmp) / 'agents').glob('*.json'))
            self.assertEqual(len(saved), 1)
            self.assertEqual(json.loads(saved[0].read_text())['text'], 'partial')

    def test_stage_resume_does_not_repeat_successful_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore.create(Path(tmp), {'area': 'test'}, 'hash')
            self.assertEqual(store.stage('search', lambda: {'papers': [1]}), {'papers': [1]})
            with self.assertRaisesRegex(ValueError, 'bad output'):
                store.stage('gap', lambda: (_ for _ in ()).throw(ValueError('bad output')))
            resumed = RunStore(store.path)
            self.assertEqual(resumed.stage('search', lambda: self.fail('paid work repeated')), {'papers': [1]})
            self.assertEqual(resumed.stage('gap', lambda: {'gaps': []}), {'gaps': []})
            self.assertEqual(resumed.manifest['stages']['gap']['attempts'], 2)

    def test_report_priorities_cover_routes_and_headings_are_nested(self):
        papers = [{'id': str(i), 'subtopics': [route]} for i, route in enumerate(['a', 'a', 'b', 'c', 'd', 'e'])]
        self.assertEqual({p['subtopics'][0] for p in priority_reading(papers)}, {'a', 'b', 'c', 'd', 'e'})
        result = _survey_headings('# Survey\n## Route\n```python\n# comment\n```')
        self.assertTrue(result.startswith('### Survey\n#### Route'))
        self.assertIn('\n# comment\n', result)


if __name__ == '__main__':
    unittest.main()
