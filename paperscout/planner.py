"""M1 · Planner agent：种子标题 → 定位 AMiner → 读摘要 → 出 K 个子方向的 plan。"""

from __future__ import annotations

from .harness import AgentResult, extract_json, run_agent

# 子方向数量区间：仅作 fan-out/成本护栏，具体拆几个由 Planner 按领域宽窄自行决定
MIN_SUBTOPICS = 2
MAX_SUBTOPICS = 6

_PROMPT = """你是一个文献调研的“规划 agent”。你能调用 AMiner 学术工具（工具名以 mcp__aminer__ 开头），其中你需要用到：
- mcp__aminer__search_paper_by_title：按标题搜论文，拿到论文的 AMiner id、DOI、年份。
- mcp__aminer__get_paper_detail：按 id 取论文详情，含英文摘要 abstract、中文摘要、关键词 keywords、被引数。

你的任务（严格按顺序）：
1. 对下面每一篇种子论文标题，调用 search_paper_by_title 找到最匹配的那一篇，取它的 AMiner id 和年份。
2. 对每篇种子，用 get_paper_detail 读它的摘要和关键词，用一两句话概括它做了什么。
3. 综合这两篇种子，判断它们所在的**研究领域/子领域**是什么。
4. **根据这个领域实际的宽窄，自行决定要拆成几个子方向（{min_subtopics}–{max_subtopics} 个）**：领域窄就少拆、够宽才多拆；宁可少而准，不要为凑数硬拆，也不要把明显不同的技术流派揉进同一个子方向。每个子方向给 2–4 个**英文检索词组**，供后续用 search_paper 做广度检索，并用一句话说明为何要单列这个方向。
5. 只做上面的事，**不要写综述正文**。

完成后，在最后输出一个 JSON（用 ```json 围栏包裹）。**注意：JSON 字符串值内部不要出现英文双引号，需要强调或引用时一律用中文引号「」。** schema 如下：
```json
{{
  "area": "研究领域的中文描述",
  "seeds": [
    {{"title": "原标题", "aminer_id": "...", "year": 2024, "summary": "一句话概括"}}
  ],
  "subtopics": [
    {{"name": "子方向名", "rationale": "为什么要覆盖它", "queries": ["english query 1", "english query 2"]}}
  ]
}}
```

种子论文标题：
{seeds}
"""


def build_prompt(seed_titles: list[str], min_subtopics: int = MIN_SUBTOPICS,
                 max_subtopics: int = MAX_SUBTOPICS) -> str:
    seeds = "\n".join(f"{i}. {t}" for i, t in enumerate(seed_titles, 1))
    return _PROMPT.format(min_subtopics=min_subtopics, max_subtopics=max_subtopics, seeds=seeds)


def plan(harness, seed_titles: list[str], session_id: str = "planner",
         min_subtopics: int = MIN_SUBTOPICS, max_subtopics: int = MAX_SUBTOPICS) -> tuple[dict | None, AgentResult]:
    prompt = build_prompt(seed_titles, min_subtopics, max_subtopics)
    result = run_agent(harness, prompt, session_id=session_id)
    return extract_json(result.text), result
