# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个职业导航 Agent。它会先认真了解真实的你，再讨论下一步往哪里走。

它把用户确认过的信息整理成 Career Profile，每次只追问一个真正缺失的问题，再用可追踪的证据生成职业方向推荐。换一个 JD，不应该摇出一个全新的人。🎰🚫

## 现在做到哪了

**版本 0.13.0**

现在能用：

- 通过自然语言、文本文件或引导式访谈建立 Career Profile
- 每次只问一个问题的 Adaptive Follow-up
- 保存 Profile 前必须确认，修改时显示差异
- 为 Applied AI Engineer、Machine Learning Engineer 和 Research Engineer 分别计算 Current Fit 与 Directional Fit
- 保存可复用的 evidence Mapping，并独立审核模糊分配和语义证据
- 单一全局职业排名：Core-supported 优先，其次是 Extended-only
- 将确认或暂缓的职业方向决定独立保存，不与 Recommendation 混在一起
- 对已确认的 Primary 和 Secondary 方向生成确定性的 Gap Analysis
- 支持 OpenAI-compatible Provider，包括阿里云百炼
- 原子 JSON 保存、确定性验证和隐私安全的诊断信息

还没实现：

- 实时岗位搜索和详细 JD Matching
- Evidence Bank、简历定制和自动申请

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
```

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
- **Phase 2D：** 确定性的角色级 Gap Analysis — 已完成
- **Phase 2E–2G：** 实时岗位、匹配、Evidence Bank 和端到端加固 — 尚未实现

## 开发验证

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```

工程历史请查看[脱敏后的分阶段摘要](docs/conversation-log.md)和[开发日志](docs/codex-log.md)。它们与 README 分开保存，让项目首页保持清爽。
