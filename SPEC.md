# Paper Scout — 多 Agent 论文调研助手（最终 Spec，已冻结）

在 dsh 上做一个多 Agent 论文调研助手：给**种子论文标题**，自动产出「必读清单 + 领域综述 + 创新点/gap 清单」。

## 1. 动机 / 解决什么
研究生读论文找创新点。单 agent 综述会**漏**（广度不够）、**浅**（不分工深挖）、**不主动找 gap**。
→ 用多 agent 分工：广度铺开 + 独立上下文精读 + 专门的 gap 分析。

## 2. 输入 / 数据源 / 接入
- **输入（MVP）**：1–2 篇种子论文的**标题**。经 `search_paper_by_title` 解析成 AMiner paper id。
  - 【延后】PDF（本地抽标题定位 + 读全文增强 Planner）、DOI、arXiv/AMiner 链接。
- **数据源**：**只用 AMiner**，走托管 MCP（`https://mcp.aminer.cn/mcp`，streamable-http，Bearer token，26 个学术工具）。
- **接入 = A2（已实测通过）**：把 AMiner MCP 经 `--patch` 挂进源码 dsh runtime，dsh agent 自主调 `mcp__aminer__*` 工具。不做 A1 退路。

## 3. 架构（3 层）
1. **编排层 = Python**：决定跑哪几个 agent、按序传递中间结果、成本记账、组装报告。
2. **推理层 = dsh agent**：每个角色一次 `harness.run`（全新隔离 session），角色说明写进提示词，都带 AMiner 工具。
3. **数据层 = AMiner MCP 工具**。

## 4. 功能流水线与数据契约
| 环节 | 输入 | 做什么（自主调 AMiner） | 产出 |
|---|---|---|---|
| **Planner** | 种子标题 | `search_paper_by_title`→id；`get_paper_detail`读摘要/关键词 → 框定领域 | 领域框定 + K 个子方向 + 每方向检索词（JSON plan） |
| **Searcher×K** | 一个子方向 | `search_paper` 广度搜；`get_paper_detail` 取全摘要；`get_paper_citations`/`recommend_paper` 沿引用/推荐扩展 | 该方向语料：每篇 {id, 标题, 年份, 被引档位, 问题, 方法, 贡献, 局限} |
| **Triage** | 全部语料 | 跨方向去重，按 相关性×被引档位×年份 排序（Python 计算） | **① 必读清单**（排序 + 为什么读） |
| **Synthesizer** | 语料 + 必读清单 | 方法分类 / 共识与分歧 / 时间线 | **② 领域综述**（带引用） |
| **Gap** | 综述 + 语料 | 找没人做/做得浅的交叉点，每条 grounded 到具体论文 | **③ 创新点/gap 清单**（每条：现状证据 + 缺口） |
| **Verifier** | gap 清单 + 语料 | 核验每条 gap 不是语料里已有工作（Reflection），剔除假新 | 定稿 gap |
| **报告组装** | 三段 + 记账 | Python 合成 | 一份 Markdown 报告 + 成本小结 |

## 5. 产出
- 一份**中文 Markdown 报告**（论文标题留英文原名），含三段：必读清单 / 领域综述 / 创新点清单，末尾附成本小结。
- 存到 `~/paper-scout/reports/<课题>-<时间戳>.md`。

## 6. 成本控制
- **记账机制**（从 `RunResult.events` 抽）：每次运行统计 AMiner 调用次数 + token + 估算花费，写进报告末尾。
- **上限做成开关，默认不限**，数值以后再定。
- **省钱手法**：广度用限免搜索 + 批量 `get_papers_info`（部分摘要），只对入选 top-N 花钱取 `get_paper_detail`（全摘要）。

## 7. 默认旋钮
中文报告 Markdown｜子方向 K≤4｜每方向候选 ≤8、精读 top 3–4｜模型 `deepseek-v4-flash`｜今晚**串行**跑各 agent（并行留作优化）｜项目在 `~/paper-scout`。

## 8. AMiner 关键工具（已核实）
`search_paper_by_title`（定位种子）｜`search_paper`（广度搜，返回 id/DOI/被引档位）｜`get_paper_detail`（**完整 abstract + 中文摘要 + 关键词 + 被引数**）｜`get_paper_citations`（参考文献，**向后**）｜`recommend_paper`（按主题/学者推荐）｜`get_papers_info`（批量部分摘要）。
**局限**：只有向后参考文献（无向前引用）、只有摘要无全文。

## 9. 技术与启动（已验证）
Python SDK `deepseek-harness-sdk` 驱动，关键配置：
- `dsh_bin=~/paper-scout/dsh-src.sh`（源码 dsh 经 tsx 启动，保证 mcp-client 可解析）
- `runtime_cwd=/Users/axton/deepseek-harness`（tsx 解析）、`cwd=~/paper-scout/workspace`（agent 工作目录）
- `dsh_home=~/paper-scout/dsh-home`（SDK 要求显式 home）
- `profile=sdk`、`patches=[patches/aminer.patch.yml]`
- env：`AMINER_TOKEN` + `DEEPSEEK_API_KEY`（存在 `.env`，已 gitignore）
- 每个 agent 角色 = 一次 `harness.run(角色+任务提示)`，session 隔离。

## 10. 构建里程碑（今晚顺序）
- **M1** 种子解析 + Planner：标题 → AMiner id → 摘要 → 出 K 个子方向的 plan。
- **M2** 单个 Searcher：一个子方向 → 语料 + 每篇笔记。
- **M3** Triage + 循环跑 K 个 Searcher → 必读清单。
- **M4** Synthesizer → 综述。
- **M5** Gap + Verifier → 创新点清单。
- **M6** 报告组装 + 成本小结。

## 11. 完成判据（MVP）
给一个真实课题的种子论文标题，能产出一份含三段 + 成本小结的中文 Markdown 报告。

## 12. 状态
- ✅ Step 0：A2 已实测通过（dsh agent 自主调用 AMiner MCP 工具，拿回真数据）。
- ⏭️ 下一步：按 M1 开始搭。首次真跑需要你给种子论文标题。
