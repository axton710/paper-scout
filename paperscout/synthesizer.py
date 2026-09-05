"""M4 · Synthesizer agent：把语料综合成领域综述（输出 Markdown 正文）。"""

from __future__ import annotations

from .harness import AgentResult, run_agent

_PROMPT = """你是文献综述的“综述 agent”。下面给你一个研究领域的一批论文（已含每篇的问题/方法/贡献/局限）。请写一份**结构化的中文领域综述**，要求：
- 按**方法流派 / 技术路线**分类组织（而不是逐篇罗列）。
- 指出该领域的**共识**与**分歧/争议**。
- 给出**技术演变时间线**（如 GAN→扩散、full-shot→few-shot→免训练 的脉络）。
- 引用论文时用其英文标题，便于溯源。
- 只写综述正文（Markdown，使用二级/三级标题分节），不要输出 JSON，不要重复整段粘贴论文清单。

研究领域：
{area}

论文语料：
{corpus}
"""


def synthesize(harness, area: str, corpus_text: str, session_id: str = "synthesizer") -> AgentResult:
    return run_agent(harness, _PROMPT.format(area=area, corpus=corpus_text), session_id=session_id)
