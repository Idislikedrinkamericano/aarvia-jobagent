# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个 **Career Navigation + Job Application Agent**。它先了解你的真实经历、兴趣、目标和限制，再讨论工作。

它不是简历老虎机。换一个 JD，不应该摇出一个全新的人。🎰🚫

## 当前状态

**版本 0.6.3 — Phase 2B-1 curation lineage 契约已完成。**

- ✅ Phase 1：经过确认的 Career Profile 与 Adaptive Career Discovery
- ✅ Phase 2A：版本化 Role Catalog 与共享数据契约
- ✅ 八个稳定 Role Family，以及 specialization 和搜索标题 alias
- ✅ Phase 2A recommendation、decision、gap 与 job artifact 的原子 JSON 持久化
- ✅ 美国 internship、new-grad 与 0-2 年 early-career JD 的 Schema 2 source contract
- ✅ Tier A/B/C provenance、canonical dedup、人工 curation 与 prevalence 基础
- ✅ Requirement Candidate 的 approve、reject、revise、split 可机读 lineage
- ⏳ 有来源的 Role Requirements、推荐、Gap 和真实岗位尚未实现

当前 production Catalog 故意保持 **0 条 requirement、0 个 source**。它是一套带护栏的 taxonomy，不是一件塞满虚构就业市场知识的风衣。🕵️

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
Career Profile → Career Discovery → Role Recommendation → Live Job Examples
→ User Decision → Role-level Gap Analysis → Evidence Bank → Base Resume
→ Live Job Discovery → Detailed JD Matching
→ Minimal Tailoring → Fact Checking → Final Resume
```

## Phase 2 地图

- **2A — 数据契约：** 已完成。Role taxonomy、provenance、recommendation、decision、gap 和 live-job schema 均可严格验证与序列化。
- **2B — Role Recommendation：** 2B-1 source/curation foundation 已完成；推荐排名尚未实现。
- **2C — User Decision：** 未实现。推荐结果和用户确认决定始终是两个独立对象。
- **2D — Role-level Gap Analysis：** 未实现。契约明确保留 `unknown != missing`。
- **2E — Live Job Discovery：** 未实现。官方页面和职位状态将在这里验证。
- **2F — Preliminary Job Matching：** 未实现。它描述 JD requirement coverage，不代表面试或 offer 概率。
- **2G — 端到端加固：** 未实现。

**Role Fit 与 Job Fit 不是一回事。** 某个 Role Family 可以是合理方向，但某条具体职位仍可能存在资格冲突。详细 JD Matching 和简历工作依然位于 Evidence Bank 与 Base Resume 之后。

## Phase 2A 安全边界

- 每条 production requirement 必须引用已知 provenance source。
- `1.0.0` production taxonomy 从打包的 `catalog_data/role-catalog-1.0.0.json` 加载，不在 Python 中重复维护。
- 没有来源的 requirement 不得宣称 prevalence 为 `common` 或 `frequent`。
- 测试 fixture source 不得进入 production Catalog 或岗位集合。
- Recommendation 只能引用真实存在的 Role、Requirement 和已确认 Profile fact。
- 引用 RecommendationSet 的 Decision 必须使用同一 Catalog 和同一份 CareerProfile 快照完成验证。
- 每次保存或加载 Gap Analysis 都必须提供 confirmed User Decision，且只能分析其中的 Primary 或 Secondary 方向。
- Profile reference 保存字段路径、精确值快照和确定性 Profile fingerprint。
- 列表路径目前使用下标，因此引用只属于一个精确 Profile 快照。稳定的本地 entry ID 需要未来单独迁移，Provider 永远不能生成它。
- `verified_open` 必须由具体官方职位页面支持；普通 careers 首页最多支持 `possibly_open`。
- application URL 的“已提供”和“已验证”是两种状态；验证另有状态、时间和来源引用。
- Live Job 必须具备官方来源契约，但 Phase 2A 不搜索或验证任何真实岗位。

Phase 2B 开始排名前，Aarvia 仍需建立一份经过审查、由真实官方职位来源支持的 Role Requirements 数据集。空 requirements 不等于市场证据。

### Phase 2B-1 Source Foundation

- 首版市场限定为美国 internship、new-grad 和明确经验要求不超过两年的 early-career 岗位。
- Tier A 是公司官方或 ATS 来源。Tier B 是经过验证的招聘平台职位；LinkedIn 是平台来源，不是公司官网。Tier C 只用于发现。
- 官方 verified open 与平台 verified open 是不同状态。普通 careers 首页和未完成强验证的来源最多只能支持 `possibly_open`。
- Live Job schema 2 将申请链接“存在”与验证状态、验证时间和验证来源分开保存。只有具体且合格的职位来源才能证明申请链接已验证；URL 格式正确本身不代表可投递。
- LLM 只能创建未批准的 `RequirementCandidate`。Python 负责 ID、hash、exact dedup、来源比例和 prevalence；人工负责 normalization、importance、证据与发布审批。
- Curation schema 3 使用不可变人工审核记录保存 lineage。revise 与 split 后的 Candidate 保留确定性的父子关系、source hash、证据范围和 Role mapping。schema 2 继续显式可读并可原样 round-trip，但不能通过 Catalog builder 发布。
- Catalog draft 只消费 approved leaf Candidate；同一家公司的一条证据被拆分后仍只计一个公司样本。
- 完整 JD 与 Pilot artifact 只能保存在被忽略的 `local_data/`；本次契约修复不会修改它们。
- Production Catalog 仍是 `1.0.0`：8 个 role、0 条 requirement、0 个 source。包含推荐用户能力的完整 Phase 2B 才会升级为项目版本 `0.7.0`。

## 设计原则

- 用户方向不能只由当前简历决定。
- 用户保留职业方向的最终决定权。
- 简历内容必须有真实证据支持。
- Tailoring 应该最小化并可追踪。
- 复杂 Agent 框架只在真正需要时引入。
- 核心逻辑必须能脱离 LLM 独立测试。

## 开发验证

项目记录：[按 Phase 拆分的公开脱敏对话摘要](docs/conversation-log.md)和[开发日志](docs/codex-log.md)。包含个人信息的逐字原文不会由 Git 跟踪。

```bash
python -m pip install ".[dev]"
python -m pytest
git diff --check
```
