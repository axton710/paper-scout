"""Coordinator：根据共享证据板，只为覆盖缺口派发有限的补搜任务。"""

from __future__ import annotations

import json

from .harness import AgentResult, extract_json, run_agent

MAX_FOLLOWUPS = 2

_PROMPT = """你是多 Agent 文献调研流程中的 Coordinator。首轮 Searcher 已独立完成检索，下面是压缩后的共享证据板；其中包含各方向的边界、覆盖结论、未解问题，以及论文摘要或搜索结果中的证据。你不能调用工具，也不写综述。

你的唯一任务是决定是否值得补搜：
- 找出跨方向的重复检索、没有证据覆盖的重要范围，或需要反证的开放问题。
- 只派发真正必要的补搜任务，最多 {max_followups} 个；若首轮覆盖充分，输出空列表。
- 每个任务必须给出 2–3 个英文查询词组，并说明需要补什么证据。
- `known_paper_ids` 只放与任务相关的已精读论文 id，供后续 Searcher 复用。不要把「未找到」误判成「不存在」。

研究领域：
{area}

共享证据板：
{board}

只输出一个 JSON（```json 围栏）。schema：
```json
{{
  "followups": [
    {{
      "name": "定向补搜任务名",
      "rationale": "首轮的具体覆盖缺口或重叠",
      "scope": "要验证的具体问题",
      "exclude": "不需要重复检索的范围",
      "queries": ["english query 1", "english query 2"],
      "known_paper_ids": ["AMiner id"]
    }}
  ]
}}
```
"""


def normalize_followups(obj) -> list[dict]:
    if not isinstance(obj, dict):
        return []
    followups = obj.get("followups", [])
    if not isinstance(followups, list):
        return []
    normalized = []
    for task in followups[:MAX_FOLLOWUPS]:
        if not isinstance(task, dict) or not task.get("name") or not task.get("queries"):
            continue
        normalized.append({
            "name": task["name"],
            "rationale": task.get("rationale", ""),
            "scope": task.get("scope", ""),
            "exclude": task.get("exclude", ""),
            "queries": task["queries"][:3],
            "known_paper_ids": task.get("known_paper_ids", []),
        })
    return normalized


def coordinate(harness, area: str, board: dict, session_id: str = "coordinator") -> tuple[list[dict], AgentResult]:
    prompt = _PROMPT.format(
        max_followups=MAX_FOLLOWUPS,
        area=area,
        board=json.dumps(board, ensure_ascii=False),
    )
    result = run_agent(harness, prompt, session_id=session_id)
    return normalize_followups(extract_json(result.text)), result
