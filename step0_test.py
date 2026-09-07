"""显式联网的 AMiner 冒烟测试，会产生一次模型调用及最多一次搜索。"""
from paperscout.harness import make_harness, run_agent
from paperscout.provenance import retrieval_records


def main():
    with make_harness() as harness:
        result = run_agent(harness, '用 search_paper_by_title 搜索 Attention Is All You Need；只返回第一篇标题和 ID。',
                           'aminer-smoke', tool_limits={'search_paper_by_title': 1})
    records = retrieval_records(result.events, result.session_id)
    if not records:
        raise RuntimeError(f'未收到可验证论文返回，请查看 runs/standalone/agents/{result.session_id}.json')
    print(result.text)
    print(result.cost)


if __name__ == '__main__':
    main()
