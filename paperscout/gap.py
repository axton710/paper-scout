"""M5 · Gap（找创新点）+ Verifier（核验）+ Reflection 闭环。

Reflection = 生成 → 批评 → 把批评喂回生成方重出 → 再批评，直到无 weak/drop 或达轮数上限。
只过滤不回改就只是 critique；这里补上"回改"那条边才是真正的 reflection。
"""

from __future__ import annotations

import json

from .harness import Cost, extract_json, run_agent

MAX_ROUNDS = 2  # 首轮 + 至多一次修正

_GAP_PROMPT = """你是帮研究生找创新点的“gap 分析 agent”。下面是某领域的综述与论文语料。请找出**尚未被充分研究、或做得较浅**的方向作为候选创新点。要求：
- 每条 gap 必须 **grounded**：说清“现状证据”（哪些论文做了什么）与“缺口”（还缺什么 / 哪种组合没人做）。
- 优先找**交叉点、被忽视的设定、方法学空白**，而不是泛泛而谈。
- 给 3–6 条，宁精勿滥。

综述：
{survey}

论文语料：
{corpus}

只输出一个 JSON（```json 围栏）。**字符串值内部不要用英文双引号，需引用时用中文引号「」。** schema：
```json
{{
  "gaps": [
    {{
      "gap": "一句话说清这个创新方向",
      "evidence": "现状证据：哪些论文做了什么",
      "missing": "缺口：还缺什么/哪种组合没人做",
      "related_titles": ["相关论文英文标题"]
    }}
  ]
}}
```
"""

_REVISE_PROMPT = """你是“gap 分析 agent”，正在做第二轮**修正**。上一轮你提的创新点已被核验 agent 逐条评审。请据评审改进这批创新点：
- 判 **keep** 的：保留（措辞可微调）。
- 判 **weak** 的：**收窄或换角度**，改到与已有工作无重叠、更具体可做。
- 判 **drop** 的：说明它已被现有工作覆盖，**换一个真正未被覆盖的新方向替换**。
- 最终仍给 3–6 条，每条依旧要 grounded（现状证据 + 缺口）。

综述：
{survey}

论文语料：
{corpus}

上一轮创新点 + 核验结果（据此改进）：
{feedback}

只输出一个 JSON（```json 围栏）。**字符串值内部不要用英文双引号，需引用时用中文引号「」。** schema 同上一轮：
```json
{{
  "gaps": [
    {{"gap": "...", "evidence": "...", "missing": "...", "related_titles": ["..."]}}
  ]
}}
```
"""

_VERIFY_PROMPT = """你是“核验 agent”，以**严格、挑剔**的标准把关，目的是挤掉“假新颖”。下面是一批候选创新点(gaps)和论文语料。逐条判定：
- verdict：keep | weak | drop。
- **keep 门槛要高**：只有语料中确实**没有方向相近的工作**、且缺口具体可做，才判 keep。
- 只要语料里存在**方向相近、部分重叠、或该 gap 偏宽泛**，就判 **weak** 并指出与哪篇重叠、该如何收窄。
- 已被现有工作基本覆盖，判 **drop** 并点名论文。
- 抱着“能挑刺就挑刺”的心态——初版 gap 里通常会有若干 weak，请如实标出，不要为了好看一律 keep。
- reason：简要理由（点名论文）；对 keep/weak 给一句更聚焦的改写 refined。

候选 gaps：
{gaps}

论文语料：
{corpus}

只输出一个 JSON（```json 围栏）。**字符串值内部不要用英文双引号。** schema：
```json
{{
  "verified": [
    {{"gap": "原 gap", "verdict": "keep|weak|drop", "reason": "...", "refined": "更聚焦的改写（可选）"}}
  ]
}}
```
"""


def _find_gaps(harness, survey, corpus_text, session_id):
    r = run_agent(harness, _GAP_PROMPT.format(survey=survey, corpus=corpus_text), session_id=session_id)
    return extract_json(r.text), r


def _revise_gaps(harness, survey, corpus_text, feedback, session_id):
    r = run_agent(harness, _REVISE_PROMPT.format(survey=survey, corpus=corpus_text, feedback=feedback), session_id=session_id)
    return extract_json(r.text), r


def _verify_gaps(harness, gaps_json, corpus_text, session_id):
    r = run_agent(harness, _VERIFY_PROMPT.format(gaps=gaps_json, corpus=corpus_text), session_id=session_id)
    return extract_json(r.text), r


def _counts(verified: list[dict]) -> dict[str, int]:
    c: dict[str, int] = {}
    for v in verified:
        k = v.get("verdict", "?")
        c[k] = c.get(k, 0) + 1
    return c


def _needs_revision(verified: list[dict]) -> bool:
    return any(v.get("verdict") in ("weak", "drop") for v in verified)


def _format_feedback(raw_gaps: dict, verified: list[dict]) -> str:
    by_gap = {v.get("gap", ""): v for v in verified}
    lines = []
    for i, g in enumerate(raw_gaps.get("gaps", []), 1):
        v = by_gap.get(g.get("gap", ""), {})
        lines.append(
            f"{i}. [{v.get('verdict', '?')}] {g.get('gap', '')}\n"
            f"   理由: {v.get('reason', '')}"
            + (f"\n   收窄建议: {v['refined']}" if v.get("refined") else "")
        )
    return "\n".join(lines)


def reflect_gaps(harness, survey: str, corpus_text: str, max_rounds: int = MAX_ROUNDS):
    """生成→核验→回改的反思闭环。返回 (最终 raw_gaps, 最终 verified, 成本, 每轮记录)。"""
    total = Cost()

    raw, r = _find_gaps(harness, survey, corpus_text, "gap-r1")
    total.merge(r.cost)
    raw = raw or {"gaps": []}
    vobj, vr = _verify_gaps(harness, json.dumps(raw, ensure_ascii=False), corpus_text, "verify-r1")
    total.merge(vr.cost)
    verified = (vobj or {}).get("verified", [])
    rounds = [{"round": 1, "n": len(raw.get("gaps", [])), "verdicts": _counts(verified)}]

    n = 1
    while n < max_rounds and _needs_revision(verified):
        n += 1
        feedback = _format_feedback(raw, verified)
        raw2, r2 = _revise_gaps(harness, survey, corpus_text, feedback, f"gap-r{n}")
        total.merge(r2.cost)
        if raw2 and raw2.get("gaps"):
            raw = raw2
        vobj, vr2 = _verify_gaps(harness, json.dumps(raw, ensure_ascii=False), corpus_text, f"verify-r{n}")
        total.merge(vr2.cost)
        verified = (vobj or {}).get("verified", [])
        rounds.append({"round": n, "n": len(raw.get("gaps", [])), "verdicts": _counts(verified)})

    return raw, verified, total, rounds
