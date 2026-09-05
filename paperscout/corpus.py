"""语料的合并、去重、排序（Triage，纯 Python，确定性）与紧凑渲染。"""

from __future__ import annotations

# 被引档位 → 权重（AMiner 的 n_citation_bucket 文案）
_BUCKET = {
    "0": 0, "1-10": 1, "11-50": 2, "51-200": 3,
    "201-1000": 4, "1000-5000": 5, "5000+": 6,
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
                by_id[pid] = p
            else:
                if sub and sub not in by_id[pid]["subtopics"]:
                    by_id[pid]["subtopics"].append(sub)
    return list(by_id.values())


def rank(papers: list[dict], now_year: int = 2026) -> list[dict]:
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
            f"[{i}] {p.get('title')} ({p.get('year')}, 被引{p.get('citation_bucket')})\n"
            f"    问题: {p.get('problem','')}\n"
            f"    方法: {p.get('method','')}\n"
            f"    贡献: {p.get('contribution','')}\n"
            f"    局限: {p.get('limitation','')}"
        )
    return "\n".join(lines)
