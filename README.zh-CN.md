# Aarvia

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个本地优先的职业导航工具。它把用户确认过的职业事实整理成可解释的
方向推荐、可复用的基础简历，以及能够追溯来源的 JD 匹配报告。

**当前版本：0.25.0**

## 现在能做什么

1. **建立 Career Profile**：支持自然语言对话、UTF-8 文本文件和完全手动访谈。
2. **比较职业方向**：目前覆盖 Applied AI Engineer、Machine Learning Engineer 和
   Research Engineer。每项推荐都能追溯到证据，审核后才会影响结果。
3. **准备角色中立的基础简历**：联系方式、结构信息、事实证据和简历措辞分开保存，
   未确认的内容不会悄悄变成简历 bullet。
4. **匹配本地 JD**：使用同一套已确认事实，区分充分支持、部分支持、缺少证据、
   明确不符和申请待办。
5. **审核最小调整建议**：针对项目/经历排序和现有 bullet 选择生成建议；建议与
   用户决定分开保存；随后可把 accepted 排序建议应用到新的 Tailored Resume，
   原 Base Resume 不会被覆盖。
6. **审核可选的 JD 定向 bullet 改写**：Provider 只能提出已有 bullet 的候选措辞；
   只有用户能接受。接受后写入新简历，不修改 master wording。
7. **逐条事实检查最终简历**：Python 校验 provenance、membership、引用和 accepted
   措辞；Profile owner 独立记录每条最终表达的人工语义审核结果。
8. **把已审核的最终简历导出为 PDF**：本地 renderer 原样保留已验证的顺序和措辞，
   支持 Letter 页面自然分页，全程不调用 Provider。

Aarvia 的推荐不是面试或录取概率。“缺少证据”也不等于“用户不会”。

## 快速开始

安装项目并配置一个 OpenAI-compatible Provider：

```bash
python -m pip install .

export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-model"
# OpenAI 可省略；其他 compatible Provider 通常需要
export AARVIA_LLM_BASE_URL="https://your-provider.example/v1"
```

### 1. 建立 Profile

```bash
# 交互式讲述经历
aarvia discover --narrative --profile profile.json

# 或使用本地 UTF-8 文件
aarvia discover --narrative-file background.txt --profile profile.json

# 完全手动，不调用 LLM
aarvia discover --manual --profile profile.json
```

以后继续补充：

```bash
aarvia discover --follow-up --profile profile.json
```

### 2. 生成职业方向推荐

```bash
aarvia recommend --profile profile.json \
  --mapping-output mapping.json \
  --output recommendation.json
```

第一次运行由 Provider 提出证据关系；ID、验证、分数、置信度和排名均由 Python
负责。使用 `aarvia review-evidence --help` 审核模糊或 provisional 证据，之后可从
已保存的 mapping 重新计算，无需再次调用 Provider。

### 3. 匹配一份 JD

```bash
aarvia match-jd --help
```

`match-jd` 接收本地 UTF-8 JD、明确的分析日期和已经保存的职业文件。它会生成
结构化 JD 分析、结构化匹配结果和可读报告，不会修改基础简历。

### 4. 生成并审核调整建议

```bash
aarvia propose-tailoring --help
aarvia review-tailoring-proposal --help
```

第一条命令从已验证的 Base Resume 与 JD Match 生成可追溯建议；第二条命令把
`accepted`、`rejected` 或 `deferred` 决定保存为独立 Review。只应用 accepted
排序建议：

```bash
aarvia apply-tailoring --help
```

该命令新建 Tailored Resume JSON、Markdown 和变更记录，不覆盖 Base Resume。
它不会改写 bullet、增加事实、删除内容或跨 section 移动内容。

独立的人工措辞审核流程：

```bash
aarvia propose-tailored-wording --help
aarvia review-tailored-wording --help
aarvia apply-tailored-wording --help
```

Provider 只看到 JD、可改写的已有 bullet 及合格事实引用，不会收到 Contact。
Python 校验 ID 与 provenance；候选是否忠实于事实仍必须由用户判断。完整响应先通过
严格校验，之后才会统计并省略与原文逐字相同的候选，因此合法运行可以没有改写建议。

### 5. 检查最终简历

```bash
aarvia fact-check-resume --help
```

命令支持纯排序式 Tailored Resume，也支持可选的已审核 wording overlay。它生成
typed 检查结果和不含 Contact 的可读报告，不修改简历，也不调用 Provider。

### 6. 导出已审核的最终简历

```bash
aarvia export-final-resume-pdf --help
```

导出前必须提供与当前最终内容严格匹配、且所有 bullet 都为 `passed` 的 Final Resume
Fact Check。renderer 从 typed artifact 重建内容，不解析任意 Markdown。该审核范围仍
仅限 bullet，并不代表 Contact、Education、Skills 或整份简历已全面核实。ReportLab
随 Aarvia 安装；开发者使用 `.[dev]` 可获得 `pypdf` 文字提取检查。

## 命令地图

| 目标 | 命令 | 是否调用 Provider |
| --- | --- | --- |
| 建立或继续 Career Profile | `aarvia discover` | 可选 |
| 推荐职业方向 | `aarvia recommend` | 仅首次 mapping |
| 审核证据 | `aarvia review-evidence` | 否 |
| 选择 Primary / Secondary 方向 | `aarvia decide` | 否 |
| 分析优势、待发展项和未知项 | `aarvia analyze-gaps` | 否 |
| 建立可复用事实银行 | `aarvia build-evidence-bank` | 否 |
| 补充用户确认的证据细节 | `aarvia enrich-evidence` | 否 |
| 准备简历安全材料 | `aarvia prepare-resume-materials` | 否 |
| 审核简历措辞 | `aarvia review-resume-wording` | 否 |
| 补充私密联系方式和结构信息 | `aarvia supplement-resume-profile` | 否 |
| 组装角色中立的 master resume | `aarvia build-base-resume` | 否 |
| 将 master resume 渲染为 Markdown | `aarvia render-base-resume` | 否 |
| 匹配本地 JD | `aarvia match-jd` | 是 |
| 生成最小排序/选择建议 | `aarvia propose-tailoring` | 否 |
| 审核调整建议 | `aarvia review-tailoring-proposal` | 否 |
| 应用 accepted 排序建议 | `aarvia apply-tailoring` | 否 |
| 生成可选 bullet 改写 | `aarvia propose-tailored-wording` | 是 |
| 审核 bullet 改写 | `aarvia review-tailored-wording` | 否 |
| 应用 accepted 改写 | `aarvia apply-tailored-wording` | 否 |
| 对最终 bullet 逐条事实审核 | `aarvia fact-check-resume` | 否 |
| 将已验证的最终简历导出为 PDF | `aarvia export-final-resume-pdf` | 否 |

对任意命令运行 `--help` 可查看所需输入路径。流程看起来较长是有意设计：
每一步都会重新验证输入，避免过期或未审核的事实静默进入后续结果。

## 数据如何流动

```text
Career Profile
  -> 证据 mapping 与审核
  -> 职业推荐与用户决定
  -> Gap Analysis 与 Evidence Bank
  -> 已审核的简历材料
  -> 角色中立的基础简历
  -> JD 匹配报告
  -> 排序 Proposal 与用户 Review
  -> 可选措辞 Proposal 与用户 Review
  -> 最终简历事实检查与用户语义审核
  -> 已验证的 PDF 导出
```

这些文件分开保存是为了守住边界：模型建议不是事实，gap 不是简历证据，
简历措辞也不能提高证据强度。

## 重要限制

- 真实模型仍可能误解 JD 条件、归错事实或作无依据推断。**每份 JD 报告都需要人工审核。**
- `scripts/` 下的 instructions 变体是离线、非生产实验，没有替换生产 prompt。
- 目前可应用用户 accepted 的排序和措辞候选，并将全部 bullet 已通过审核的最终简历
  导出为本地 PDF。PDF 导出只保留已验证内容，不会自动证明 Provider 措辞的语义忠实度，
  也不会自动申请。
- 一个私有真实 JD 已完成“人工建议 → 用户确认 → 简历输出 → bullet 事实检查 → PDF 导出”
  的 Phase 3 最小人工审核闭环。这不代表自动 Tailoring、自动语义事实判断、自动投递或
  真实模型质量已普遍通过。本次真实措辞流程中的候选全部被拒绝，最终保留原文；accepted
  改写的真实模型应用路径仍待验收。正式事实审核仅覆盖 4 条 bullet，不覆盖简历其余部分。
- Production Role Catalog 目前有 8 个 Role Family，但尚未发布 requirement 和 source。
  推荐 Rubric 可以使用，市场数据不能被当成完整数据集。

## 隐私与安全

- 事实只有在用户确认后才会保存。
- 被拒绝的证据不参与评分；模糊证据在审核前贡献为零。
- Profile 和 Review 文件使用原子写入。
- Diagnostics 只保存安全元数据，不保存 API Key、原始 Provider 响应或 Profile 摘录。
- 私人数据应放在 `local_data/` 等被 Git 忽略的位置。不要提交 `.env`、Profile、
  简历或 Provider 响应。

私密联系方式属于 Resume Profile Supplement，不应进入公共 Career Profile 或日志。
Supplement 只能补充结构信息，不能把未覆盖经历变成成就。

## 阿里云百炼

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"
```

API Key、endpoint 和模型需要属于同一地域。Aarvia 对百炼使用 Chat Completions，
不会把百炼专用参数发送给 OpenAI endpoint。

## 项目状态

- Career Profile 与 Adaptive Discovery：完成
- 可解释职业推荐与证据审核：完成
- Gap Analysis、Evidence Bank 与基础简历底座：完成
- 可追溯本地 JD Matching：已实现，语义结论仍须人工审核
- 可追溯 Tailoring Proposal 与用户审核：已实现
- 应用 accepted 排序建议：已实现
- 人工审核的 JD 定向改写：已实现
- 可追溯最终简历事实检查：已实现，语义结论仍由用户判断
- 已验证最终简历 PDF 导出：已支持 ordering-only 与 wording-overlay 输出
- 自由改写、自动语义判断与申请流程：尚未实现

下一步是用更多真实案例验收这一刻意收窄、必须人工审核的调整闭环。

## 开发验证

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```

工程历史见[脱敏阶段摘要](docs/conversation-log.md)和[开发日志](docs/codex-log.md)。
