"""共享证据板与定向补搜的纯函数测试，不调用模型或外部学术服务。"""

from __future__ import annotations

import unittest

from paperscout.coordinator import MAX_FOLLOWUPS, normalize_followups
from paperscout.corpus import build_evidence_board
from paperscout.searcher import build_prompt
from run_pipeline import _cache_matches_subtopic


class CollaborationTest(unittest.TestCase):
    def test_evidence_board_keeps_multiple_agents_observations(self) -> None:
        board = build_evidence_board([
            {
                "agent": "searcher-0",
                "subtopic": "少样本生成",
                "scope": "少样本方法",
                "covered_claims": ["论文 A 使用少样本训练"],
                "open_questions": ["是否跨类别泛化"],
                "papers": [{"id": "a", "title": "Paper A", "read_detail": True, "method": "M1"}],
            },
            {
                "agent": "searcher-1",
                "subtopic": "跨类别",
                "scope": "跨类别泛化",
                "papers": [{"id": "a", "title": "Paper A", "read_detail": False, "contribution": "C1"}],
            },
        ])

        self.assertEqual(len(board["coverage"]), 2)
        self.assertEqual(len(board["papers"]), 1)
        paper = board["papers"][0]
        self.assertEqual(paper["found_by"], ["searcher-0", "searcher-1"])
        self.assertEqual([e["source"] for e in paper["evidence"]], ["abstract", "search_result"])

    def test_coordinator_caps_and_normalizes_followups(self) -> None:
        tasks = normalize_followups({
            "followups": [
                {"name": f"task-{i}", "queries": ["q1", "q2", "q3", "q4"], "known_paper_ids": ["a"]}
                for i in range(MAX_FOLLOWUPS + 2)
            ]
        })

        self.assertEqual(len(tasks), MAX_FOLLOWUPS)
        self.assertEqual(tasks[0]["queries"], ["q1", "q2", "q3"])
        self.assertEqual(tasks[0]["known_paper_ids"], ["a"])

    def test_searcher_prompt_reuses_known_detail_without_blocking_search(self) -> None:
        prompt = build_prompt(
            "test area",
            {"name": "followup", "rationale": "gap", "scope": "test scope", "exclude": "old work", "queries": ["query"]},
            ["seed"],
            [{"id": "known-id", "title": "Known Paper"}],
            max_detail_reads=2,
        )

        self.assertIn("known-id: Known Paper", prompt)
        self.assertIn("不作为重点：old work", prompt)
        self.assertIn("不超过 2 篇", prompt)

    def test_cache_is_invalidated_when_subtopic_contract_changes(self) -> None:
        subtopic = {
            "name": "diffusion",
            "scope": "few-shot",
            "exclude": "GAN",
            "queries": ["diffusion anomaly"],
        }
        self.assertTrue(_cache_matches_subtopic({"subtopic": "diffusion", **subtopic}, subtopic))
        self.assertFalse(_cache_matches_subtopic({"subtopic": "diffusion"}, subtopic))
        self.assertFalse(_cache_matches_subtopic({"subtopic": "diffusion", **subtopic, "queries": ["GAN"]}, subtopic))


if __name__ == "__main__":
    unittest.main()
