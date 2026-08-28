# Aarvia

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个 Career Navigation + Job Application Agent。它先理解用户的背景、兴趣、限制和职业目标，再支持基于真实事实的求职申请。

Aarvia 不是“上传 JD 后让 LLM 重写整份简历”的工具。它把职业探索、事实收集、岗位方向的 Base Resume、JD 匹配、最小化修改和事实核查拆分为可追踪的流程。

## Aarvia 解决的问题

职业方向不能只由用户当前的简历决定。用户可能尚未明确目标岗位，相关经历可能分散在不同项目中，也可能需要先了解能力差距。对每个 JD 重写整份简历还会制造不必要的修改，并增加无事实支持内容的风险。

Aarvia 从用户本身出发，保存结构化职业信息，并在信息不足时明确提出问题，而不是自行猜测答案。

## 产品流程

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

## 设计原则

- 用户的职业方向不能只由当前简历决定。
- 用户对职业方向保留最终决定权。
- 简历中的陈述必须有真实事实依据。
- 针对 JD 的修改应当最小化且可追踪。
- 只在真正需要时引入复杂 Agent 框架。
- 核心逻辑必须能够脱离 LLM 独立测试。

## 当前状态

- **Phase 1A — Career Profile Foundation 已完成。**
- **Phase 1B-v1 — Interactive Career Discovery CLI 已完成。**
- **Phase 1B-v2 — Natural-language discovery 尚未实现。**

Phase 1A 提供结构化、可验证、可保存为 JSON 的 Career Profile。Phase 1B-v1 新增确定性的终端问卷，可以创建新 Profile 或继续填写已有 Profile；每个回答都通过 Phase 1A 数据模型验证，并在有效回答后及时保存。尚未完成的多字段记录会保存在 Profile 同目录的私有访谈草稿中，正式 Career Profile 始终保持有效。

尚未实现 Role Recommendation、User Decision、Gap Analysis、Evidence Bank、Base Resume、JD Matching、Minimal Tailoring、Fact Checking 和 Final Resume。

## 交互式 Career Discovery

以开发模式安装本地项目，然后创建或继续默认 Profile：

```bash
python -m pip install -e ".[dev]"
aarvia discover
```

默认保存位置为 `data/profiles/default.json`。可以指定其他路径：

```bash
aarvia discover --profile data/profiles/example.json
```

列表问题使用英文逗号分隔。输入 `:skip` 跳过当前问题；输入 `:quit` 保存进度并退出。再次运行时会保留已有字段，并从缺失信息继续。

`data/profiles/` 下的 Profile JSON 和访谈草稿已被 Git 忽略。不要提交私人职业数据。

## Python API 最简示例

在仓库根目录运行，并将 `src` layout 加入 Python 路径：

```bash
PYTHONPATH=src python - <<'PY'
from aarvia import create_profile, load_profile, save_profile

profile = create_profile({
    "basic_profile": {
        "name": "Lin",
        "current_location": "Shanghai",
        "current_status": "Graduate student",
    },
    "skills": [
        {"skill_name": "Python", "category": "programming language"}
    ],
})

print(profile.open_questions)
save_profile(profile, "data/career-profile.json")
assert load_profile("data/career-profile.json") == profile
PY
```

示例路径仅用于演示。实际应用应为每位用户选择合适的保存位置，并且不要将私人职业数据提交到版本库。
