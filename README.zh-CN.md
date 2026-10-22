# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个职业导航 Agent。它会先认真了解真实的你，再讨论下一步往哪里走。

它把用户确认过的信息整理成 Career Profile，每次只追问一个真正缺失的问题，再用可追踪的证据生成职业方向推荐。换一个 JD，不应该摇出一个全新的人。🎰🚫

## 现在做到哪了

**版本 0.20.6**

现在能用：

- 通过自然语言、文本文件或引导式访谈建立 Career Profile
- 每次只问一个问题的 Adaptive Follow-up
- 保存 Profile 前必须确认，修改时显示差异
- 为 Applied AI Engineer、Machine Learning Engineer 和 Research Engineer 分别计算 Current Fit 与 Directional Fit
- 保存可复用的 evidence Mapping，并独立审核模糊分配和语义证据
- 单一全局职业排名：Core-supported 优先，其次是 Extended-only
- 将确认或暂缓的职业方向决定独立保存，不与 Recommendation 混在一起
- 对已确认的 Primary 和 Secondary 方向生成确定性的 Gap Analysis
- 建立确定性的 Evidence Bank，严格分开真实事实、能力链接和待确认需求
- 用户可原样补充并审核 Evidence Enrichment，且不会改写 Evidence Bank
- 生成确定性的 Resume Material，并如实报告尚未覆盖的内容
- 用户可组合材料并审核自己编写的简历措辞，且每条措辞保留来源引用
- 用私有 Resume Profile Supplement 补充已确认的联系方式和未覆盖结构事实
- 生成 role-neutral 的 Base Resume master，并支持明确 omission、私有布局 session 和忠实保留事实的确定性 Markdown
- 拆解本地 UTF-8 JD，并用 Phase 2 已确认事实生成可追溯匹配报告
- 保守的 JD 支持语义：非强制示例不是额外门槛，仅有相关背景也不算部分满足
- 支持 OpenAI-compatible Provider，包括阿里云百炼
- 原子 JSON 保存、确定性验证和隐私安全的诊断信息

还没实现：

- 实时岗位搜索
- 简历定制和自动申请

Supplement 属于本地私密数据：联系方式及其内容派生 ID 不应提交到公开仓库。它只补结构信息，不能把未覆盖的经历或技能变成成就。

Production Role Catalog 目前仍是 **8 个 Role Family、0 条已发布 requirement、0 个 source**。推荐 Rubric 已经存在，但 Aarvia 不会把尚未完成的市场数据假装成事实。

## 快速开始

```bash
python -m pip install .

export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-model"
# OpenAI 可省略；其他 compatible Provider 通常需要
export AARVIA_LLM_BASE_URL="https://your-provider.example/v1"
```

建立 Profile：

```bash
# 交互式讲述自己的经历
aarvia discover --narrative --profile profile.json

# 使用较长的 UTF-8 文本
aarvia discover --narrative-file background.txt --profile profile.json

# 继续补充已有 Profile，每次只处理一个缺失主题
aarvia discover --follow-up --profile profile.json

# 完全手动，不调用 LLM
aarvia discover --manual --profile profile.json
```

生成并审核职业方向推荐：

```bash
# 首次运行：Provider 提出证据绑定，Python 负责验证和评分
aarvia recommend --profile profile.json \
  --mapping-output mapping.json \
  --output recommendation.json

# 先处理模糊证据分配，再审核 provisional 语义证据
aarvia review-evidence --profile profile.json \
  --mapping mapping.json \
  --allocation-output allocation-review.json \
  --output evidence-review.json

# 使用已保存的 artifact 重算；不会再次调用 Provider
aarvia recommend --profile profile.json \
  --mapping-artifact mapping.json \
  --allocation-review-artifact allocation-review.json \
  --review-artifact evidence-review.json \
  --output reviewed-recommendation.json

# 选择职业方向；不会调用 Provider
aarvia decide --profile profile.json \
  --recommendation reviewed-recommendation.json \
  --output career-decision.json

# 只分析已确认的 Primary 和 Secondary；不会调用 Provider
aarvia analyze-gaps --profile profile.json \
  --mapping mapping.json \
  --recommendation reviewed-recommendation.json \
  --decision career-decision.json \
  --allocation-review-artifact allocation-review.json \
  --review-artifact evidence-review.json \
  --output gap-analysis.json

# 从完整验证链建立可复用的事实银行；不调用 Provider
aarvia build-evidence-bank --profile profile.json \
  --mapping mapping.json \
  --allocation-review-artifact allocation-review.json \
  --review-artifact evidence-review.json \
  --recommendation reviewed-recommendation.json \
  --decision career-decision.json \
  --gap-analysis gap-analysis.json \
  --output evidence-bank.json

# 用自己的原话补充并审核细节；不调用 Provider
aarvia enrich-evidence --profile profile.json \
  --mapping mapping.json \
  --allocation-review-artifact allocation-review.json \
  --review-artifact evidence-review.json \
  --recommendation reviewed-recommendation.json \
  --decision career-decision.json \
  --gap-analysis gap-analysis.json \
  --evidence-bank evidence-bank.json \
  --output evidence-enrichment.json
```

证据编号支持单个或逗号分隔输入，例如 `1, 2`。
补充过程中可用 `b` 返回；最终保存前可以编辑或删除本轮 Claim。

使用 `aarvia prepare-resume-materials --help` 准备可追踪、角色中立的 Resume Material。
它不是成品简历；尚未覆盖的 Profile 记录和联系方式会明确保留。

使用 `aarvia review-resume-wording --help` 将同一来源下的材料组合成用户自己编写并审核的措辞。
该阶段不调用 Provider，也不会渲染成品简历。

使用 `aarvia supplement-resume-profile --help` 审核私密联系方式及尚未覆盖的 Education、Skills
和 Experience 结构信息，不需要改写 Career Profile。
记录区支持 `all`、编号列表和范围批量审核。输入 `p` 可原子保存私密会话草稿，之后用
`--resume-session` 显式恢复。

使用 `aarvia build-base-resume --help` 在不改写事实的前提下组装 master resume；
它只允许纳入、排除、恢复和调整顺序，`p` 会保存独立的私有布局 session。
随后用 `aarvia render-base-resume --help` 在重新验证完整 provenance 链后生成确定性
Markdown。输出是 role-neutral 的完整 master，不强制一页，也不针对具体 JD 定制。

使用本地 UTF-8 JD 进行匹配且不修改 Base Resume：

```bash
aarvia match-jd --help
```

`match-jd` 会分别保存 JD 分析、结构化匹配 artifact 和可读报告，并区分充分支持、
部分支持、当前材料缺少证据及有事实支撑的明确不符。缺少证据不等于用户不会，
报告也不是面试或 Offer 概率。事实 ID 必须逐字来自本次请求库存；Provider 首次返回
无效引用时最多纠正一次，再次失败则停止且不写入输出。

严格的来源与契约校验并不能让开放式语义判断变成确定性结果。真实模型使用已经表明，
模型仍可能误解条件、归错事实或作无依据推断，因此每份匹配报告都需要人工审核。
`scripts/` 下的 B/C instructions 只是非生产实验，并未替换生产 prompt。

运行时必须显式提供 `--analysis-date YYYY-MM-DD`。JD Analysis schema 3 将分析日期与
实习时间、毕业时间和申请截止日期分开保存；申请截止日期和材料要求属于程序性待办，
不会被表述为能力缺口。
Provider 只分类评估模式、AND/OR 结构和支持范围，最终匹配状态由 Python 派生；
不一致的组合会被拒绝。
每条事实同时携带最小化、无联系方式的来源背景；每项 JD 要求保存所属段落、逻辑关系
和逐字条件片段。来源背景只解释 provenance，不会增加能力证据。
Provider 输出必须是仅含顶层 `requirements` 的数据实例；schema 外壳和互相重叠的
条件片段会由生产匹配与本地 Eval 共用的同一契约拒绝。

职业方向推荐衡量的是证据覆盖情况，**不是获得面试或 Offer 的概率**。

## Follow-up 输入命令

- `.done`：提交多行回答。
- `.cancel`：放弃当前回答并重新提问。
- `:none`：明确没有内容可补充。
- `:skip`：这次跳过，以后可以再问。
- `:decline`：用户选择不回答。
- `:finish`：查看本轮总结；`q`：取消且不保存。

如果没有缺失内容，也没有任何变化，Aarvia 会直接退出，不调用 Provider，也不重写文件。知道什么时候什么都不做，也是一种能力。

## 百炼配置

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"
```

API Key、endpoint 和模型需要属于同一地域。Aarvia 对百炼使用 Chat Completions，也不会把百炼专用参数发送给 OpenAI endpoint。

## 安全承诺 🔒

- 提取出的事实只有在用户确认后才会进入正式 Profile。
- Provider 可以提出证据关系；ID、验证、分配、状态、分数、置信度和排名都由 Python 决定。
- Profile reference 必须指向当前 Profile 中真实存在的字段和精确证据。
- 模糊证据在审核前贡献为零；被拒绝的证据绝不参与评分。
- Correction 和 Review 不能静默覆盖无关事实。
- Profile 和 Review 文件使用原子写入。
- 显式开启的 diagnostics 只保存安全元数据，不保存原始 Provider 响应、API Key 或 Profile 摘录。

私人 Profile 和 diagnostics 应保存在 `local_data/` 等被 Git 忽略的位置。不要提交密钥。

## 路线图

- **Phase 1：** Career Profile 与 Adaptive Discovery — 已完成
- **Phase 2A：** 共享数据契约与 Role Catalog 基础 — 已完成
- **Phase 2B：** 美国 early-career 市场证据与 curation 基础 — 已完成，正式发布数据集仍未完成
- **Phase 2C：** 可解释推荐、证据审核与 User Decision — 已完成
- **Phase 2D：** Gap Analysis、Evidence Bank、用户补充、Resume Material 与措辞审核基础 — 已完成
- **Phase 3A：** 本地 JD 拆解与可追溯匹配 — 已实现并通过契约测试，也完成了一次私有真实 JD 使用；语义结论仍须人工审核
- **Phase 3B+：** 私有真实 JD 已走通一次人工辅助的建议、确认、简历输出与事实检查；自动 Tailoring、自动语义事实检查、统一 PDF 导出、实时搜索与申请自动化尚未实现

下一步最小产品目标是把“调整建议 → 用户确认 → 简历输出”做成强类型、可审核的正式流程。
已经完成的私有人工闭环只是使用验证，不代表自动 Tailoring 已实现。

## 开发验证

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```

工程历史请查看[脱敏后的分阶段摘要](docs/conversation-log.md)和[开发日志](docs/codex-log.md)。它们与 README 分开保存，让项目首页保持清爽。
