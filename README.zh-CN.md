# Aarvia 🧭

[English](README.md) | [中文](README.zh-CN.md)

Aarvia 是一个 **Career Navigation + Job Application Agent**。它先了解你的真实经历、兴趣、目标和限制，再讨论工作。

它不是简历老虎机。换一个 JD，不应该摇出一个全新的人。🎰🚫

## 当前状态

**版本 0.9.0 — 可复用人工审核的证据感知推荐。**

- ✅ Phase 1：经过确认的 Career Profile 与 Adaptive Career Discovery
- ✅ Phase 2A：版本化 Role Catalog 与共享数据契约
- ✅ 八个稳定 Role Family，以及 specialization 和搜索标题 alias
- ✅ Phase 2A recommendation、decision、gap 与 job artifact 的原子 JSON 持久化
- ✅ 面向美国 early-career 证据的版本化逻辑来源与不可变 JD capture
- ✅ Tier A/B/C provenance、canonical dedup、人工 curation 与 prevalence 基础
- ✅ Requirement Candidate 的 approve、reject、revise、split 可机读 lineage
- ✅ 每个 canonical job 的 Role Family 以人工确认的 Role Assignment 为唯一权威来源
- ✅ 非递归 `all_of` / `any_of` Candidate 逻辑与确定性人工审核 provenance
- ✅ Candidate 独立审核、受控 Cluster 审核与明确的 Logic Group resolution
- ✅ Capability Rubric schema 2：20 个 Dimension、稳定 criterion ID 与强类型 Evidence Support Policy
- ✅ 确定性 Current Fit、Directional Fit、约束、置信度、并列与追问
- ✅ Mapping schema 4 证据分配、Recommendation schema 5 评分与强类型证据审核 provenance
- ✅ 百炼/custom JSON mode、有限 repair retry 与显式启用的纯元数据诊断
- ⏳ User Decision、Gap Analysis、真实岗位发现和简历链路尚未实现

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

# 首次推荐：同时保存 Mapping 3 与 Recommendation 4
aarvia recommend --profile data/profiles/example.json \
  --mapping-output mapping.json \
  --output recommendation.json

# 审核 provisional 经历/项目证据，不调用 Provider
aarvia review-evidence --profile data/profiles/example.json \
  --mapping mapping.json \
  --output evidence-review.json

# 使用同一 Mapping 和已保存 Review 重算；不会调用 Provider
aarvia recommend --profile data/profiles/example.json \
  --mapping-artifact mapping.json \
  --review-artifact evidence-review.json \
  --output reviewed-recommendation.json

# 显式 legacy 离线流程（仅 Mapping schema 1/2）
aarvia recommend --profile data/profiles/example.json \
  --mapping-candidates mapping-candidates.json \
  --output legacy-recommendation.json
```

不提供 Mapping 输入时，`aarvia recommend` 会使用已配置的 OpenAI-compatible Provider 仅提出 criterion binding，然后原子保存 Mapping schema 4 与 Recommendation schema 5。Python 验证引用、把一个 evidence span 最多分配给一个 primary 和一个受限 secondary Dimension，并计算所有状态、分数、等级、置信度、并列和排名。`--mapping-artifact` 始终复用已有 Mapping，不调用 Provider。Review 只对创建它时的精确 Profile、Rubric、Mapping 和 binding identity 有效。

Provider 诊断必须显式开启，而且只保存元数据：

```bash
aarvia recommend --profile data/profiles/example.json \
  --provider-diagnostics-dir local_data/provider_diagnostics
```

这些文件可能描述由私人 Profile 引发的错误，请保存在已忽略的 `local_data/` 下。Aarvia 只记录 hash、长度、协议、JSON mode、parser error 与 fallback reason；不会保存 Provider raw response、API Key、请求 header 或环境变量。

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

请替换 `{WorkspaceId}`。API Key、endpoint 和模型必须属于同一地域。Recommendation mapping 使用 Chat Completions JSON object mode 与严格 schema prompt。若 compatible endpoint 明确拒绝 JSON mode，Aarvia 会记录原因，并仅对该请求降级到严格 prompt-only 模式。Malformed JSON 不会在本地被猜测修复；系统只会携带完整任务重试一次，然后安全失败。

Profile reference 只能从确定性生成的 canonical leaf path 清单中选择。请求里的 `career_profile` 只是 transport envelope；Aarvia 最多移除一个误加的 `career_profile.` 前缀，之后仍会重新严格验证 path、value snapshot 和 Profile fingerprint。其他路径 alias 一律不接受。

用户填写的目标职位名称只是偏好，不是 Rubric 标识。Provider 的每个 `role_id` 都被限制为当前 Rubric 的 canonical enum；未知值只会触发一次明确的完整响应重试，之后被隔离，不做模糊或自动映射。

单条错误 Candidate 不再拖垮整份可用响应。Aarvia 会携带精确规则重试一次；第二次仍不合法时，只隔离该 Candidate 并记录结构化 reason code。受影响能力保持 `unknown`，不会被改写成证据，也不会被静默搬进 Directional Fit。只要发生 rejection，推荐置信度就不能是 High；rejection 比例达到 50%，或影响 ready scoring dimension 时，最高只能是 Low；全部 Candidate 被拒绝时，结果为 Insufficient，并带有 `provider_mapping_insufficient` blocker。这些阈值都集中在确定性 Python 常量中。

第二次响应不会因为“来得更晚”就自动覆盖第一次。Aarvia 会独立验证两份完整响应；只有第二次同时严格降低 rejection 数量和比例、保留首次已接受 Candidate、canonical Role 与 Current Fit Dimension，并且不增加 Candidate 总量时，才会采用第二次。持平或退化时保留第一次，两次响应绝不合并；诊断只记录最终 attempt 与无敏感信息的选择原因。

Atomic evidence 校验会在源头产生结构化代码，区分 excerpt 错误、token boundary、重复或重叠证据、跨维度复用，以及非法的 status/inference 关系；分类不依赖人类可读异常文案。诊断和持久化 warning 只保存类别与安全标识，不保存 excerpt 或 Profile 值。

同一结构化边界也覆盖 Current Fit 字段类型、evidence strength、Provider confidence、review flag、contribution relationship、确定性 provenance、重复 mapping 与集合级 contribution cap。只有真正未知的旧异常才使用通用拒绝类别。

Current Fit 使用字段内的原子证据。Mapping schema 4 仍只允许 Provider 提出 canonical Role、Dimension、criterion、span、binding type 和 confidence。Python 物化 span、应用 Rubric policy，并以稳定规则分配跨 Dimension 复用：一个 primary 最多贡献 `1.0`，一个 secondary 最多贡献 `0.3`；无法确定 primary 时整组拒绝并要求修正。Recommendation schema 5 只消费这些分配结果，并在审核拒绝 primary 后对已持久化的剩余成员重新分配。审核仍不自动等于 `demonstrated`，结构证据也不会因审核变成语义证据。旧 Mapping 1–3 与 Recommendation 1–4 继续显式可读。

## 安全第一

- Provider 输出会经过确定性清理和严格 Schema 验证。
- Provider 的 Profile reference 必须使用真实 canonical leaf path 和精确 Profile 值。
- 结构错误会使整份响应失败；单条语义错误则被隔离、可审计且绝不参与评分。
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
- **2B — 市场证据与 Curation：** source、capture、assignment、candidate、logic 和 review 基础已完成。
- **2C — Role Recommendation：** 已支持 Applied AI Engineer、Machine Learning Engineer 和 Research Engineer；Current Fit 与 Directional Fit 始终分开。
- **2D–2G：** User Decision、Role-level Gap Analysis、真实岗位流程、匹配与端到端加固仍是后续工作。

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

打包的 Capability Rubric 与 Production Role Catalog 是两个独立对象。Rubric 为推荐提供经过审查的聚合能力维度；Catalog 仍故意保持 0 条已发布 requirement 和 0 个 source。

### Phase 2B-1 Source Foundation

- 首版市场限定为美国 internship、new-grad 和明确经验要求不超过两年的 early-career 岗位。
- Tier A 是公司官方或 ATS 来源。Tier B 是经过验证的招聘平台职位；LinkedIn 是平台来源，不是公司官网。Tier C 只用于发现。
- 官方 verified open 与平台 verified open 是不同状态。普通 careers 首页和未完成强验证的来源最多只能支持 `possibly_open`。
- Live Job schema 2 和 3 都将申请链接“存在”与验证分开；schema 3 还将验证绑定到不可变 capture。URL 格式正确本身不代表可投递。
- LLM 只能创建未批准的 `RequirementCandidate`。Python 负责 ID、hash、exact dedup、来源比例和 prevalence；人工负责 normalization、importance、证据与发布审批。
- Source schema 3 将稳定职位身份与不可变 capture 分开。capture scope 区分状态检查、局部摘录、完整 JD 和 legacy 证据；只有经过验证的完整 JD 才能支持未来的 `not_stated` 判断。
- Live Job schema 3 将 listing 与申请链接验证分别绑定到明确 capture。新增 capture 不会自动改写旧 Job artifact。
- Curation schema 4 要求每个 Candidate 明确引用 source、capture、hash 和 evidence locator。revise/split successor 必须继承 capture，除非未来另行设计显式 rebase。
- Source v2、Live Job v1/v2 与 Curation v2/v3 继续显式可读。迁移必须主动调用，不会虚构完整 capture；hash 匹配不唯一时会安全阻断。
- Role Assignment schema 1 记录人工确认的初始映射与重新分类，包含精确 capture 证据、确定性 lineage、审核人、时间和理由。
- Live Job schema 4 与 Curation schema 5 仅将兼容 Role 字段作为 current Assignment 的受验证 projection；旧 schema 仍显式可读。
- Reclassification 会整体更新 Assignment、Live Job 与 Curation。仅未审核 Candidate 和单岗位 proposed Cluster 可安全自动处理；已有审核、lineage、confirmed/rejected Cluster 或 mixed-job Cluster 会结构化阻断。
- Curation schema 6 可以把同一来源 clause 保存为一组非递归 `all_of` 或 `any_of` 原子 Candidate。Python 负责稳定 ID、验证和真值聚合；Provider 只能提出逻辑；人工负责确认或拒绝。
- Curation schema 7 将 Candidate 事实审核与 Cluster 归并分开。Proposed Cluster 只能通过受控 API 创建、分配、移除、合并和拆分；确认或拒绝会生成绑定精确语义快照的确定性人工 Review。
- Logic Group 现在支持六种明确结果：confirm、quarantine、reject members、release members、revise 和 split。当前有效绑定由已审核 lineage 确定，released 或 superseded 历史不会伪装成当前逻辑。
- Catalog draft 只消费 schema 7 中位于 current confirmed Cluster、且与 confirmed Assignment 一致的 approved leaf。未聚类批准项和未解决 Logic Group 会被阻断；confirmed logic 仍返回 `production_requirement_logic_contract_required`，直到存在 Production Requirement Logic schema。
- Builder 会独立聚合 Logic Group blocker；即使 Cluster 尚未确认，也不会遮蔽 group 层面的安全诊断。
- 同一 canonical job 不能进入两个 Role Family，多个 capture、split successor 或 logic branch 也不能扩大公司样本数。
- 完整 JD 与 Pilot artifact 只能保存在被忽略的 `local_data/`；本次契约修复不会修改它们。
- 真实 Pilot 中待重新分类的岗位尚未迁移；Clause Coverage 与样本计数只能在另行批准的本地迁移后重新生成。
- 真实 schema 6 Candidate Completion artifact 仍仅在本地且未被修改；它尚未迁移到 schema 7，没有改变任何审核状态，也没有计算正式 prevalence。
- Production Requirement Logic、prevalence 发布、User Decision 与 Gap Analysis 仍明确未实现。
- Capability Rubric schema 2 为每条 inclusion criterion 分配稳定 ID，并按 Dimension 声明 evidence class、保守 status cap、confirmed evidence 阈值与行为证据规则。Schema 1 继续显式可读并保持原格式 round-trip。
- `0.9.0` 默认使用 Mapping schema 4 与 Recommendation schema 5，保存可复用的确定性分配 provenance，并提供独立的 `review-evidence` 命令。User Decision、Gap Analysis 与 Phase 2D 尚未实现。
- Production Catalog 仍是 `1.0.0`：8 个 role、0 条 requirement、0 个 source。Rubric policy contract 不代表已经发布任何市场 requirement。

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
