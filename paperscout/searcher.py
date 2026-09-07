"""M2 · Searcher agent：一个子方向 → AMiner 广度搜 + 精读 → 结构化语料。"""

from __future__ import annotations

import json

from .harness import AgentResult, extract_json, run_agent
from .provenance import ground_corpus
from .validation import OutputError, object_value, list_value, strings, subtopic, text_value

MAX_CANDIDATES = 8      # 每个子方向保留的候选上限
MAX_DETAIL_READS = 4    # 精读（get_paper_detail，计费）上限，控成本

_PROMPT = """你是一个文献调研的“检索 agent”，负责把一个子方向查透。你能调用 AMiner 工具（mcp__aminer__ 前缀），需要用到：
- mcp__aminer__search_paper：按英文关键词搜论文，返回候选的 id、标题、年份、被引档位 n_citation_bucket。
- mcp__aminer__get_paper_detail：按 id 取论文详情，含英文摘要 abstract、关键词、被引数。
- （可选）mcp__aminer__recommend_paper：按主题词推荐相关论文。

研究领域背景：
{area}

当前子方向：**{sub_name}**
（为什么重要：{sub_rationale}）
必须覆盖：{sub_scope}
不作为重点：{sub_exclude}
建议检索词（可自行调整）：
{queries}

已知的两篇种子论文（不要把它们当作本方向的新发现重复报告，但可以作为相关性判断的锚点）：
{seeds}

前序检索已发现且已精读的论文如下。它们可以作为判断相关性和设计检索词的依据；不要再对这些 id 调用 get_paper_detail，把精读额度留给新增证据：
{known_papers}

工作步骤（严格控制调用次数，避免浪费）：
1. 对每个检索词调用 **一次** search_paper（size 可设 8~10）。**不要重复搜同一个词。**
2. 汇总候选，按 id 去重，剔除明显跑题的；综合“与本子方向相关性 + 被引档位 + 年份”挑出最重要的 **不超过 {max_candidates} 篇**。
3. 只对其中最关键的 **不超过 {max_detail_reads} 篇** 调用 get_paper_detail 读摘要（这一步计费，务必克制）；其余论文用搜索返回的标题/年份/被引信息即可。
4. 为每篇候选写一条结构化笔记。精读过的用摘要填 method/contribution/limitation；没精读的这些字段可留简短或空字符串。
5. 总结本轮已经覆盖的事实性结论，以及仍缺证据、值得下一轮定向核查的问题。不要把「尚未搜索到」表述为「不存在」。

完成后，只输出一个 JSON（用 ```json 围栏）。**注意：JSON 字符串值内部不要出现英文双引号，需要强调或引用时一律用中文引号「」。** schema：
```json
{{
  "subtopic": "{sub_name}",
  "covered_claims": ["本轮论文支持的一条事实性结论"],
  "open_questions": ["语料仍无法回答、可供后续检索的问题"],
  "papers": [
    {{
      "id": "AMiner id",
      "title": "标题",
      "year": 2024,
      "citation_bucket": "如 51-200",
      "read_detail": true,
      "problem": "它针对的问题（中文一句）",
      "method": "核心方法（中文一句）",
      "contribution": "主要贡献（中文一句）",
      "limitation": "局限或未解决之处（中文一句，没读全文就据摘要推断，可留空）",
      "relevance": "与本子方向的关系（中文一句）"
    }}
  ]
}}
```
"""


def build_prompt(area: str, subtopic: dict, seed_titles: list[str], known_papers: list[dict],
                 max_detail_reads: int) -> str:
    queries = "\n".join(f"- {q}" for q in subtopic.get("queries", []))
    seeds = "\n".join(f"{i}. {t}" for i, t in enumerate(seed_titles, 1))
    known = "\n".join(
        f"- {p.get('id')}: {p.get('title')} | 方法: {p.get('method', '')} | 贡献: {p.get('contribution', '')} | 局限（推断）: {p.get('limitation', '')}"
        for p in known_papers if p.get("id")
    ) or "（无）"
    return _PROMPT.format(
        area=area,
        sub_name=subtopic.get("name", ""),
        sub_rationale=subtopic.get("rationale", ""),
        sub_scope=subtopic.get("scope", "未指定"),
        sub_exclude=subtopic.get("exclude", "未指定"),
        queries=queries,
        seeds=seeds,
        known_papers=known,
        max_candidates=MAX_CANDIDATES,
        max_detail_reads=max_detail_reads,
    )


def normalize_corpus(obj, subtopic_name: str) -> dict:
    obj = object_value(obj, "corpus")
    papers = list_value(obj.get("papers"), "corpus.papers")
    normalized, seen = [], set()
    for i, paper in enumerate(papers):
        paper = object_value(paper, f"papers[{i}]").copy()
        for key in ("id", "title"):
            paper[key] = text_value(paper.get(key), f"papers[{i}].{key}")
        year = paper.get("year")
        if year is not None and (type(year) is not int or not 1800 <= year <= 2100):
            raise OutputError(f"papers[{i}].year: 需要有效整数年份或 null")
        if type(paper.get("read_detail", False)) is not bool:
            raise OutputError(f"papers[{i}].read_detail: 需要布尔值")
        for key in ("problem", "method", "contribution", "limitation", "relevance"):
            if not isinstance(paper.get(key, ""), str):
                raise OutputError(f"papers[{i}].{key}: 需要字符串")
        if paper["id"] not in seen:
            normalized.append(paper)
            seen.add(paper["id"])
    return {**obj, "subtopic": subtopic_name, "papers": normalized[:MAX_CANDIDATES],
            "covered_claims": strings(obj.get("covered_claims", []), "covered_claims"),
            "open_questions": strings(obj.get("open_questions", []), "open_questions")}


def search(harness, area: str, subtopic: dict, seed_titles: list[str], session_id: str,
           known_papers: list[dict] | None = None,
           max_detail_reads: int = MAX_DETAIL_READS) -> tuple[dict, AgentResult]:
    from .validation import subtopic as validate_subtopic
    subtopic = validate_subtopic(subtopic)
    known = [p for p in known_papers or [] if p.get("read_detail") and p.get("provenance")]
    prompt = build_prompt(area, subtopic, seed_titles, known, max_detail_reads)
    result = run_agent(harness, prompt, session_id=session_id,
                       tool_limits={"search_paper": len(subtopic["queries"]), "get_paper_detail": max_detail_reads},
                       known_ids=[p["id"] for p in known])
    corpus = normalize_corpus(extract_json(result.text), subtopic["name"])
    return ground_corpus(corpus, result.events, result.session_id, known), result
