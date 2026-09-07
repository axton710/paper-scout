"""实验二补 · 子方向命中数 + 发表年份对照。

把「直接问 LLM 的 20 篇」与「Paper Scout 的 31 篇」用同一把尺子逐篇分类：
该论文的主要贡献是否落在种子定义的目标主题——**缺陷/异常图像的生成与合成**
（含扩散/GAN/copy-paste/噪声模拟等生成手段，及其可控性/质量/掩码对齐/为下游合成增广），
而非单纯的异常检测/定位/表征/基准/综述。分类由一次判定 agent 统一完成，规则对两组一致。

同时算两组的发表年份中位数/均值。结果写入 experiments/exp2b_result.json。
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from paperscout.harness import extract_json, make_harness, run_agent

HERE = Path(__file__).resolve().parent
OUT = HERE / "exp2b_result.json"

_JUDGE = """你是文献分类裁判。目标研究主题（由两篇种子论文定义）是：**缺陷/异常图像的生成与合成**——即用扩散模型、GAN、copy-paste、噪声模拟等手段**人工构造缺陷/异常图像**，或直接服务于该目标的可控性、质量评估、掩码对齐、以及「用合成样本增广下游异常检测/分割」的工作。

不属于该主题的是：**单纯的异常检测/定位/表征方法、数据集/基准、库、综述**——即使它们很有名、也在异常检测大领域内，但主要贡献不是「生成/合成缺陷图像」。

**不要输出长篇推理，直接给出 JSON。** 请对下面每篇论文，仅凭标题判断其主要贡献是否落在上述目标主题，输出 label：
- "generation"：主要贡献是缺陷/异常图像的生成、合成、或直接服务于合成（可控/质量/掩码/合成增广）。
- "detection_or_other"：主要是检测/定位/表征/基准/库/综述等，不以生成合成为核心。

论文清单：
{items}

只输出一个 JSON（```json 围栏）。schema：
```json
{{"labels": [{{"idx": 1, "label": "generation|detection_or_other"}}]}}
```
"""


def _year_stats(years):
    ys = [y for y in years if isinstance(y, int)]
    return {
        "n": len(ys),
        "median": statistics.median(ys) if ys else None,
        "mean": round(statistics.mean(ys), 2) if ys else None,
        "min": min(ys) if ys else None,
        "max": max(ys) if ys else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    args = parser.parse_args()
    llm = json.loads(args.control.read_text())["raw_llm_papers"]
    ps = json.loads(args.corpus.read_text())

    llm_titles = [p.get("title", "") for p in llm]
    ps_titles = [p.get("title", "") for p in ps]

    # 拼一个统一编号清单交给裁判（两组连排，标注来源仅用于回填，不给裁判看来源）
    items = []
    order = []  # (group, local_idx)
    n = 1
    for t in llm_titles:
        items.append(f"{n}. {t}")
        order.append(("llm", n))
        n += 1
    for t in ps_titles:
        items.append(f"{n}. {t}")
        order.append(("ps", n))
        n += 1

    h = make_harness(max_tokens=32000, use_aminer=False)  # 51 条分类，12k 会在推理阶段被截断导致正文为空
    with h:
        r = run_agent(h, _JUDGE.format(items="\n".join(items)), session_id="exp2b-topic-judge")
    obj = extract_json(r.text) or {"labels": []}
    label_by_idx = {d.get("idx"): d.get("label") for d in obj.get("labels", [])}

    def count_gen(group):
        idxs = [gi for g, gi in order if g == group]
        gens = [i for i in idxs if label_by_idx.get(i) == "generation"]
        return len(gens), len(idxs), gens

    llm_gen, llm_tot, _ = count_gen("llm")
    ps_gen, ps_tot, _ = count_gen("ps")

    result = {
        "topic_hit": {
            "criterion": "主要贡献=缺陷/异常图像生成合成(含可控/质量/掩码/合成增广)，裁判规则两组一致",
            "control_llm": {"on_target": llm_gen, "total": llm_tot,
                            "rate_pct": round(llm_gen / max(llm_tot, 1) * 100, 1)},
            "paper_scout": {"on_target": ps_gen, "total": ps_tot,
                            "rate_pct": round(ps_gen / max(ps_tot, 1) * 100, 1)},
        },
        "year": {
            "control_llm": _year_stats([p.get("year") for p in llm]),
            "paper_scout": _year_stats([p.get("year") for p in ps]),
        },
        "labels_detail": {
            "llm": [{"title": llm_titles[gi - 1], "label": label_by_idx.get(gi)} for g, gi in order if g == "llm"],
            "ps": [{"title": ps_titles[gi - 1 - len(llm_titles)], "label": label_by_idx.get(gi)} for g, gi in order if g == "ps"],
        },
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != "labels_detail"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
