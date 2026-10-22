# Aarvia

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个本地优先的职业导航工具。它把用户确认过的职业事实整理成可解释的
方向推荐、可复用的基础简历，以及能够追溯来源的 JD 匹配报告。

**当前版本：0.20.6**

## 现在能做什么

1. **建立 Career Profile**：支持自然语言对话、UTF-8 文本文件和完全手动访谈。
2. **比较职业方向**：目前覆盖 Applied AI Engineer、Machine Learning Engineer 和
   Research Engineer。每项推荐都能追溯到证据，审核后才会影响结果。
3. **准备角色中立的基础简历**：联系方式、结构信息、事实证据和简历措辞分开保存，
   未确认的内容不会悄悄变成简历 bullet。
4. **匹配本地 JD**：使用同一套已确认事实，区分充分支持、部分支持、缺少证据、
   明确不符和申请待办。

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
```

这些文件分开保存是为了守住边界：模型建议不是事实，gap 不是简历证据，
简历措辞也不能提高证据强度。

## 重要限制

- 真实模型仍可能误解 JD 条件、归错事实或作无依据推断。**每份 JD 报告都需要人工审核。**
- `scripts/` 下的 instructions 变体是离线、非生产实验，没有替换生产 prompt。
- 实时岗位搜索、自动 Tailoring、自动语义事实检查、统一 PDF 导出和自动申请尚未实现。
- 一个私有真实 JD 已完成“人工建议 → 用户确认 → 简历输出 → 事实检查”闭环；
  这是使用验证，不代表自动 Tailoring 已实现。
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
- 自动 Tailoring 与申请流程：尚未实现

下一步是把“调整建议 → 用户确认 → 简历输出”做成可审核的正式流程。

## 开发验证

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```

工程历史见[脱敏阶段摘要](docs/conversation-log.md)和[开发日志](docs/codex-log.md)。
