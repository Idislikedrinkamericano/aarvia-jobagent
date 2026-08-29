# Aarvia

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 的目标是先帮用户建立一份准确的 Career Profile，再进行职业方向和求职申请相关工作。Profile 保存三类信息：用户真实做过什么、想做什么，以及求职时有哪些现实限制。

Aarvia 不是一个“上传 JD，然后不看背景就重写整份简历”的工具。

## 现在已经能做什么

当前版本为 `0.4.3`，支持：

- 保存教育、经历、技能、职业偏好和求职限制。
- 严格验证 Profile，并保存和读取 JSON。
- 使用固定字段的终端问卷。
- 使用正常语言描述背景，由 OpenAI-compatible Responses API 提取候选信息。
- 先展示 Candidate，再由用户确认是否写入正式 Profile。
- 新信息与已有 Profile 冲突时，让用户明确选择。
- 使用同一套 OpenAI SDK 配置 OpenAI 或阿里云百炼。

目前还不能读取 Resume 文件，不能自动追问下一项最重要的信息，也不会推荐岗位。

## 快速开始

先以开发模式安装项目：

```bash
python -m pip install -e ".[dev]"
```

### 使用正常语言填写

配置模型服务，然后运行：

```bash
export AARVIA_LLM_API_KEY="your-api-key"
export AARVIA_LLM_MODEL="your-structured-output-model"

aarvia discover --narrative
```

Aarvia 会请你用一段正常语言介绍教育、经历、项目和技能，然后逐个主题展示提取结果：

- 输入 `y`：接受这个主题
- 输入 `n`：拒绝这个主题
- 输入 `e`：用一句话说明哪里需要纠正
- 输入 `q`：退出，不接受仍在等待确认的主题

模型提取的内容只是 Candidate。你没有确认之前，它不会进入正式 Career Profile。

### 使用阿里云百炼

百炼需要配置对应地域的 OpenAI-compatible endpoint：

```bash
export AARVIA_LLM_API_KEY="your-dashscope-api-key"
export AARVIA_LLM_BASE_URL="https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
export AARVIA_LLM_MODEL="qwen-plus"

aarvia discover --narrative
```

请把 `{WorkspaceId}` 替换为你自己的业务空间 ID。

需要注意：

- 北京、新加坡等地域的 API Key 和 endpoint 不能混用。
- 模型必须在同一地域已经开通，并且支持 Responses API。
- 不是所有百炼模型都支持 Responses API。
- 不要提交 API Key、Workspace ID、个人 endpoint 或 Profile JSON。

如果使用 OpenAI 默认 endpoint，可以不设置 `AARVIA_LLM_BASE_URL`。没有通用变量时，旧版 `OPENAI_API_KEY` 和 `AARVIA_OPENAI_MODEL` 仍然可以使用。

### 不使用模型，手动填写

固定字段问卷不需要 LLM：

```bash
aarvia discover
aarvia discover --manual
```

输入 `:skip` 跳过当前问题，输入 `:quit` 保存并退出。Manual 模式是备用方案，因此会看到更多底层 Profile 字段。

## Profile 保存在哪里

默认位置是：

```text
data/profiles/default.json
```

也可以指定其他路径：

```bash
aarvia discover --narrative --profile data/profiles/example.json
```

`data/profiles/` 下的文件已被 Git 忽略。不要提交个人职业信息。

## 自然语言信息如何进入 Profile

```text
用户描述
-> 模型提取
-> 确定性字段转换
-> Candidate 验证
-> 用户确认
-> 冲突处理
-> 正式 Career Profile
```

Responses API 的 JSON Schema 已要求 provider 返回正式的嵌套字段。不过部分 OpenAI-compatible provider 仍可能返回以下语义别名：

- `projects` -> `experience_overview` 中的 project 经历
- `target_roles` -> `career_preferences.currently_considered_roles`
- `target_locations` -> `constraints.target_locations`
- `target_employment_type` -> `constraints.employment_type_preference`

Aarvia 只转换这四个明确支持的别名。其他未知字段仍然会被拒绝。转换后的内容还必须通过原有 Career Profile 验证；Candidate 验证失败时，正式 Profile 不会被修改。

部分 compatible provider 还会返回完全没有内容的额外字段，例如 `"contact_info": {}`。只有当未知字段的值彻底为空时，Aarvia 才会清理它；嵌套 object 和 list 也会递归检查。只要未知字段包含任何真实内容，Aarvia 就会拒绝整个 Candidate，不会偷偷丢掉信息。

## 当前开发阶段

- **Phase 1A — Career Profile Foundation：已完成**
- **Phase 1B-v1 — Manual Career Discovery CLI：已完成**
- **Phase 1B-v2.1 — Candidate and Confirmation Workflow：已完成**
- **Phase 1B-v2.2 — Resume 文件导入：尚未实现**
- **Phase 1B-v2.3 — Adaptive follow-up questions：尚未实现**

完整产品流程计划为：

```text
User Profile
-> Career Discovery
-> Role Recommendation
-> User Decision
-> Gap Analysis
-> Evidence Bank
-> Base Resume
-> JD Matching
-> Minimal Tailoring
-> Fact Checking
-> Final Resume
```

目前只完成了 User Profile 和 Career Discovery 的基础部分。

## 设计原则

- 用户的职业方向不能只由当前简历决定。
- 用户保留最终决定权。
- Profile 和简历内容必须有真实信息支持。
- 模型提取的内容在用户确认前只是 Candidate。
- 未知信息和冲突信息不能被静默接受。
- 核心验证和合并逻辑必须能够脱离 LLM 独立测试。

## Python API（可选）

普通用户只需要运行 `aarvia discover`，不需要自己写下面的 Python 代码。这一节只提供给需要直接调用 Aarvia 的开发者。

下面故意创建一份尚未填写完整的 Profile。`create_profile()` 会验证已经提供的信息，并把仍然缺少的内容放进 `open_questions`。`save_profile()` 负责保存 JSON，`load_profile()` 会重新读取并再次验证。

```python
from aarvia import create_profile, load_profile, save_profile

profile_data = {
    "basic_profile": {
        "current_location": "Shanghai",
        "current_status": "Graduate student",
    },
    "skills": [
        {"skill_name": "Python", "category": "programming language"}
    ],
}

profile = create_profile(profile_data)
print(profile.open_questions)

profile_path = "data/profiles/example.json"
save_profile(profile, profile_path)

loaded_profile = load_profile(profile_path)
assert loaded_profile == profile
```
