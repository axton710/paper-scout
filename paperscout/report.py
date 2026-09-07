"""报告面向阅读与验证决策，保留证据层级和运行完成度。"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote

from .gap import align_verdicts
from .harness import Cost


def _cell(value) -> str:
    return str(value).replace('|', '/').replace('\n', ' ')


def _paper_link(paper: dict) -> str:
    title = _cell(paper.get('title', '')).replace('[', '\\[').replace(']', '\\]')
    url = ('https://doi.org/' + quote(paper['doi'], safe='/')) if paper.get('doi') else ('https://www.aminer.cn/pub/' + quote(paper.get('id', ''), safe=''))
    return f'[{title}]({url})'


def priority_reading(ranked: list[dict], limit: int = 5) -> list[dict]:
    chosen, covered = [], set()
    # 先覆盖不同技术路线，再补总排名靠前的论文，避免五篇全挤在同一路线。
    for paper in ranked:
        topics = set(paper.get('subtopics', []))
        if topics - covered:
            chosen.append(paper)
            covered.update(topics)
        if len(chosen) == limit:
            return chosen
    for paper in ranked:
        if paper not in chosen:
            chosen.append(paper)
        if len(chosen) == limit:
            break
    return chosen


def _reading_list(ranked: list[dict]) -> str:
    lines = ['| # | 论文 | 年份 | 被引档位 | 证据 | 阅读目的 |', '|---|---|---|---|---|---|']
    for i, p in enumerate(ranked, 1):
        why = _cell(p.get('relevance') or p.get('contribution', ''))
        source = '已读摘要' if p.get('read_detail') else '仅搜索结果，待读摘要'
        lines.append(f"| {i} | {_paper_link(p)} | {p.get('year', '')} | {_cell(p.get('citation_bucket', ''))} | {source} | {why} |")
    return '\n'.join(lines)


def _gaps(verified: list[dict], raw_gaps: dict) -> str:
    checked = align_verdicts(raw_gaps, {'verified': verified})
    out = []
    for g, v in zip(raw_gaps.get('gaps', []), checked):
        if v['verdict'] == 'drop':
            continue
        verdict = {'keep': '支持继续验证', 'weak': '证据较弱，需收窄', 'unverified': '未核验'}[v['verdict']]
        headline = v.get('refined') or g.get('gap', '')
        out.append(
            f"### {len(out) + 1}. {headline}\n"
            f"- **已有证据**：{g.get('evidence', '')}\n"
            f"- **候选缺口**：{g.get('missing', '')}\n"
            f"- **核验状态**：{verdict} — {v['reason']}\n"
            f"- **仍需查证**：{g.get('to_verify') or '需要补充全文和反证检索'}\n"
            f"- **最小验证实验**：{g.get('minimal_experiment') or '尚未提出，不应直接作为已确认选题'}\n"
            f"- **相关论文**：{', '.join(g.get('related_titles', []))}"
        )
    return '\n\n'.join(out) if out else '_暂无保留的候选方向。_'


def _survey_headings(survey: str) -> str:
    levels = [len(m.group(1)) for m in re.finditer(r'^(#{1,6})\s', survey, re.MULTILINE)]
    shift = max(0, 3 - min(levels)) if levels else 0
    in_fence = False
    lines = []
    for line in survey.splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            in_fence = not in_fence
        if not in_fence:
            line = re.sub(r'^(#{1,6})(?=\s)', lambda m: '#' * min(6, len(m[1]) + shift), line)
        lines.append(line)
    return '\n'.join(lines)


def _reflection_note(rounds: list[dict]) -> str:
    return ' → '.join(f"R{r['round']}: {r['verdicts']}" for r in rounds)


def assemble(area: str, seeds: list[dict], ranked: list[dict], survey: str,
             raw_gaps: dict, verified: list[dict], cost: Cost, rounds: list[dict] | None = None,
             manifest: dict | None = None) -> str:
    manifest = manifest or {}
    coverage = manifest.get('search_coverage', {})
    failures = [f"{name}: {stage.get('error', stage['status'])}" for name, stage in manifest.get('stages', {}).items() if stage['status'] != 'complete']
    seed_lines = '\n'.join(f"- {s.get('title')} ({s.get('year', '年份待查')})" for s in seeds)
    return f"""# 论文调研报告

> 生成时间：{datetime.now():%Y-%m-%d %H:%M} | 运行：{manifest.get('run_id', '示例')} | 状态：{manifest.get('status', '未记录')}
>
> 证据范围为搜索结果与摘要，未读取全文。研究方向均为待验证候选；“支持继续验证”不代表已证明新颖性。

## 先读这 {min(5, len(ranked))} 篇
按下列顺序建立各路线的认识，再根据研究重点进入完整清单。

{_reading_list(priority_reading(ranked))}

## 研究范围与完成度
{area}

{seed_lines}

- 首轮方向：{coverage.get('completed', '未记录')} / {coverage.get('planned', '未记录')}
- 补搜任务：{coverage.get('followups_completed', '未记录')} / {coverage.get('followups_planned', '未记录')}
- 失败或未完成阶段：{'; '.join(failures) or '无记录'}

## 领域综述
{_survey_headings(survey)}

## 候选研究方向与验证计划
{_reflection_note(rounds or [])}

{_gaps(verified, raw_gaps)}

## 完整论文清单
{_reading_list(ranked)}

## 证据与成本
- [计划](plan.json) · [合并语料与逐篇来源](corpus.json) · [最终证据板](final_evidence_board.json) · [阶段状态](manifest.json) · [核验轮次](gaps.json)
- 原始调用保存在 `agents/<session_id>.json`；语料中的 `provenance` 指向具体 session、tool_call_id 和返回记录。
- AMiner 已准入调用：{cost.aminer_calls} 次（{cost.aminer_tools}）；不含被预算拦截的请求，服务端实际计费以账单为准。
- 模型 token：未缓存输入 {cost.input_tokens} / 缓存读取 {cost.cache_read_tokens} / 输出 {cost.output_tokens}
- 成本范围：{manifest.get('cost_scope', '本次已记录调用')}；成本未知的失败尝试：{manifest.get('unknown_cost_attempts', 0)}。
"""
