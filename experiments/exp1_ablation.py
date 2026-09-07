"""实验一 · 消融对比：关掉「两轮自适应补搜」，只跑首轮，隔离 Coordinator 的价值。

对同一个 plan.json 跑一次完整的「首轮 → 证据板 → Coordinator → 定向补搜」，
分别记录「仅首轮」与「两轮自适应」两个断面的：
  - 去重后论文数
  - 剩余覆盖缺口数（open_questions；补搜前 N → 补搜后 M，用一次判定 agent 语义核验）
  - token 消耗（输入/输出）
  - 耗时（wall clock）

同时用同一批新鲜语料量化第二个消融「关掉去重 / 缓存 / 预算控制」的价值：
  - 去重 off：合并前后论文数差（确定性，纯后处理）
  - 缓存 off：一次重跑会重复付出的首轮成本（= 实测首轮成本）
  - 预算控制 off（精读上限）：单子方向去掉 get_paper_detail 上限的实测增量探针

结果写入 experiments/exp1_result.json，不覆盖 reports/ 下的正式产物。
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from paperscout.coordinator import coordinate
from paperscout.corpus import build_evidence_board, merge_corpora
from paperscout.harness import Cost, extract_json, make_harness, run_agent
from paperscout.searcher import search

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"
OUT = Path(__file__).resolve().parent / "exp1_result.json"

FOLLOWUP_DETAIL_READS = 2
MAX_PARALLEL = 3
UNCAPPED_DETAIL_READS = 8  # 预算控制 off 探针：允许把保留的候选全部精读


def _search_isolated(area, subtopic, seed_titles, session_id, known=None, max_detail=4):
    # 每个并行 agent 独立进程隔离（RPC 客户端非线程安全），与 run_pipeline 一致。
    h = make_harness()
    with h:
        return search(h, area, subtopic, seed_titles, session_id, known_papers=known, max_detail_reads=max_detail)


def _count_open_questions(board: dict) -> int:
    return sum(len(cov.get("open_questions", [])) for cov in board.get("coverage", []))


def _collect_open_questions(board: dict) -> list[str]:
    qs = []
    for cov in board.get("coverage", []):
        for q in cov.get("open_questions", []):
            qs.append(q)
    return qs


_JUDGE_PROMPT = """你是文献调研的「缺口核验 agent」。下面是首轮汇总时列出的「尚缺证据/待核查问题」清单，以及补搜后合并的论文语料（含每篇的方法/贡献/局限）。请逐条判断：**补搜后的语料是否已经提供了回答该问题的证据**。

判定标准：
- resolved：语料中已有具体论文明确回答了这个问题。
- partial：有相关论文但只部分回答，仍有明显缺口。
- open：语料仍无法回答这个问题。

首轮遗留的缺口问题（逐条判定）：
{questions}

补搜后合并语料：
{corpus}

只输出一个 JSON（```json 围栏）。schema：
```json
{{
  "judgements": [
    {{"question": "原问题", "verdict": "resolved|partial|open", "evidence": "点名相关论文或说明为何仍 open"}}
  ]
}}
```
"""


def _render_corpus(papers: list[dict]) -> str:
    lines = []
    for i, p in enumerate(papers, 1):
        lines.append(
            f"[{i}] {p.get('title')} ({p.get('year')})\n"
            f"    方法: {p.get('method','')}\n"
            f"    贡献: {p.get('contribution','')}\n"
            f"    局限: {p.get('limitation','')}"
        )
    return "\n".join(lines)


def _judge_gap_closure(questions: list[str], papers: list[dict]) -> tuple[dict, Cost]:
    """用一次判定 agent 语义核验首轮缺口在补搜后是否被回答。"""
    if not questions:
        return {"judgements": []}, Cost()
    prompt = _JUDGE_PROMPT.format(
        questions="\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)),
        corpus=_render_corpus(papers),
    )
    h = make_harness()
    with h:
        r = run_agent(h, prompt, session_id="gap-closure-judge")
    obj = extract_json(r.text) or {"judgements": []}
    return obj, r.cost


def main() -> None:
    plan = json.loads((REPORTS / "plan.json").read_text())
    area = plan["area"]
    seed_titles = [s["title"] for s in plan["seeds"]]
    subtopics = plan["subtopics"]

    result: dict = {"area": area, "subtopics": [s["name"] for s in subtopics]}

    # ---------- 首轮：4 个独立 Searcher（新鲜跑，全字段插桩） ----------
    print("== 首轮：并行 Searcher ==", flush=True)
    first_cost = Cost()
    first_corpora: list[dict] = [None] * len(subtopics)
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=min(MAX_PARALLEL, len(subtopics))) as pool:
        fut = {}
        for i, sub in enumerate(subtopics):
            print(f"  [{i}] {sub['name']}", flush=True)
            fut[pool.submit(_search_isolated, area, sub, seed_titles, f"abl-searcher-{i}")] = (i, sub)
        for f in as_completed(fut):
            i, sub = fut[f]
            corpus, res = f.result()
            first_cost.merge(res.cost)
            if corpus:
                # 补齐协作字段，供证据板统计缺口
                corpus.setdefault("subtopic", sub["name"])
                corpus["agent"] = f"searcher-{i}"
                corpus.setdefault("scope", sub.get("scope", ""))
                corpus.setdefault("open_questions", [])
                corpus.setdefault("covered_claims", [])
                first_corpora[i] = corpus
            else:
                print(f"  [{i}] ⚠️ 解析失败", flush=True)
    first_time = time.perf_counter() - t0
    first_round = [c for c in first_corpora if c]

    board_first = build_evidence_board(first_round)
    first_papers = merge_corpora(first_round)
    N = _count_open_questions(board_first)
    open_qs = _collect_open_questions(board_first)
    print(f"  首轮：{len(first_papers)} 篇(去重后) | 缺口 N={N} | {first_time:.0f}s", flush=True)

    result["only_first_round"] = {
        "papers_dedup": len(first_papers),
        "papers_raw": sum(len(c.get("papers", [])) for c in first_round),
        "open_questions_N": N,
        "input_tokens": first_cost.input_tokens,
        "output_tokens": first_cost.output_tokens,
        "aminer_calls": first_cost.aminer_calls,
        "aminer_tools": first_cost.aminer_tools,
        "wall_seconds": round(first_time, 1),
        "detail_reads": sum(1 for c in first_round for p in c.get("papers", []) if p.get("read_detail")),
        "candidates": sum(len(c.get("papers", [])) for c in first_round),
    }

    # ---------- Coordinator：读证据板派发定向补搜 ----------
    print("== Coordinator ==", flush=True)
    coord_cost = Cost()
    t0 = time.perf_counter()
    ch = make_harness()
    with ch:
        followups, cres = coordinate(ch, area, board_first)
    coord_cost.merge(cres.cost)
    coord_time = time.perf_counter() - t0
    print(f"  派发 {len(followups)} 个补搜 | {coord_time:.0f}s", flush=True)

    # ---------- 定向补搜：并行执行 ----------
    print("== 定向补搜 Searcher ==", flush=True)
    fu_cost = Cost()
    fu_corpora: list[dict] = []
    known_by_id = {p.get("id"): p for p in first_papers if p.get("id")}
    dup_detail_reads = 0  # 补搜里重复精读已读论文的次数（预算复用机制要压到 0）
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(len(followups), 1)) as pool:
        fut = {}
        for i, task in enumerate(followups):
            known = [known_by_id[pid] for pid in task.get("known_paper_ids", []) if pid in known_by_id]
            print(f"  [补搜 {i}] {task['name']}", flush=True)
            fut[pool.submit(_search_isolated, area, task, seed_titles, f"abl-followup-{i}", known, FOLLOWUP_DETAIL_READS)] = (i, task)
        for f in as_completed(fut):
            i, task = fut[f]
            corpus, res = f.result()
            fu_cost.merge(res.cost)
            if corpus:
                corpus.setdefault("subtopic", task["name"])
                corpus["agent"] = f"followup-{i}"
                corpus.setdefault("open_questions", [])
                # 统计是否有补搜论文其实已在首轮读过（重复精读）
                for p in corpus.get("papers", []):
                    if p.get("read_detail") and p.get("id") in known_by_id and known_by_id[p["id"]].get("read_detail"):
                        dup_detail_reads += 1
                fu_corpora.append(corpus)
            else:
                print(f"  [补搜 {i}] ⚠️ 解析失败", flush=True)
    fu_time = time.perf_counter() - t0

    # ---------- 两轮汇总 ----------
    all_corpora = first_round + fu_corpora
    all_papers = merge_corpora(all_corpora)
    board_all = build_evidence_board(all_corpora)

    # 剩余缺口 M：用判定 agent 核验首轮 N 条缺口在补搜语料里是否被回答
    print("== 缺口闭合判定 ==", flush=True)
    judge_obj, judge_cost = _judge_gap_closure(open_qs, all_papers)
    verdicts = {}
    for j in judge_obj.get("judgements", []):
        v = j.get("verdict", "?")
        verdicts[v] = verdicts.get(v, 0) + 1
    M = verdicts.get("open", 0) + verdicts.get("partial", 0)  # 仍未完全闭合的算残留缺口
    M_strict = verdicts.get("open", 0)                        # 完全没回答的

    two_round_cost = Cost()
    two_round_cost.merge(first_cost)
    two_round_cost.merge(coord_cost)
    two_round_cost.merge(fu_cost)
    two_round_cost.merge(judge_cost)
    two_round_time = first_time + coord_time + fu_time

    result["two_round_adaptive"] = {
        "papers_dedup": len(all_papers),
        "papers_raw": sum(len(c.get("papers", [])) for c in all_corpora),
        "open_questions_N_before": N,
        "gap_verdicts": verdicts,
        "open_questions_M_after_partial_or_open": M,
        "open_questions_M_after_strict_open": M_strict,
        "input_tokens": two_round_cost.input_tokens,
        "output_tokens": two_round_cost.output_tokens,
        "aminer_calls": two_round_cost.aminer_calls,
        "aminer_tools": two_round_cost.aminer_tools,
        "wall_seconds": round(two_round_time, 1),
        "followups": [{"name": t["name"], "queries": t.get("queries", [])} for t in followups],
        "followup_papers_added": len(all_papers) - len(first_papers),
        "duplicate_detail_reads_in_followup": dup_detail_reads,
    }

    # ---------- 第二个消融：去重 / 缓存 / 预算控制 的价值（多数为确定性后处理） ----------
    raw_all = sum(len(c.get("papers", [])) for c in all_corpora)
    result["controlled_execution_value"] = {
        "dedup_off": {
            "papers_without_dedup": raw_all,
            "papers_with_dedup": len(all_papers),
            "duplicates_collapsed": raw_all - len(all_papers),
            "inflation_pct": round((raw_all - len(all_papers)) / max(len(all_papers), 1) * 100, 1),
            "note": "关掉按 id 去重后，下游综述/排序会重复处理这些条目",
        },
        "cache_off": {
            "first_round_input_tokens": first_cost.input_tokens,
            "first_round_output_tokens": first_cost.output_tokens,
            "first_round_aminer_calls": first_cost.aminer_calls,
            "note": "关掉缓存后，每次重跑都要重复付出这份首轮成本；开缓存时命中子方向近似 0 增量",
        },
        "budget_cap_off_estimate": {
            "detail_reads_capped": result["only_first_round"]["detail_reads"],
            "candidates_retained": result["only_first_round"]["candidates"],
            "note": "去掉 get_paper_detail 上限后，精读会向候选数收敛；见 uncapped_probe 的实测增量",
        },
    }

    with OUT.open("w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    # 判定明细单独存，便于回看
    (OUT.parent / "exp1_gap_judgements.json").write_text(json.dumps(judge_obj, ensure_ascii=False, indent=2))

    print("\n===== 实验一结果 =====", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
