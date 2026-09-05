"""端到端编排：plan → 各子方向 Searcher → Triage → 综述 → Gap+Verifier → 报告。

已存在 reports/corpus_i.json 的子方向会复用缓存（省钱）；成本只累计本次真正发生的调用。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from paperscout.coordinator import coordinate
from paperscout.corpus import build_evidence_board, merge_corpora, rank, render_compact
from paperscout.gap import reflect_gaps
from paperscout.harness import Cost, make_harness
from paperscout.report import assemble
from paperscout.searcher import search
from paperscout.synthesizer import synthesize

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"
FOLLOWUP_DETAIL_READS = 2
MAX_PARALLEL_SEARCHERS = 3


def _annotate_corpus(corpus: dict, subtopic: dict, agent: str) -> dict:
    """旧缓存没有协作字段，运行时补齐以保持已有检索结果可复用。"""
    corpus = dict(corpus)
    corpus["agent"] = agent
    corpus.setdefault("scope", subtopic.get("scope", ""))
    corpus.setdefault("exclude", subtopic.get("exclude", ""))
    corpus.setdefault("queries", subtopic.get("queries", []))
    corpus.setdefault("covered_claims", [])
    corpus.setdefault("open_questions", [])
    return corpus


def _cache_matches_subtopic(corpus: dict, subtopic: dict) -> bool:
    """检索边界变更后必须失效缓存，否则 Coordinator 会根据过期证据调度。"""
    return (
        corpus.get("subtopic") == subtopic.get("name")
        and corpus.get("scope") == subtopic.get("scope", "")
        and corpus.get("exclude") == subtopic.get("exclude", "")
        and corpus.get("queries") == subtopic.get("queries", [])
    )


def _search_with_own_harness(area: str, subtopic: dict, seed_titles: list[str], session_id: str,
                              known_papers: list[dict] | None = None,
                              max_detail_reads: int = 4):
    # Harness 的 RPC 客户端不保证线程安全；每个并行 agent 使用独立进程隔离调用。
    harness = make_harness()
    with harness:
        return search(
            harness, area, subtopic, seed_titles, session_id,
            known_papers=known_papers, max_detail_reads=max_detail_reads,
        )


def main() -> None:
    plan = json.loads((REPORTS / "plan.json").read_text())
    area = plan["area"]
    seeds = plan["seeds"]
    seed_titles = [s["title"] for s in seeds]
    total = Cost()

    # M2-R1: 各子方向独立检索。缓存命中时保留原结果，未命中时并行探索。
    corpora: list[dict | None] = [None] * len(plan["subtopics"])
    pending = {}
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_SEARCHERS, len(plan["subtopics"]))) as pool:
        for i, sub in enumerate(plan["subtopics"]):
            cache = REPORTS / f"corpus_{i}.json"
            if cache.exists():
                cached = json.loads(cache.read_text())
                if _cache_matches_subtopic(cached, sub):
                    print(f"[{i}] 复用缓存 {cache.name}", flush=True)
                    corpora[i] = _annotate_corpus(cached, sub, f"searcher-{i}")
                    continue
                print(f"[{i}] 缓存与当前子方向不匹配，重新检索", flush=True)
            print(f"[{i}] 并行检索：{sub['name']}", flush=True)
            future = pool.submit(_search_with_own_harness, area, sub, seed_titles, f"searcher-{i}")
            pending[future] = (i, sub, cache)

        for future in as_completed(pending):
            i, sub, cache = pending[future]
            corpus, result = future.result()
            total.merge(result.cost)
            if corpus:
                corpus = _annotate_corpus(corpus, sub, f"searcher-{i}")
                cache.write_text(json.dumps(corpus, ensure_ascii=False, indent=2))
                corpora[i] = corpus
            else:
                print(f"[{i}] ⚠️ 解析失败，跳过")

    first_round = [corpus for corpus in corpora if corpus]
    board = build_evidence_board(first_round)
    (REPORTS / "evidence_board.json").write_text(json.dumps(board, ensure_ascii=False, indent=2))

    # M2-R2: 只在首轮的证据缺口上补搜，避免所有 Searcher 无差别重跑。
    coordinator_harness = make_harness()
    with coordinator_harness:
        followups, coordination = coordinate(coordinator_harness, area, board)
    total.merge(coordination.cost)
    (REPORTS / "followups.json").write_text(json.dumps(followups, ensure_ascii=False, indent=2))
    print(f"Coordinator 派发 {len(followups)} 个定向补搜任务", flush=True)

    first_papers = {paper.get("id"): paper for paper in merge_corpora(first_round) if paper.get("id")}
    followup_corpora = []
    with ThreadPoolExecutor(max_workers=len(followups) or 1) as pool:
        pending = {}
        for i, task in enumerate(followups):
            known_papers = [first_papers[pid] for pid in task.get("known_paper_ids", []) if pid in first_papers]
            print(f"[补搜 {i}] {task['name']}", flush=True)
            future = pool.submit(
                _search_with_own_harness, area, task, seed_titles, f"followup-{i}",
                known_papers, FOLLOWUP_DETAIL_READS,
            )
            pending[future] = (i, task)

        for future in as_completed(pending):
            i, task = pending[future]
            corpus, result = future.result()
            total.merge(result.cost)
            if corpus:
                corpus = _annotate_corpus(corpus, task, f"followup-{i}")
                (REPORTS / f"followup_corpus_{i}.json").write_text(
                    json.dumps(corpus, ensure_ascii=False, indent=2)
                )
                followup_corpora.append(corpus)
            else:
                print(f"[补搜 {i}] ⚠️ 解析失败，跳过")

    all_corpora = first_round + followup_corpora
    papers = merge_corpora(all_corpora)
    ranked = rank(papers)
    corpus_text = render_compact(ranked)
    print(f"合并语料 {len(papers)} 篇，开始综述 ...", flush=True)

    # 综述这步推理会吃掉不少输出预算，16000 会在思考阶段就被截断导致正文为空，给到 32000。
    harness = make_harness(max_tokens=32000)
    with harness:
        syn = synthesize(harness, area, corpus_text)
        total.merge(syn.cost)
        survey = syn.text

        # M5: Gap + Verifier + Reflection 闭环（生成→核验→回改重出→再核验）
        print("找创新点 + 反思核验 ...", flush=True)
        raw_gaps, verified, gap_cost, rounds = reflect_gaps(harness, survey, corpus_text)
        total.merge(gap_cost)
        print("  反思轮次：" + " → ".join(f"R{r['round']} {r['n']}条{r['verdicts']}" for r in rounds), flush=True)

    # M6: 组装报告
    report = assemble(area, seeds, ranked, survey, raw_gaps, verified, total, rounds)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = REPORTS / f"report-{ts}.md"
    out.write_text(report)

    print("\n========== 完成 ==========")
    print(f"报告：{out}")
    print(f"必读 {len(ranked)} 篇 | 创新点原始 {len(raw_gaps.get('gaps', []))} 条 | 核验 {len(verified)} 条")
    print(f"本次成本：AMiner {total.aminer_calls} 次 {total.aminer_tools} | token 入 {total.input_tokens}/出 {total.output_tokens}")


if __name__ == "__main__":
    main()
