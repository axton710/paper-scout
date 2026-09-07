"""dsh 启动配置 + agent 调用/解析/记账的复用库。

每个角色一次 harness.run（全新隔离 session），角色+任务写进提示词；
AMiner 工具经 --patch 挂进源码 dsh runtime（A2，已实测）。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from deepseek_harness import DeepSeekHarness

PROJECT = Path(__file__).resolve().parent.parent          # ~/paper-scout
POLICIES = PROJECT / "dsh-home" / "policies"


def load_env() -> None:
    """把 .env 读进进程环境（loader 的 !!js 从 process.env 取 AMINER_TOKEN）。"""
    env_file = PROJECT / ".env"
    for line in (env_file.read_text().splitlines() if env_file.exists() else []):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def make_harness(model: str = "deepseek-v4-flash", max_tokens: int = 12288,
                 artifact_dir: Path | None = None, use_aminer: bool = True) -> DeepSeekHarness:
    """按已验证的 A2 配置构造 harness（源码 dsh + AMiner 补丁）。"""
    load_env()
    for key in (["AMINER_TOKEN"] if use_aminer else []) + ["DEEPSEEK_API_KEY", "DEEPSEEK_HARNESS_REPO"]:
        if not os.environ.get(key):
            raise ValueError(f"缺少 {key}，请设置环境变量或项目 .env")
    repo = Path(os.environ["DEEPSEEK_HARNESS_REPO"]).expanduser().resolve()
    if not (repo / "apps/cli/src/bin.ts").is_file():
        raise ValueError(f"DEEPSEEK_HARNESS_REPO 不是 Harness 源码目录: {repo}")
    (PROJECT / "workspace").mkdir(exist_ok=True)
    POLICIES.mkdir(parents=True, exist_ok=True)
    profile = PROJECT / "dsh-home/profiles/sdk"
    profile.mkdir(parents=True, exist_ok=True)
    for source in (PROJECT / "config/sdk").iterdir():
        target = profile / source.name
        if not target.exists():
            shutil.copyfile(source, target)
    guard_patch = POLICIES / f"budget-{uuid.uuid4().hex[:8]}.patch.json"
    ready_file = guard_patch.with_suffix(".ready")
    guard_patch.write_text(json.dumps([{"insert": [{
        "id": "paperscout-budget", "name": (PROJECT / "runtime/budget.mjs").as_uri(),
        "config": {"policyDir": str(POLICIES), "readyFile": str(ready_file)},
    }]}]))
    patches = ((str(PROJECT / "patches/aminer.patch.yml"),) if use_aminer else ()) + (str(guard_patch),)
    harness = DeepSeekHarness(

        dsh_bin=str(PROJECT / "dsh-src.sh"),
        runtime_cwd=str(repo),
        cwd=str(PROJECT / "workspace"),
        dsh_home=str(PROJECT / "dsh-home"),
        profile="sdk",
        patches=patches,
        env={"DEEPSEEK_HARNESS_REPO": str(repo)},
        provider="deepseek-official",
        model=model,
        max_tokens=max_tokens,
        initialize_timeout_seconds=180.0,
        request_timeout_seconds=300.0,
    )
    harness.budget_ready = ready_file
    harness.artifact_dir = artifact_dir or PROJECT / "runs" / "standalone"
    return harness


@dataclass
class Cost:
    """一次运行的成本（AMiner 计费按调用次数，token 是 DeepSeek 侧）。"""

    aminer_calls: int = 0
    aminer_tools: dict[str, int] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def merge(self, other: "Cost") -> None:
        self.aminer_calls += other.aminer_calls
        for k, v in other.aminer_tools.items():
            self.aminer_tools[k] = self.aminer_tools.get(k, 0) + v
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens


@dataclass
class AgentResult:
    text: str
    events: list
    cost: Cost
    finish_reason: str | None
    session_id: str = ""


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
        # 只统计最终 message，避免同一用量在流式 chunk 与 message 中重复累计。
        usage = _find_usage(ev)
        if usage and etype == "assistant/message":
            cost.input_tokens += _num(usage, "input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
            cost.output_tokens += _num(usage, "output_tokens", "outputTokens", "completion_tokens", "completionTokens")
            cost.cache_read_tokens += _num(usage, "cacheReadTokens", "cache_read_tokens")
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


def run_agent(harness: DeepSeekHarness, prompt: str, session_id: str,
              tool_limits: dict[str, int] | None = None, known_ids: list[str] | None = None) -> AgentResult:
    """每次调用独立预算；先落盘原始输出，使解析失败也能回看与记账。"""
    if hasattr(harness, "budget_ready") and not harness.budget_ready.is_file():
        raise RuntimeError("预算插件未加载，拒绝发起模型调用")
    unique = f"{session_id}-{uuid.uuid4().hex[:8]}"
    POLICIES.mkdir(parents=True, exist_ok=True)
    (POLICIES / f"{unique}.json").write_text(json.dumps({
        "tools": {f"mcp__aminer__{name}": limit for name, limit in (tool_limits or {}).items()},
        "known_ids": known_ids or [],
    }))
    artifacts = Path(harness.artifact_dir) / "agents"
    artifacts.mkdir(parents=True, exist_ok=True)
    output = artifacts / f"{unique}.json"
    try:
        res = harness.run(prompt, session_id=unique)
    except Exception as error:
        # 运行边界保留异常上下文后继续抛出；不把付费调用失败伪装成空结果。
        output.write_text(json.dumps({"session_id": unique, "status": "failed", "error": str(error),
                                      "cost_status": "unknown"}, ensure_ascii=False, indent=2))
        raise
    cost = _cost_of(res.events)
    calls = POLICIES / f"{unique}.calls.jsonl"
    admitted = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    cost.aminer_tools = {}
    for call in admitted:
        name = call["tool"].removeprefix("mcp__aminer__")
        cost.aminer_tools[name] = cost.aminer_tools.get(name, 0) + 1
    cost.aminer_calls = len(admitted)
    result = AgentResult(res.final_response, res.events, cost, res.finish_reason, unique)
    output.write_text(json.dumps({**asdict(result), "admitted_calls": admitted}, ensure_ascii=False, indent=2))
    finishes = [e.get("data", {}).get("chunk", {}).get("reason", {}).get("kind")
                for e in res.events if e.get("type") == "assistant/chunk"
                and e.get("data", {}).get("chunk", {}).get("type") == "finish"]
    if res.finish_reason != "completed" or not res.final_response.strip() or (finishes and finishes[-1] != "stop"):
        from .validation import OutputError
        raise OutputError(f"{unique}: 输出未完整结束 ({res.finish_reason}, model_finish={finishes[-1] if finishes else None})，原始输出: {output}")
    return result


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
