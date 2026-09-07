"""语料的合并、去重、排序（Triage，纯 Python，确定性）与紧凑渲染。"""

from __future__ import annotations

# 被引档位 → 权重（AMiner 的 n_citation_bucket 文案）
_BUCKET = {
    "0": 0, "1-10": 1, "11-50": 2, "51-200": 3,
    "201-1000": 4, "1000-5000": 5, "1001-5000": 5, "5000+": 6,
}


def merge_corpora(corpora: list[dict]) -> list[dict]:
    """跨子方向合并，按 id 去重；保留每篇出现过的子方向列表。"""
    by_id: dict[str, dict] = {}
    for c in corpora:
        sub = c.get("subtopic", "")
        for p in c.get("papers", []):
            pid = p.get("id")
            if not pid:
                continue
            if pid not in by_id:
                p = dict(p)
                p["subtopics"] = [sub] if sub else []
                p["observations"] = [{"subtopic": sub, **{k: p.get(k, "") for k in ("method", "contribution", "limitation", "read_detail")}}]
                p["provenance"] = list(p.get("provenance", []))
                by_id[pid] = p
            else:
                existing = by_id[pid]
                existing["observations"].append({"subtopic": sub, **{k: p.get(k, "") for k in ("method", "contribution", "limitation", "read_detail")}})
                for source in p.get("provenance", []):
                    if source not in existing["provenance"]:
                        existing["provenance"].append(source)
                # 后来的摘要证据可以升级首轮标题笔记，但不能覆盖掉来源与独立观察。
                upgrade = p.get("read_detail") and not existing.get("read_detail")
                for key, value in p.items():
                    if key not in ("subtopics", "observations", "provenance") and (upgrade or not existing.get(key)):
                        existing[key] = value
                if sub and sub not in by_id[pid]["subtopics"]:
                    by_id[pid]["subtopics"].append(sub)
    return list(by_id.values())


def build_evidence_board(corpora: list[dict]) -> dict:
    """合并首轮发现，供 Coordinator 判断覆盖缺口而非读取 agent 对话。"""
    papers: dict[str, dict] = {}
    coverage = []
    for corpus in corpora:
        subtopic = corpus.get("subtopic", "")
        agent = corpus.get("agent", subtopic)
        coverage.append({
            "agent": agent,
            "subtopic": subtopic,
            "scope": corpus.get("scope", ""),
            "exclude": corpus.get("exclude", ""),
            "covered_claims": corpus.get("covered_claims", []),
            "open_questions": corpus.get("open_questions", []),
        })
        for paper in corpus.get("papers", []):
            paper_id = paper.get("id")
            if not paper_id:
                continue
            entry = papers.setdefault(paper_id, {
                "id": paper_id,
                "title": paper.get("title", ""),
                "year": paper.get("year"),
                "found_by": [],
                "evidence": [],
            })
            if agent not in entry["found_by"]:
                entry["found_by"].append(agent)
            entry["evidence"].append({
                "subtopic": subtopic,
                "source": "abstract" if paper.get("read_detail") else "search_result",
                "method": paper.get("method", ""),
                "contribution": paper.get("contribution", ""),
                "limitation": paper.get("limitation", ""),
            })
    return {"coverage": coverage, "papers": list(papers.values())}


def rank(papers: list[dict]) -> list[dict]:
    """按 影响力(被引档位) + 新近度 + 是否精读 排序，产出必读顺序。"""
    def score(p: dict) -> float:
        bucket = _BUCKET.get(str(p.get("citation_bucket", "")).strip(), 1)
        year = p.get("year") or 0
        recency = max(0, (year - 2018)) if year else 0
        detail = 1 if p.get("read_detail") else 0
        # 影响力为主，新近度与精读为辅
        return bucket * 2.0 + recency * 0.6 + detail
    return sorted(papers, key=score, reverse=True)


def render_compact(papers: list[dict]) -> str:
    """把语料压成紧凑文本喂给下游 agent（控 token）。"""
    lines = []
    for i, p in enumerate(papers, 1):
        lines.append(
            f"[{p.get('id')}] {p.get('title')} ({p.get('year')}, 被引{p.get('citation_bucket')})\n"
            f"    证据层级: {'摘要' if p.get('read_detail') else '搜索结果，未读摘要'}；局限为推断，不能证明全文未涉及\n"
            f"    问题: {p.get('problem','')}\n"
            f"    方法: {p.get('method','')}\n"
            f"    贡献: {p.get('contribution','')}\n"
            f"    局限（待验证）: {p.get('limitation','')}\n"
            f"    独立观察: {p.get('observations', [])}"
        )
    return "\n".join(lines)
