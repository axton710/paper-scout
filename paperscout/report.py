"""M6 · 报告组装：把必读清单/综述/创新点/成本拼成一份 Markdown。"""

from __future__ import annotations

import json
from datetime import datetime

from .harness import Cost


def _reading_list(ranked: list[dict]) -> str:
    lines = ["| # | 论文 | 年份 | 被引档位 | 为什么读 |", "|---|---|---|---|---|"]
    for i, p in enumerate(ranked, 1):
        title = str(p.get("title", "")).replace("|", "/")
        why = str(p.get("relevance", "") or p.get("contribution", "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| {i} | {title} | {p.get('year','')} | {p.get('citation_bucket','')} | {why} |")
    return "\n".join(lines)


def _gaps(verified: list[dict], raw_gaps: dict) -> str:
    by_gap = {v.get("gap", ""): v for v in verified}
    out = []
    n = 0
    for g in raw_gaps.get("gaps", []):
        v = by_gap.get(g.get("gap", ""), {})
        verdict = v.get("verdict", "keep")
        if verdict == "drop":
            continue
        n += 1
        headline = v.get("refined") or g.get("gap", "")
        out.append(
            f"### {n}. {headline}\n"
            f"- **现状证据**：{g.get('evidence','')}\n"
            f"- **缺口**：{g.get('missing','')}\n"
            f"- **核验**：{verdict} — {v.get('reason','')}\n"
            f"- **相关论文**：{', '.join(g.get('related_titles', []))}"
        )
    return "\n\n".join(out) if out else "_（暂无通过核验的创新点）_"


def _reflection_note(rounds: list[dict]) -> str:
    if not rounds:
        return ""
    segs = []
    for r in rounds:
        verdicts = "，".join(f"{k} {v}" for k, v in r["verdicts"].items())
        segs.append(f"第 {r['round']} 轮：{r['n']} 条（{verdicts}）")
    loop = "（经 {} 轮生成-核验-回改反思）".format(len(rounds)) if len(rounds) > 1 else "（单轮核验，无需回改）"
    return f"> **反思闭环** {loop}：" + " → ".join(segs) + "\n\n"


def assemble(area: str, seeds: list[dict], ranked: list[dict], survey: str,
             raw_gaps: dict, verified: list[dict], cost: Cost, rounds: list[dict] | None = None) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    seed_lines = "\n".join(f"- {s.get('title')} ({s.get('year')})" for s in seeds)
    aminer_detail = "、".join(f"{k}×{v}" for k, v in cost.aminer_tools.items())
    return f"""# 论文调研报告

> 生成时间：{ts}　|　工具：Paper Scout（多 Agent · dsh × AMiner）

## 研究领域
{area}

**种子论文**
{seed_lines}

## 一、必读论文清单
{_reading_list(ranked)}

## 二、领域综述
{survey}

## 三、创新点 / 研究空白
{_reflection_note(rounds or [])}{_gaps(verified, raw_gaps)}

---

## 附：成本小结
- AMiner 调用：**{cost.aminer_calls}** 次（{aminer_detail}）
- DeepSeek token：输入 {cost.input_tokens} / 输出 {cost.output_tokens}
"""
