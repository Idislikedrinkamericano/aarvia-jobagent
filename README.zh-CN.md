# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个 **Career Navigation + Job Application Agent**。它先了解你的真实经历、兴趣、目标和限制，再讨论工作。

它不是简历老虎机。换一个 JD，不应该摇出一个全新的人。🎰🚫

## 当前状态

**版本 0.5.7 — Phase 1 已完成。**

- ✅ 结构化 Career Profile 与严格 JSON 存储
- ✅ 手动、自然语言和 Adaptive Follow-up CLI
- ✅ Candidate 确认、修正和证据检查
- ✅ Profile + Discovery State 事务保存
- ⏳ Resume Import 和 Phase 2 尚未实现

## 工作方式

```text
用正常语言介绍自己
→ 提取候选事实
→ 确认或修正
→ 只保存已确认信息
→ 每次追问一个真正缺失的问题
```

不猜日期，不编成果，不替你决定职业方向。Aarvia 对虚构内容保持一种令人安心的不耐烦。

## 快速开始

```bash
python -m pip install .

export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-model"
export AARVIA_LLM_BASE_URL="https://your-provider.example/compatible-mode/v1"

aarvia discover --narrative
```

使用 OpenAI 时可以不设置 `AARVIA_LLM_BASE_URL`。

其他使用方式：

```bash
# 较长的 UTF-8 文本，最大 2 MiB
aarvia discover --narrative-file background.txt \
  --profile data/profiles/example.json

# 继续补充已有 Profile
aarvia discover --follow-up \
  --profile data/profiles/example.json

# 不需要 LLM
aarvia discover --manual
```

Follow-up 命令：

- `.done` 提交多行回答；`.cancel` 重答当前问题。
- `:none`、`:skip`、`:decline` 分别记录不同的未回答状态。
- `:finish` 查看本轮修改；`q` 取消整个 Session。

Profile 已完整时，Aarvia 不调用 Provider，也不重写文件。知道什么时候什么都不做，也是一种能力。

## 百炼

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"
```

请替换 `{WorkspaceId}`。API Key、endpoint 和模型必须属于同一地域，模型也必须支持 Chat Completions JSON Schema Structured Outputs。并非所有百炼模型都支持。

## 安全第一

- Provider 输出会经过确定性清理和严格 Schema 验证。
- 提取结果在用户确认前只是临时 Candidate。
- Correction 会显示前后差异并拒绝没有证据的变化。
- 已有记录使用确定性 identity matching；新记录必须确认。
- Session Draft 只在最终确认后原子保存。
- Debug 只显示在终端，并会隐藏配置的 API Key。

`data/profiles/` 下的私人 Profile 和 API Key 都不应该提交到 Git。

## 产品流程

```text
User Profile → Career Discovery → Role Recommendation → User Decision
→ Gap Analysis → Evidence Bank → Base Resume → JD Matching
→ Minimal Tailoring → Fact Checking → Final Resume
```

目前只实现了 Profile 与 Career Discovery。Aarvia 没有躲在幕布后偷偷开始 Phase 2。

## 设计原则

- 用户方向不能只由当前简历决定。
- 用户保留职业方向的最终决定权。
- 简历内容必须有真实证据支持。
- Tailoring 应该最小化并可追踪。
- 复杂 Agent 框架只在真正需要时引入。
- 核心逻辑必须能脱离 LLM 独立测试。

## 开发验证

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```
