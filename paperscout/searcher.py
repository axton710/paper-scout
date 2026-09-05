"""M2 · Searcher agent：一个子方向 → AMiner 广度搜 + 精读 → 结构化语料。"""

from __future__ import annotations

import json

from .harness import AgentResult, extract_json, run_agent

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
建议检索词（可自行调整）：
{queries}

已知的两篇种子论文（不要把它们当作本方向的新发现重复报告，但可以作为相关性判断的锚点）：
{seeds}

工作步骤（严格控制调用次数，避免浪费）：
1. 对每个检索词调用 **一次** search_paper（size 可设 8~10）。**不要重复搜同一个词。**
2. 汇总候选，按 id 去重，剔除明显跑题的；综合“与本子方向相关性 + 被引档位 + 年份”挑出最重要的 **不超过 {max_candidates} 篇**。
3. 只对其中最关键的 **不超过 {max_detail_reads} 篇** 调用 get_paper_detail 读摘要（这一步计费，务必克制）；其余论文用搜索返回的标题/年份/被引信息即可。
4. 为每篇候选写一条结构化笔记。精读过的用摘要填 method/contribution/limitation；没精读的这些字段可留简短或空字符串。

完成后，只输出一个 JSON（用 ```json 围栏）。**注意：JSON 字符串值内部不要出现英文双引号，需要强调或引用时一律用中文引号「」。** schema：
```json
{{
  "subtopic": "{sub_name}",
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


def build_prompt(area: str, subtopic: dict, seed_titles: list[str]) -> str:
    queries = "\n".join(f"- {q}" for q in subtopic.get("queries", []))
    seeds = "\n".join(f"{i}. {t}" for i, t in enumerate(seed_titles, 1))
    return _PROMPT.format(
        area=area,
        sub_name=subtopic.get("name", ""),
        sub_rationale=subtopic.get("rationale", ""),
        queries=queries,
        seeds=seeds,
        max_candidates=MAX_CANDIDATES,
        max_detail_reads=MAX_DETAIL_READS,
    )


def normalize_corpus(obj, subtopic_name: str) -> dict | None:
    """把解析结果归一成 {subtopic, papers}。

    兜底修复偶尔把一个对象拆成多个片段（列表），这里按 id 去重合并。
    """
    if isinstance(obj, dict) and "papers" in obj:
        return obj
    if isinstance(obj, list):
        papers, seen = [], set()
        for item in obj:
            if not isinstance(item, dict):
                continue
            for p in item.get("papers", []):
                pid = p.get("id")
                if pid and pid not in seen:
                    seen.add(pid)
                    papers.append(p)
        if papers:
            return {"subtopic": subtopic_name, "papers": papers}
    return None


def search(harness, area: str, subtopic: dict, seed_titles: list[str], session_id: str) -> tuple[dict | None, AgentResult]:
    prompt = build_prompt(area, subtopic, seed_titles)
    result = run_agent(harness, prompt, session_id=session_id)
    corpus = normalize_corpus(extract_json(result.text), subtopic.get("name", ""))
    return corpus, result
