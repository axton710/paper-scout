"""dsh 启动配置 + agent 调用/解析/记账的复用库。

每个角色一次 harness.run（全新隔离 session），角色+任务写进提示词；
AMiner 工具经 --patch 挂进源码 dsh runtime（A2，已实测）。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from deepseek_harness import DeepSeekHarness

PROJECT = Path(__file__).resolve().parent.parent          # ~/paper-scout
REPO = Path("/Users/axton/deepseek-harness")


def load_env() -> None:
    """把 .env 读进进程环境（loader 的 !!js 从 process.env 取 AMINER_TOKEN）。"""
    env_file = PROJECT / ".env"
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def make_harness(model: str = "deepseek-v4-flash", max_tokens: int = 12288) -> DeepSeekHarness:
    """按已验证的 A2 配置构造 harness（源码 dsh + AMiner 补丁）。"""
    load_env()
    assert os.environ.get("AMINER_TOKEN"), "缺 AMINER_TOKEN"
    assert os.environ.get("DEEPSEEK_API_KEY"), "缺 DEEPSEEK_API_KEY"
    (PROJECT / "workspace").mkdir(exist_ok=True)
    (PROJECT / "dsh-home").mkdir(exist_ok=True)
    return DeepSeekHarness(
        dsh_bin=str(PROJECT / "dsh-src.sh"),
        runtime_cwd=str(REPO),
        cwd=str(PROJECT / "workspace"),
        dsh_home=str(PROJECT / "dsh-home"),
        profile="sdk",
        patches=(str(PROJECT / "patches" / "aminer.patch.yml"),),
        provider="deepseek-official",
        model=model,
        max_tokens=max_tokens,
        initialize_timeout_seconds=180.0,
        request_timeout_seconds=300.0,
    )


@dataclass
class Cost:
    """一次运行的成本（AMiner 计费按调用次数，token 是 DeepSeek 侧）。"""

    aminer_calls: int = 0
    aminer_tools: dict[str, int] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0

    def merge(self, other: "Cost") -> None:
        self.aminer_calls += other.aminer_calls
        for k, v in other.aminer_tools.items():
            self.aminer_tools[k] = self.aminer_tools.get(k, 0) + v
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class AgentResult:
    text: str
    events: list
    cost: Cost
    finish_reason: str | None


_AMINER_TOOL_RE = re.compile(r"mcp__aminer__([a-zA-Z0-9_]+)")


def _cost_of(events: list) -> Cost:
    """从事件流抽成本：AMiner 工具调用次数 + token 用量。"""
    cost = Cost()
    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        blob = json.dumps(ev, ensure_ascii=False)
        if etype == "tool/call":
            m = _AMINER_TOOL_RE.search(blob)
            if m:
                cost.aminer_calls += 1
                cost.aminer_tools[m.group(1)] = cost.aminer_tools.get(m.group(1), 0) + 1
        # token 用量：兼容多种可能的字段名，先尽量抓，跑通后再按真实结构收紧
        usage = _find_usage(ev)
        if usage:
            cost.input_tokens += _num(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
            cost.output_tokens += _num(usage, "output_tokens", "outputTokens", "completion_tokens", "completionTokens")
    return cost


def _find_usage(ev: dict):
    for key in ("usage", "tokenUsage", "token_usage"):
        if isinstance(ev.get(key), dict):
            return ev[key]
    # 有时嵌在 data/message 里
    for container in ("data", "message"):
        inner = ev.get(container)
        if isinstance(inner, dict):
            for key in ("usage", "tokenUsage", "token_usage"):
                if isinstance(inner.get(key), dict):
                    return inner[key]
    return None


def _num(d: dict, *keys: str) -> int:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def run_agent(harness: DeepSeekHarness, prompt: str, session_id: str) -> AgentResult:
    """跑一个角色 agent，返回文本 + 事件 + 成本。

    session_id 作为可读前缀，附加唯一后缀——dsh-home 会持久化 session，
    固定 id 在重跑时会因 “session already exists” 报错。
    """
    unique = f"{session_id}-{uuid.uuid4().hex[:8]}"
    res = harness.run(prompt, session_id=unique)
    return AgentResult(res.final_response, res.events, _cost_of(res.events), res.finish_reason)


def extract_json(text: str):
    """从 agent 文本里稳健地抽出最后一个 JSON 对象。"""
    # 1) ```json ... ``` 围栏
    for block in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass
    # 2) 括号配平扫描，取能解析的最大对象
    best = None
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start : i + 1]
                try:
                    obj = json.loads(candidate)
                    if best is None or len(candidate) > best[0]:
                        best = (len(candidate), obj)
                except json.JSONDecodeError:
                    pass
    if best is not None:
        return best[1]
    # 3) json-repair 兜底：模型常在字符串值里塞未转义的英文双引号
    return _repair_json(text)


def _repair_json(text: str):
    """用 json-repair 修复常见的 LLM JSON 缺陷（未转义引号、尾逗号等）。"""
    from json_repair import repair_json

    # 优先修复围栏内的块；没有围栏就修复从第一个 { 到最后一个 } 的片段
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = blocks if blocks else []
    first, last = text.find("{"), text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first : last + 1])
    for cand in reversed(candidates):
        try:
            obj = repair_json(cand, return_objects=True)
            if isinstance(obj, (dict, list)) and obj:
                return obj
        except Exception:
            continue
    return None
