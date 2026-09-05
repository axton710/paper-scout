"""第 0 步：验证 A2 —— dsh agent 能不能通过挂进来的 AMiner MCP 自主调工具。

成功判据：run 结果里出现对 mcp__aminer__* 工具的调用，且拿到论文数据。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from deepseek_harness import DeepSeekHarness

HERE = Path(__file__).resolve().parent
REPO = Path("/Users/axton/deepseek-harness")


def load_env() -> None:
    """把 .env 读进进程环境（loader 里的 !!js 从 process.env 取 token）。"""
    env_file = HERE / ".env"
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    load_env()
    assert os.environ.get("AMINER_TOKEN"), "缺 AMINER_TOKEN"
    assert os.environ.get("DEEPSEEK_API_KEY"), "缺 DEEPSEEK_API_KEY"

    workspace = HERE / "workspace"
    workspace.mkdir(exist_ok=True)
    dsh_home = HERE / "dsh-home"
    dsh_home.mkdir(exist_ok=True)

    harness = DeepSeekHarness(
        dsh_bin=str(HERE / "dsh-src.sh"),
        runtime_cwd=str(REPO),          # 让 tsx/esm 在仓库根被解析
        cwd=str(workspace),             # agent 的工作目录
        dsh_home=str(dsh_home),
        profile="sdk",
        patches=(str(HERE / "patches" / "aminer.patch.yml"),),
        provider="deepseek-official",
        model="deepseek-v4-flash",
        max_tokens=8192,
        initialize_timeout_seconds=180.0,  # 源码经 tsx 启动较慢
        request_timeout_seconds=240.0,
    )

    prompt = (
        "请使用 mcp__aminer__search_paper_by_title 工具，"
        "按标题搜索 “Attention Is All You Need”，"
        "然后只把你找到的第一篇论文的标题和它的 id 告诉我，不要做别的事。"
    )

    try:
        with harness:
            print(">>> 已启动 runtime，发起请求 ...", flush=True)
            result = harness.run(prompt, session_id="step0-aminer-smoke")
    except Exception as exc:  # 启动/请求失败：把 stderr 打出来诊断
        print("!!! 失败：", repr(exc))
        try:
            for line in list(harness.client._stderr_lines)[-60:]:
                print("stderr>", line)
        except Exception:
            pass
        raise

    print("\n===== final_response =====")
    print(result.final_response)

    # 扫描事件里有没有 mcp__aminer__ 的工具调用
    tool_hits = []
    for ev in result.events:
        blob = json.dumps(ev, ensure_ascii=False)
        if "mcp__aminer__" in blob:
            tool_hits.append(ev.get("type") if isinstance(ev, dict) else "?")
    print("\n===== 判定 =====")
    print(f"事件总数: {len(result.events)}")
    print(f"命中 mcp__aminer__ 的事件类型: {tool_hits}")
    print("A2 验证:", "✅ 通过（agent 自主调了 AMiner 工具）" if tool_hits else "❓ 未见 AMiner 工具调用（需排查）")


if __name__ == "__main__":
    main()
