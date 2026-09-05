"""端到端编排：plan → 各子方向 Searcher → Triage → 综述 → Gap+Verifier → 报告。

已存在 reports/corpus_i.json 的子方向会复用缓存（省钱）；成本只累计本次真正发生的调用。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from paperscout.corpus import merge_corpora, rank, render_compact
from paperscout.gap import reflect_gaps
from paperscout.harness import Cost, make_harness
from paperscout.report import assemble
from paperscout.searcher import search
from paperscout.synthesizer import synthesize

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"


def main() -> None:
    plan = json.loads((REPORTS / "plan.json").read_text())
    area = plan["area"]
    seeds = plan["seeds"]
    seed_titles = [s["title"] for s in seeds]
    total = Cost()

    # 综述这步推理会吃掉不少输出预算，16000 会在思考阶段就被截断导致正文为空，给到 32000
    harness = make_harness(max_tokens=32000)
    with harness:
        # M3: 各子方向检索（缓存复用）
        corpora = []
        for i, sub in enumerate(plan["subtopics"]):
            cache = REPORTS / f"corpus_{i}.json"
            if cache.exists():
                print(f"[{i}] 复用缓存 {cache.name}", flush=True)
                corpora.append(json.loads(cache.read_text()))
                continue
            print(f"[{i}] 检索：{sub['name']}", flush=True)
            corpus, res = search(harness, area, sub, seed_titles, session_id=f"searcher-{i}")
            total.merge(res.cost)
            if corpus:
                cache.write_text(json.dumps(corpus, ensure_ascii=False, indent=2))
                corpora.append(corpus)
            else:
                print(f"[{i}] ⚠️ 解析失败，跳过")

        papers = merge_corpora(corpora)
        ranked = rank(papers)
        corpus_text = render_compact(ranked)
        print(f"合并语料 {len(papers)} 篇，开始综述 ...", flush=True)

        # M4: 综述
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
