# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个 Career Navigation + Job Application Agent。它先理解“你是谁”，再讨论“你该投什么”：你真实做过什么、喜欢怎样的工作，以及求职中有哪些现实限制。

它不是简历老虎机。换一个 JD，不应该摇出一个全新的人。🎰🚫

## 现在能做什么

当前版本是 `0.4.7`，已经完成 Career Profile 与职业发现基础：

- 🧱 严格的教育、经历、技能、偏好和限制数据模型
- 💾 经过验证的 JSON 保存与加载
- 💬 手动问卷和自然语言终端访谈
- ✅ 任何信息进入正式 Profile 前都要经过 Candidate 确认
- 🔍 面向 compatible provider 的安全提取诊断
- ☁️ OpenAI Responses 与百炼 Chat Completions Structured Outputs
- 📄 适合长背景的 UTF-8 文本文件输入

Resume 导入、自适应追问、岗位推荐、Gap Analysis 和简历修改目前都**没有实现**。

## 快速开始 🚀

```bash
python -m pip install .
aarvia --version
```

配置 OpenAI-compatible provider：

```bash
export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-structured-output-model"
```

自定义 Provider 还需要 endpoint：

```bash
export AARVIA_LLM_BASE_URL="https://your-provider.example/compatible-mode/v1"
```

然后选择一种方式介绍自己。

### 在终端里直接说

```bash
aarvia discover --narrative
```

### 使用长文本文件

```bash
aarvia discover \
  --narrative-file background.txt \
  --profile data/profiles/example.json
```

文件必须是非空 UTF-8 纯文本，最大 2 MiB。本阶段不读取 PDF 或 DOCX。

### 使用手动备用问卷

```bash
aarvia discover --manual
```

输入 `:skip` 跳过当前问题，输入 `:quit` 保存进度并退出。

## 百炼配置 ☁️

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"
```

请替换自己的 `{WorkspaceId}`。API Key、endpoint 和模型必须属于同一地域；模型必须支持 Chat Completions JSON Schema Structured Outputs，Aarvia 不会假设所有百炼模型都支持。

Aarvia 根据解析后的 host 识别 `dashscope.aliyuncs.com` 和 `*.maas.aliyuncs.com`，对它们使用严格 `json_schema` 的 Chat Completions 并关闭 thinking。其他 endpoint 保持 Responses 协议。两条路径使用同一份 Candidate schema，并保持 `additionalProperties: false`。

## 你的回答会经历什么？🔐

```text
用户描述
-> Provider 提取
-> 确定性 cleanup 和 normalization
-> 严格 Candidate 验证
-> 用户确认
-> 正式 Career Profile
```

未知日期保存为 `null`，Aarvia 不会猜。只有五个正式日期字段中的精确格式占位符会被清理；其他非法日期仍会被拒绝。

`data/profiles/` 下的文件不会提交到 Git。请勿提交 API Key、Workspace ID、私人 endpoint 或个人 Profile JSON。

## Provider 出错时怎么查？🔦

```bash
aarvia discover --narrative --debug-extraction
```

失败时会显示版本、模型、endpoint host、协议、schema 模式、执行阶段、JSON 状态、验证原因和 Provider 原始文本。配置的 API Key 会被脱敏，原始输出不会保存。

原始输出可能包含个人信息，请只在私人终端使用 debug 模式。

## 产品路线

```text
User Profile -> Career Discovery -> Role Recommendation -> User Decision
-> Gap Analysis -> Evidence Bank -> Base Resume -> JD Matching
-> Minimal Tailoring -> Fact Checking -> Final Resume
```

当前状态：

- ✅ Phase 1A — Career Profile Foundation
- ✅ Phase 1B-v1 — Manual Career Discovery CLI
- ✅ Phase 1B-v2.1 — Natural-language Candidate and Confirmation workflow
- ⏳ Resume Import、自适应职业发现和 Phase 2

## 设计原则

- 用户方向不能只由当前简历决定。
- 用户保留职业方向的最终决定权。
- Profile 和简历内容必须有真实证据支持。
- 提取结果在确认前只是 Candidate。
- Tailoring 应该最小化并且可追踪。
- 复杂 Agent 框架只在真正需要时引入。
- 核心逻辑必须能脱离 LLM 独立测试。

## 开发验证 🧪

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```
