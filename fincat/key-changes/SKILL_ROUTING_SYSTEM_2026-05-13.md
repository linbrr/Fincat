# Skill 四层前置检索路由系统 + Topic 生成推送机制改造（2026-05-13）

> 生成日期：2026-05-13
> 关联文件：skill_router.py, context.py, loop.py, skill_manage.py, skill_candidates.md, dream_phase3_memory.md, topic/, pattern_miner.py, event_topic_bridge.py

---

## 目录

**第一章：Skill 四层前置检索路由系统**

1. [背景与问题](#1-背景与问题)
2. [整体架构](#2-整体架构)
3. [四层检索详解](#3-四层检索详解)
4. [SkillVectorIndex 向量索引](#4-skillvectorindex-向量索引)
5. [SkillRouter 主类](#5-skillrouter-主类)
6. [ContextBuilder 集成](#6-contextbuilder-集成)
7. [SkillManageTool 变更回调](#7-skillmanagetool-变更回调)
8. [Dream Phase 3：memory.md LLM 总结](#8-dream-phase-3memorymd-llm-总结)
9. [性能预算](#9-性能预算)
10. [实施阶段](#10-实施阶段)
11. [数据流全景图](#11-数据流全景图)

**第二章：Topic 生成推送机制改造**

12. [背景与问题](#12-背景与问题)
13. [改造方案](#13-改造方案)
14. [数据流全景图（改造后）](#14-数据流全景图改造后)
15. [实施阶段](#15-实施阶段)

---

## 1. 背景与问题

### 1.1 原有方案

改造前，fincat 的技能选择完全依赖 LLM：

```
用户消息到达
    ↓
ContextBuilder.build_system_prompt()
    ↓
加载 ALL 技能摘要到 system prompt（name + description + quality_score）
    ↓
LLM 读摘要 → 自行判断用哪个 → read_file 加载全文
```

每轮对话都把所有 15+ 技能的摘要注入 system prompt，由 LLM 自行判断用哪个。

### 1.2 存在的问题

| 问题 | 影响 |
|------|------|
| **Token 浪费** | 无关技能的摘要每轮占 500-1000 tokens，15 个技能全部注入 |
| **LLM 误判** | 技能越多，选择准确率越低，容易选错或遗漏 |
| **无法利用已有数据** | `SkillUsageTracker` 的质量分数、向量相似度等信号未被用于技能选择 |
| **无语义匹配** | 纯靠 LLM 理解 description 文本，没有向量相似度辅助 |

### 1.3 改造目标

实现四层前置检索（规则匹配 → 语义向量 → 行为过滤 → 评分排序），将候选从 15+ 缩小到 1-3 个，再交给 LLM 做最终决策。

预期效果：
- 减少 30-60% 技能相关 token 消耗
- 提升技能选择准确率
- 复用已有基础设施（EmbeddingEngine、SkillUsageTracker、QueryVectorCache）

---

## 2. 整体架构

### 2.1 改造后的流程

```
用户消息到达
    ↓
MemoryRetrievalFilter（是否需要记忆）
    ↓
SkillRouter.route()                    ← 新增
    ↓
    Layer 1: 规则匹配（关键词、斜杠命令）
    Layer 2: 语义向量检索（EmbeddingEngine + FAISS）
    Layer 3: 行为过滤（stale、activate_when）
    Layer 4: 综合评分（相似度×40% + 规则×25% + 质量×20% + 近因×15%）
    ↓
返回 1-3 个 SkillCandidate
    ↓
ContextBuilder.build_system_prompt()
    ↓
    有候选 → 只加载 1-3 个技能完整内容
    无候选 → fallback 到全量摘要（原有行为）
    ↓
LLM 只需在 1-3 个候选中做最终决策
```

### 2.2 新增文件

| 文件 | 用途 |
|------|------|
| `fincat/agent/skill_router.py` | 核心路由模块：`SkillVectorIndex` + `SkillRouter` |
| `fincat/templates/agent/skill_candidates.md` | LLM 决策 prompt 模板 |
| `fincat/templates/agent/dream_phase3_memory.md` | Dream Phase 3 memory.md 总结模板 |

### 2.3 修改文件

| 文件 | 改动 |
|------|------|
| `fincat/agent/loop.py` | 初始化 `SkillRouter`，在 `_process_message` 中调用 `route()` |
| `fincat/agent/context.py` | `build_system_prompt` 支持 `skill_routing` 参数，条件化技能加载 |
| `fincat/agent/tools/skill_manage.py` | 增加 `on_skill_change` 回调，create/patch/delete 后更新向量索引 |
| `fincat/agent/memory.py` | Dream Phase 2 后新增 Phase 3 memory.md LLM 总结 |

### 2.4 不需要修改的文件

| 文件 | 原因 |
|------|------|
| `skill_tracker.py` | 质量分接口已就绪，直接调用 `get_quality_score()` |
| `skill_evolver.py` | 被动受益，无需改动 |
| `skill_lifecycle_manager.py` | 仅用于后台维护，不参与实时路由 |
| `embedding.py` | 作为依赖直接复用 |
| `category_vector_index.py` | 仅作模式参考 |
| `query_cache.py` | 直接复用（key 加 `"skill:"` 前缀避免冲突） |
| `command/router.py` | 斜杠命令系统独立，不涉及 |

---

## 3. 四层检索详解

### 3.1 Layer 1：规则匹配

**目的：** 零成本快速命中明确的技能请求。

**匹配方式：**

| 匹配类型 | 示例 | 得分 |
|----------|------|------|
| 斜杠命令 | `/stock-analysis 600519` | 1.0 |
| 关键词匹配 | 用户说"分析股票"，description 包含"分析股票" | 0.8 |

**实现细节：**

```python
def _layer1_rule_match(self, user_message, available):
    hits = {}
    msg_lower = user_message.lower().strip()

    for entry in available:
        name = entry["name"]

        # 斜杠命令匹配
        if msg_lower.startswith(f"/{name}"):
            hits[name] = 1.0
            continue

        # 关键词匹配：从 description 提取引号内的触发短语
        desc = self._loader._get_skill_description(name)
        triggers = re.findall(r'["""\']([^"""\']+)["""\']', desc)
        for trigger in triggers:
            if trigger.lower() in msg_lower:
                hits[name] = max(hits.get(name, 0.0), 0.8)
                break

    return hits
```

**触发短语来源：** 技能 description 中引号内的中文短语。例如 stock-analysis 的 description 包含 `"分析股票"`、`"帮我看看"`、`"技术分析"`，这些会被提取为触发关键词。

### 3.2 Layer 2：语义向量检索

**目的：** 通过向量相似度找到语义最相关的技能。

**流程：**

```
用户消息
    ↓
QueryPreprocessor.preprocess()  → 清洗、去停用词、同义词替换
    ↓
QueryVectorCache.get()          → 缓存命中？直接返回向量
    ↓ (缓存未命中)
EmbeddingEngine.embed()         → bge-small-zh-v1.5, 512维
    ↓
QueryVectorCache.put()          → 写入缓存
    ↓
SkillVectorIndex.search()       → 余弦相似度暴力搜索 top-5（阈值≥0.7）
```

**关键设计：**

- **复用 QueryVectorCache**：skill 查询的向量缓存与 memory 查询共享同一缓存实例，key 加 `"skill:"` 前缀避免冲突
- **复用 QueryPreprocessor**：同一个预处理器实例，清洗逻辑一致
- **暴力搜索**：15 个技能不需要 FAISS，numpy 余弦相似度 < 5ms

### 3.3 Layer 3：行为过滤

**目的：** 排除不适用的技能。

**过滤规则：**

| 规则 | 条件 | 处理 |
|------|------|------|
| Stale 过滤 | `SkillUsageTracker.is_stale = True`（30天未用） | 排除，除非 Layer 1 命中 |
| `activate_when` | `tool_available()`、`time_range()`、`env()` 不满足 | 排除 |
| `requires` | 缺少依赖的 bin 或 env 变量 | 排除 |
| `disabled` | 用户禁用的技能 | 排除 |

**实现：** 复用 `SkillsLoader.list_skills(filter_unavailable=True)`，该方法已实现所有过滤逻辑。

### 3.4 Layer 4：综合评分与排序

**目的：** 综合多维度信号，选出 top-3 候选。

**评分公式：**

```
score = 0.40 × similarity          # Layer 2 语义相似度
      + 0.25 × rule_match          # Layer 1 规则命中（1.0 或 0.0）
      + 0.20 × quality_score       # SkillUsageTracker 质量分
      + 0.15 × session_recency     # 会话内近因（最近 5 轮用过的技能）
```

**各维度说明：**

| 维度 | 权重 | 来源 | 说明 |
|------|------|------|------|
| 语义相似度 | 40% | `SkillVectorIndex.search()` | 向量余弦相似度，0-1 |
| 规则命中 | 25% | Layer 1 结果 | 命中=1.0，未命中=0.0 |
| 质量分 | 20% | `SkillUsageTracker.get_quality_score()` | 加权：调用次数25% + 最近使用25% + 成功率30% + 平均质量20% |
| 会话近因 | 15% | `SkillRouter._session_history` | 最近 5 轮用过的技能得 1.0 |

**阈值与 fallback：**
- 最低得分阈值：0.3
- 最终候选数：top-3
- 0 候选时：fallback 到当前全量摘要行为

---

## 4. SkillVectorIndex 向量索引

### 4.1 设计思路

仿照 `CategoryVectorIndex` 的模式，但增加 JSON 持久化。

**存储路径：** `~/.fincat/workspace/skills/.vector_cache.json`

**存储格式：**

```json
{
  "stock-analysis": {
    "vector": [0.123, 0.456, "..."],
    "hash": "sha256:a1b2c3d4e5f6"
  },
  "earnings-analysis": {
    "vector": [0.321, 0.654, "..."],
    "hash": "sha256:f6e5d4c3b2a1"
  }
}
```

### 4.2 生命周期

```
启动
    ↓
加载 .vector_cache.json 到内存
    ↓
扫描所有技能目录
    ↓
对比 content_hash：
    ├─ 匹配 → 复用缓存向量（0ms embed）
    └─ 不匹配/缺失 → 加入待 embed 列表
    ↓
embed_batch(待 embed 列表)  → ~50-100ms
    ↓
写回 .vector_cache.json
    ↓
运行时检索：纯内存余弦相似度，不读磁盘
    ↓
技能变更（create/patch/delete）：
    ├─ 单个变更 → upsert/remove 单条向量 + 写回 JSON
    └─ 批量变更 → invalidate() 全量重建
```

### 4.3 关键方法

```python
class SkillVectorIndex:
    def build(skills, loader)        # 启动时调用，加载缓存 + 补算缺失 + 写回
    def search(query_vec, top_k=5, threshold=0.7)  # 余弦相似度暴力搜索
    def upsert(skill_name, content)  # 新增/更新单个技能向量（内存+磁盘）
    def remove(skill_name)           # 删除单个技能向量（内存+磁盘）
    def invalidate()                 # 全量重建（fallback 用）
```

### 4.4 向量生成文本

每个技能的 embedding 文本由以下部分拼接：

```python
text = f"{name} {description} {' '.join(triggers)}".lower()
```

- `name`：技能名称（如 `stock-analysis`）
- `description`：frontmatter 中的描述字段
- `triggers`：description 中引号内的触发短语

参考 `SkillLifecycleManager._extract_semantic_text()` 的提取逻辑。

---

## 5. SkillRouter 主类

### 5.1 初始化

```python
class SkillRouter:
    def __init__(
        self,
        skills_loader: SkillsLoader,      # 技能加载器
        embedding: EmbeddingEngine,       # 向量引擎
        usage_tracker: SkillUsageTracker, # 质量追踪
        query_cache: QueryVectorCache,    # 查询向量缓存
        query_preprocessor: QueryPreprocessor,  # 查询预处理器
    )
```

在 `AgentLoop.__init__()` 中初始化（loop.py ~line 458）：

```python
self._skill_router = SkillRouter(
    skills_loader=self.context.skills,
    embedding=self._embedding,
    usage_tracker=self._usage_tracker,
    query_cache=self._query_cache,
    query_preprocessor=self._query_preprocessor,
)
# Wire skill change callback
self._skill_manage.on_skill_change = self._skill_router.invalidate
```

### 5.2 route() 方法

```python
def route(self, user_message, session_key="", channel="") -> SkillRoutingResult:
    self.ensure_index()               # 懒加载向量索引
    always_skills = ...               # 绕过路由的 always:true 技能
    available = ...                   # 过滤后的可用技能

    layer1_hits = self._layer1_rule_match(user_message, available)
    semantic_candidates = self._layer2_semantic_search(user_message)
    filtered = self._layer3_filter(semantic_candidates, layer1_hits, available)
    candidates = self._layer4_score_and_rank(filtered, layer1_hits, session_key, available)

    # 更新会话历史
    if candidates and session_key:
        self._session_history[session_key].append(candidates[0].name)

    return SkillRoutingResult(always_skills, layer1_hits, candidates, available_names)
```

### 5.3 返回值

```python
@dataclass
class SkillRoutingResult:
    always_skills: list[str]           # always:true 技能（绕过路由）
    layer1_matches: list[str]          # 规则命中的技能名
    candidates: list[SkillCandidate]   # 最终 top-3 候选
    all_available: list[str]           # 所有可用技能名（fallback 用）

@dataclass
class SkillCandidate:
    name: str           # 技能名
    score: float        # 综合得分 (0-1)
    source: str         # "rule" | "semantic" | "combined"
    similarity: float   # 向量相似度
    quality_score: float # SkillUsageTracker 质量分
    description: str    # 技能描述
```

### 5.4 invalidate() 方法

当技能发生变更时被调用（通过 `SkillManageTool.on_skill_change` 回调）：

```python
def invalidate(self, skill_name=None, action="update"):
    if action == "delete" and skill_name:
        self._skill_index.remove(skill_name)
    elif skill_name and action in ("create", "patch", "update"):
        content = self._loader.load_skill(skill_name, inject_content=False)
        if content:
            self._skill_index.upsert(skill_name, content)
    else:
        self._index_built = False
        self.ensure_index()  # 全量重建
```

---

## 6. ContextBuilder 集成

### 6.1 build_system_prompt 变更

**新增参数：**

```python
def build_system_prompt(
    self,
    skill_names: list[str] | None = None,
    channel: str | None = None,
    retrieved_items: list[dict[str, Any]] | None = None,
    skill_routing: SkillRoutingResult | None = None,  # 新增
) -> str:
```

**技能加载逻辑变更：**

```python
# 原有逻辑（保留作为 fallback）：
skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
if skills_summary:
    parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))

# 新逻辑（有路由候选时）：
if skill_routing and skill_routing.candidates:
    candidate_names = [c.name for c in skill_routing.candidates]
    candidate_content = self.skills.load_skills_for_context(candidate_names)
    candidate_summary = self._format_routing_candidates(skill_routing.candidates)
    parts.append(
        f"# Recommended Skills (auto-selected)\n\n"
        f"{candidate_summary}\n\n"
        f"## Skill Details\n\n{candidate_content}"
    )
else:
    # Fallback: 原有全量摘要行为
    skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
    if skills_summary:
        parts.append(render_template("agent/skills_section.md", skills_summary=skills_summary))
```

### 6.2 候选格式化

```python
@staticmethod
def _format_routing_candidates(candidates: list) -> str:
    lines = [
        "| # | Skill | Score | Source | Description |",
        "|---|-------|-------|--------|-------------|",
    ]
    for i, c in enumerate(candidates, 1):
        lines.append(f"| {i} | **{c.name}** | {c.score:.2f} | {c.source} | {c.description} |")
    return "\n".join(lines)
```

### 6.3 build_messages 透传

```python
def build_messages(self, ..., skill_routing=None):
    messages = [
        {"role": "system", "content": self.build_system_prompt(
            ..., skill_routing=skill_routing
        )},
        *history,
    ]
```

### 6.4 AgentLoop 调用点

在 `_process_message` 中，memory retrieval 之后、`build_messages` 之前：

```python
# ---- Skill Pre-Retrieval ----
_skill_routing = None
if self._skill_router:
    with Timer() as _t_skill:
        _skill_routing = self._skill_router.route(
            user_message=raw_text,
            session_key=key,
            channel=msg.channel,
        )
    _wakeup.skill_routing_time_ms = _t_skill.elapsed_ms
    _wakeup.skill_candidates = [c.name for c in _skill_routing.candidates]

# Context building（透传 skill_routing）
initial_messages = self.context.build_messages(
    ..., skill_routing=_skill_routing,
)
```

---

## 7. SkillManageTool 变更回调

### 7.1 新增属性

```python
class SkillManageTool(Tool):
    def __init__(self, ...):
        ...
        self.on_skill_change: Any = None  # callback(skill_name, action)
```

### 7.2 回调触发点

在 `execute()` 方法中，每个成功操作后触发：

```python
# create
ok, msg = self._manager.create(name, content, reason or "")
if ok:
    self._notify_skill_change(name, "create")

# edit
ok, msg = self._manager.edit(name, content, reason or "")
if ok:
    self._notify_skill_change(name, "patch")

# patch
ok, msg = self._manager.patch(name, old_string, new_string, patch_reason)
if ok:
    self._notify_skill_change(name, "patch")

# delete
ok, msg = self._manager.delete(name)
if ok:
    self._notify_skill_change(name, "delete")
```

### 7.3 回调实现

```python
def _notify_skill_change(self, skill_name, action):
    if self.on_skill_change:
        try:
            self.on_skill_change(skill_name=skill_name, action=action)
        except Exception as e:
            logger.debug("[SkillManageTool] on_skill_change callback failed: {}", e)
```

在 `AgentLoop.__init__()` 中注册回调：

```python
self._skill_manage.on_skill_change = self._skill_router.invalidate
```

---

## 8. Dream Phase 3：memory.md LLM 总结

### 8.1 背景

改造前，`memory.md` 由 `CategoryManager.regenerate_memory_md()` 机械生成：
- 从每个 category 文件提取第一个 `> 摘要：` 行
- 按 type 分组拼接

**问题：**
- category 文件大多没有 `> 摘要：` 行，fallback 到只返回文件名
- 没有跨 category 的综合总结
- 产出的 memory.md 只是 category 名称列表，无实际价值

### 8.2 改造方案

在 Dream Phase 2 后新增 Phase 3，用 LLM 对所有 category 内容做综合总结。

**流程：**

```
Dream Phase 2 完成
    ↓
Phase 3: _regenerate_memory_md_with_llm()
    ↓
Python 读取所有 category 文件内容 → 拼接
    ↓
LLM 总结（无工具，纯文本生成）
    ↓
Python 写入 memory.md
    ↓
失败时 fallback 到机械拼接
```

### 8.3 实现

**触发点：** Dream.run() 中 Phase 2 结束后、cursor 推进前。

```python
# Phase 2 完成后
try:
    await self._regenerate_memory_md_with_llm()
except Exception:
    logger.exception("Dream Phase 3 failed, falling back to mechanical")
    if self._category_manager:
        self._category_manager.regenerate_memory_md()
```

**核心方法：**

```python
async def _regenerate_memory_md_with_llm(self):
    # 1. 读取所有 category 内容
    category_parts = []
    for meta in self._category_manager._index.list_active():
        content = self._category_manager.read_category_md(cat_id)
        category_parts.append(f"### [{cat_type}] {name}\n{content}")

    # 2. 构建 prompt
    prompt = render_template("agent/dream_phase3_memory.md", category_content=...)

    # 3. LLM 生成（无工具，max_iterations=1）
    response = await self._runner.run(AgentRunSpec(
        initial_messages=messages,
        tools=[],
        model=self.model,
        max_iterations=1,
    ))

    # 4. 写入 memory.md
    memory_md_path.write_text(response.content)
```

### 8.4 Prompt 模板

模板文件：`fincat/templates/agent/dream_phase3_memory.md`

**要求：**
- 按维度分节：画像、知识、偏好、行为洞察、合规、近期事件
- 每节用 bullet points，每条不超过 2 行
- 去重：相同信息只保留最准确/最新的
- 淘汰：忽略过期、已完成的一次性任务、超过 30 天的临时事件
- 优先级：重要个人信息 > 偏好 > 知识 > 行为模式 > 合规规则
- 总字数 500 字以内

### 8.5 为什么不用工具

Phase 3 不给 LLM 配置工具（`tools=[]`），原因是：

1. **信息已全量在 prompt 里** — `_build_category_file_context()` 已拼接所有 category 内容
2. **任务简单** — 总结是"输入→输出"的单步任务，不需要迭代编辑
3. **省 token** — 无 tool call 轮次，一次调用搞定
4. **确定性高** — Python 控制读写路径，不依赖 LLM 自行决定读什么

---

## 9. 性能预算

### 9.1 启动成本

| 操作 | 耗时 | 说明 |
|------|------|------|
| `SkillVectorIndex.build()` — 缓存命中 | ~1ms | 从 JSON 加载向量 |
| `SkillVectorIndex.build()` — 缓存缺失 | ~50-100ms | `embed_batch` 15 个技能 |
| 向量内存占用 | ~30KB | 15 个 × 512 维 × 4 字节 |

### 9.2 每轮路由成本

| 层 | 操作 | 耗时 |
|----|------|------|
| Layer 1 | ~15 个正则匹配 | < 1ms |
| Layer 2 | query embed + 15 次余弦相似度 | < 5ms |
| Layer 3 | dict 查询 + quality score | < 1ms |
| Layer 4 | 算术运算 | < 1ms |
| **总计** | | **< 10ms**（不含 query embed） |

### 9.3 Token 节省

| 场景 | 原方案 tokens | 新方案 tokens | 节省 |
|------|-------------|-------------|------|
| 15 个技能全量摘要 | ~800 | ~200（1-3 个候选） | ~75% |
| 技能详情加载 | LLM 用 read_file 加载 1 个 | 直接注入 1-3 个 | 减少 1 次 tool call |

### 9.4 Dream Phase 3 成本

| 操作 | 说明 |
|------|------|
| LLM 调用 | 1 次，无 tool call，max_iterations=1 |
| 输入 tokens | 所有 category 内容（~5000-10000 tokens） |
| 输出 tokens | memory.md 摘要（~500 tokens） |
| 频率 | 每天凌晨 1 次 |

---

## 10. 实施阶段

### Phase 1（已完成）— 核心能力

- [x] 创建 `skill_router.py`：`SkillVectorIndex` + `SkillRouter`（Layer 2 + Layer 4）
- [x] 创建 `skill_candidates.md` 模板
- [x] 修改 `loop.py`：初始化 SkillRouter + `_process_message` 中调用
- [x] 修改 `context.py`：`build_system_prompt` 支持 `skill_routing`
- [x] 修改 `skill_manage.py`：增加 `on_skill_change` 回调
- [x] 修改 `memory.py`：Dream Phase 3 memory.md LLM 总结

### Phase 2（待实施）— Layer 1 增强

- [ ] 从技能 description 提取中文关键词
- [ ] 斜杠命令前缀匹配
- [ ] Layer 1 命中给予评分加成

### Phase 3（待实施）— Layer 3 会话行为

- [ ] 会话级技能使用历史跟踪
- [ ] 近因加成

### Phase 4（待实施）— 优化与调优

- [ ] query embedding 在 memory retrieval 和 skill routing 间共享
- [ ] 单候选高置信度（score > 0.8）时自动激活，跳过 LLM 决策
- [ ] 根据实际使用数据调优评分权重和阈值

---

## 11. 数据流全景图

```
用户消息到达
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  MemoryRetrievalFilter                                  │
│  意图分类 → 跳过不需要记忆的查询                        │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Memory Retrieval Pipeline                              │
│  QueryPreprocessor → QueryVectorCache → EmbeddingEngine │
│  → CategoryVectorIndex → MemoryStoreV2                  │
│  → _retrieved_items                                     │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  SkillRouter.route()                    ← 新增          │
│                                                         │
│  Layer 1: 规则匹配（关键词、斜杠命令）                   │
│      ↓                                                  │
│  Layer 2: 语义向量检索（SkillVectorIndex.search）        │
│      ↓                                                  │
│  Layer 3: 行为过滤（stale、activate_when）               │
│      ↓                                                  │
│  Layer 4: 综合评分 → top-3 candidates                   │
│                                                         │
│  返回: SkillRoutingResult                               │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  ContextBuilder.build_system_prompt()                   │
│                                                         │
│  always:true 技能 → 无条件加载                          │
│  skill_routing.candidates → 只加载 1-3 个候选完整内容    │
│  无候选 → fallback 全量摘要（原有行为）                  │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  AgentRunner.run()                                      │
│  LLM 在 1-3 个候选中做最终决策                          │
│  → 选择最合适的技能 / 直接回答                          │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  任务完成后                                              │
│  SkillEvolver → 可能创建/patch 技能                     │
│  → SkillManageTool.on_skill_change                      │
│  → SkillRouter.invalidate() → 更新向量索引              │
└─────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

凌晨 03:00 Dream 定时任务
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 1: LLM 分析历史 → [FILE] 标签行                  │
│  Phase 2: LLM agent 编辑 category .md 文件               │
│  Phase 3: LLM 总结所有 category → memory.md  ← 新增     │
│  → git auto-commit                                      │
└─────────────────────────────────────────────────────────┘
```

---

# 第二章：Topic 生成推送机制改造（2026-05-13）

> 关联文件：topic/topic_store.py, topic/topic_dispatcher.py, topic/topic_cache.py, topic/prediction_engine.py, topic/event_topic_bridge.py, pattern_miner.py, loop.py

---

## 12. 背景与问题

### 12.1 原有方案

Topic 系统由 6 个模块组成：

| 模块 | 文件 | 角色 |
|------|------|------|
| UnifiedTopic | topic_store.py | 数据模型（topic_id, source, category, priority, entities, expires_at, delivered） |
| TopicStore | topic_store.py | JSONL 持久化 + 去重 + 过期清理 + 多 channel 投递跟踪 |
| TopicDispatcher | topic_dispatcher.py | 30s asyncio.sleep 轮询，分发到已注册 WebSocket channel |
| TopicCache | topic_cache.py | Top-N 缓存（遗留系统，从未接入推送） |
| PredictionEngine | prediction_engine.py | 零 LLM 正则匹配生成 topic |
| PatternMiner | pattern_miner.py | 时间周期/语义关联/实体共现模式挖掘 |

### 12.2 数据流（改造前）

```
用户消息 → _process_message()
    → PredictionEngine.predict(msg.content, session_history=None)  ← session_history 硬编码 None
    → TopicStore.add(UnifiedTopic)
    → TopicDispatcher._tick() [30s asyncio.sleep 轮询]
    → WebSocketChannel.broadcast_topics()
    → 前端 FloatingContextLayer [最多 3 张卡片]
    → 用户反馈 (dismiss/click/reject)
    → TopicStore.remove_topic() / PredictionEngine.update_confidence()
```

### 12.3 已识别问题

| # | 问题 | 严重度 |
|---|------|--------|
| 1 | **双系统并存**：TopicStore+TopicDispatcher（主推送）和 TopicCache（遗留）职责重叠 | 高 |
| 2 | **session_history 未使用**：`predict(msg.content, session_history=None)` 硬编码，上下文预测是死代码 | 高 |
| 3 | **PatternMiner 关键词提取过于简单**：硬编码 10 个中文关键词，fallback 到 `text[:4]` | 中 |
| 4 | **PredictionEngine 未使用向量匹配**：接收了 EmbeddingEngine 但从未用于预测 | 中 |
| 5 | **TopicCache 未接入推送**：`push_to_frontend()` 存在但从未被调用 | 中 |
| 6 | **TopicStore 全文件重写**：每次 `mark_delivered`/`cleanup`/`remove_topic` 都重写整个 JSONL | 中 |
| 7 | **Event 系统未接入 TopicStore**：ExternalEvent 产生 OutboundMessage 但不创建 UnifiedTopic | 中 |
| 8 | **无 topic 数量上限**：TopicStore 无 max_size，topic 持续累积直到过期 | 低 |
| 9 | **文件散乱**：6 个 topic 相关文件散落在 agent/ 根目录 | 中 |

---

## 13. 改造方案

### 13.1 文件整理 — topic 子文件夹（解决问题 9）

将 4 个 topic 专属模块整理到 `fincat/agent/topic/` 子文件夹。`pattern_miner.py` 和 `dynamic_rule_store.py` 留在 `agent/` 根目录（被其他模块共用）。

**移动清单：**

| 原路径 | 新路径 |
|--------|--------|
| `fincat/agent/topic_store.py` | `fincat/agent/topic/topic_store.py` |
| `fincat/agent/topic_dispatcher.py` | `fincat/agent/topic/topic_dispatcher.py` |
| `fincat/agent/topic_cache.py` | `fincat/agent/topic/topic_cache.py` |
| `fincat/agent/prediction_engine.py` | `fincat/agent/topic/prediction_engine.py` |

**新增文件：**
- `fincat/agent/topic/__init__.py` — 重新导出主要类
- `fincat/agent/topic/event_topic_bridge.py` — ExternalEvent → UnifiedTopic 转换器

**`__init__.py` 内容：**
```python
from fincat.agent.topic.topic_store import TopicStore, UnifiedTopic, compute_expires_at
from fincat.agent.topic.topic_dispatcher import TopicDispatcher
from fincat.agent.topic.topic_cache import TopicCache
from fincat.agent.topic.prediction_engine import PredictionEngine
from fincat.agent.topic.event_topic_bridge import EventTopicBridge
```

**更新 import 的文件（8 个）：**
- `fincat/agent/loop.py` — 5 处
- `fincat/cli/commands.py` — 1 处
- `fincat/agent/topic/topic_dispatcher.py` — 内部相对 import
- `fincat/agent/topic/prediction_engine.py` — 内部相对 import
- `tests/agent/test_topic_store.py`、`test_topic_cache.py`、`test_prediction_engine.py`、`test_proactive_skill.py`

### 13.2 统一 Topic 存储层（解决问题 1、5、6、8）

**TopicStore 增强：**

```python
class TopicStore:
    _FEEDBACK_DELTAS = {"click": 0.05, "ignore": -0.02, "reject": -0.10, "engage": 0.10}
    _MIN_CONFIDENCE = 0.3

    def __init__(self, path: Path, max_topics: int = 50):
        ...

    def adjust_confidence(self, topic_id: str, feedback: str) -> float:
        """Adjust topic confidence based on user feedback."""
        ...

    def on_user_feedback(self, topic_id: str, feedback: str) -> None:
        """Handle user feedback: adjust confidence + remove if rejected."""
        ...

    def _evict(self) -> None:
        """Evict lowest-priority + oldest topics when exceeding max_topics."""
        ...
```

**废弃 TopicCache：**
- PredictionEngine 移除 `topic_cache` 参数
- loop.py 移除 TopicCache 初始化
- topic_cache.py 保留但不再被核心流程引用

### 13.3 PredictionEngine 增强（解决问题 2）

**启用 session_history：**

```python
# loop.py — _process_message 中
recent_history = history[-3:] if history else None
predictions = self._prediction_engine.predict(
    msg.content if isinstance(msg.content, str) else "",
    session_history=recent_history,
)
```

`predict_from_context()` 拼接历史消息后做正则匹配，提高跨轮次召回率。
例如：用户上一轮说"茅台"，本轮说"走势如何"，拼接后能匹配"茅台.*走势"。

### 13.4 PatternMiner 增强（解决问题 3）

**关键词提取升级：**

```python
class PatternMiner:
    def __init__(self, resource_store, memory_db, embedding=None):
        ...
        self._known_entities: set[str] = set()
        self._known_tags: set[str] = set()
        self._load_known_entities()  # 从 memory_item 表加载

    def _extract_topic(self, text: str) -> str:
        """Priority: known entities > known tags > hardcoded keywords > first 4 chars."""
        for entity in sorted(self._known_entities, key=len, reverse=True):
            if entity in text:
                return entity
        for tag in sorted(self._known_tags, key=len, reverse=True):
            if tag in text:
                return tag
        # Fallback to hardcoded keywords
        ...
```

**语义关联增强：**

```python
def _mine_vector_transitions(self, conversations: list[dict]) -> list[dict]:
    """Find topic transitions using embedding similarity."""
    vectors = self._embedding.embed_batch(contents)
    for i in range(len(vectors) - 1):
        sim = self._embedding.cosine_similarity(vectors[i], vectors[i + 1])
        if sim >= 0.6:  # Related conversations
            topic_a = self._extract_topic(contents[i])
            topic_b = self._extract_topic(contents[i + 1])
            ...
```

### 13.5 Event 系统接入 TopicStore（解决问题 7）

**新增 `EventTopicBridge`：**

```python
class EventTopicBridge:
    """Bridges ExternalEvents from EventQueue into TopicStore as UnifiedTopics."""

    def __init__(self, event_queue: EventQueue, topic_store: TopicStore):
        self._queue = event_queue
        self._store = topic_store

    async def drain(self) -> int:
        """Drain all pending events from the queue and convert to topics."""
        ...

    def _convert(self, event: ExternalEvent) -> UnifiedTopic | None:
        """Convert an ExternalEvent to a UnifiedTopic."""
        category = _EVENT_CATEGORY.get(event.event_type, "insight")
        priority = _SEVERITY_PRIORITY.get(event.severity, 1)
        return UnifiedTopic(
            topic_id=f"evt_{uuid.uuid4().hex[:8]}",
            source="event",
            source_name=event.source,
            category=category,
            title=event.title,
            content=event.summary,
            priority=priority,
            entities=event.entities,
            ...
        )
```

**事件映射：**

| 事件类型 | Topic Category | Severity → Priority |
|----------|---------------|---------------------|
| market_move | alert | critical→3, warning→2, info→1 |
| new_document | news | info→1 |
| regulation_change | alert | warning→2 |
| custom | insight | info→1 |

**loop.py 集成：**

```python
# 初始化
self._event_topic_bridge = EventTopicBridge(
    event_queue=self._event_queue,
    topic_store=self._topic_store,
)

# _monitor_event_sources() 中，事件轮询后
await self._event_topic_bridge.drain()
```

---

## 14. 数据流全景图（改造后）

```
用户消息到达
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  _process_message()                                      │
│                                                          │
│  1. MemoryRetrievalFilter → 是否需要记忆                  │
│  2. Memory Retrieval Pipeline → _retrieved_items          │
│  3. SkillRouter.route() → skill_routing                   │
│  4. PredictionEngine.predict(msg, session_history[-3:])   │  ← session_history 启用
│     → TopicStore.add(UnifiedTopic)                        │
│  5. ContextBuilder.build_messages(skill_routing=...)       │
│  6. AgentRunner.run() → LLM 响应                          │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  TopicDispatcher (30s asyncio.sleep 轮询)                 │
│  TopicStore.get_pending(channel) → WebSocket broadcast    │
│  → 前端 FloatingContextLayer [最多 3 张卡片]               │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  用户反馈 (dismiss/click/reject)                          │
│  → TopicStore.on_user_feedback(topic_id, feedback)        │
│  → adjust_confidence + 自动移除低置信度 topic              │
└─────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

定时任务：_monitor_event_sources()
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  MarketEventSource.poll() → AKShare 行情异动              │
│  DocumentEventSource.poll() → 新文档入库                   │
│  → EventQueue.push(event)                                 │
│  → EventTopicBridge.drain()                               │
│     → ExternalEvent → UnifiedTopic → TopicStore.add()     │
└─────────────────────────────────────────────────────────┘

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

凌晨定时任务：PatternMiner.run_daily()
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  mine_time_periodicity() → 时间周期模式                    │
│  mine_semantic_association() → 语义关联（关键词+向量）      │
│  mine_entity_association() → 实体共现                      │
│  → DynamicRuleStore.add_rule()                             │
│  → PredictionEngine.add_dynamic_rule()                     │
└─────────────────────────────────────────────────────────┘
```

---

## 15. 实施阶段

### Phase 1（已完成）— 文件整理

- [x] 创建 `fincat/agent/topic/` 目录 + `__init__.py`
- [x] 移动 4 个文件到新目录
- [x] 更新 8 个文件的 import 路径
- [x] 测试通过：1996 passed

### Phase 2（已完成）— 统一存储层 + session_history

- [x] TopicStore 增强（max_topics=50、adjust_confidence、on_user_feedback、_evict）
- [x] 废弃 TopicCache，PredictionEngine 移除 topic_cache 参数
- [x] 启用 session_history（传入 history[-3:]）
- [x] 测试通过：35 topic tests passed

### Phase 3（已完成）— PatternMiner 增强

- [x] 关键词提取升级（memory_item entities + tags 优先匹配）
- [x] 语义关联增强（_mine_vector_transitions 向量相似度转移）
- [x] 测试通过：8 pattern_miner tests passed

### Phase 4（已完成）— Event 系统接入

- [x] 创建 `EventTopicBridge`（ExternalEvent → UnifiedTopic）
- [x] severity → priority 映射、event_type → category 映射
- [x] loop.py 集成：_monitor_event_sources() 中调用 bridge.drain()
- [x] 测试通过：全部 1996 tests passed

---

# 第三章：RAG 外部知识库升级 — FTS5 + FAISS 向量混合检索（2026-05-13）

> 关联文件：knowledge/text_splitter.py, knowledge/vector_store.py, knowledge/hybrid_retriever.py, knowledge/abstract_store.py, knowledge/factory.py, knowledge/reindex.py, knowledge/ingest.py, knowledge/mount.py, knowledge/store.py, agent/tools/rag.py, agent/loop.py

---

## 16. 背景与问题

### 16.1 原有方案

改造前，RAG 知识库仅有三通道检索：

```
用户消息 → rag_search 工具
    → Channel 1: Title/path LIKE 匹配（结构匹配）
    → Channel 2: FTS5 BM25 全文搜索
    → Channel 3: rag_facts 精确数据查找
    → _rank() 合并排序
```

无向量语义搜索能力，全靠关键词匹配。

### 16.2 存在的问题

| 问题 | 影响 |
|------|------|
| **无语义匹配** | 用户问"房贷利率"无法匹配"个人住房贷款基准利率" |
| **无分块** | 整个 section 作为检索单元，长文档召回精度低 |
| **无向量索引** | Agent 记忆系统已有 FAISS + bge-small-zh-v1.5，但知识库未接入 |
| **用户无法上传文档** | 无文件追踪、无 user_upload 类型区分 |

### 16.3 改造目标

- 升级为"FTS5 + FAISS 向量"四通道混合检索，用 RRF 融合排序
- 支持用户动态上传 PDF（分块 + 向量化 + 文件追踪）
- RAG 保持为纯工具，agent 自主判断何时调用
- SQLite + FAISS（零外部依赖），抽象层便于将来迁移 PostgreSQL+pgvector

---

## 17. 整体架构

### 17.1 改造后的流程

**入库管线：**

```
文件 → Parser → DocSection[]
    → TextSplitter → TextChunk[] (chunk_size=1000, overlap=200)
    → EmbeddingEngine.embed_batch() → vector[]
    → KnowledgeVectorStore.add_chunks() → FAISS IndexFlatIP
    → RAGKnowledgeStore.add_chunks_batch() → rag_chunks + rag_chunks_fts
    → FileTracker.update_status(completed)
```

**查询管线：**

```
用户消息 → rag_search 工具
    → HybridRetriever.search()
        → Channel 1: Vector — FAISS 余弦相似度 (weight=1.0)
        → Channel 2: FTS5 — BM25 全文搜索 (weight=0.8)
        → Channel 3: Title — 标题/路径 LIKE (weight=0.6)
        → Channel 4: Facts — rag_facts 精确查找 (weight=1.2, 利率查询→1.5)
    → RRF 融合: RRF_score(d) = Σ w_c / (k + rank_c(d)), k=60
    → 返回 top-K HybridResult
```

### 17.2 新增文件

| 文件 | 用途 |
|------|------|
| `fincat/knowledge/text_splitter.py` | 文本分块引擎（递归字符分割，中文适配） |
| `fincat/knowledge/vector_store.py` | FAISS 向量索引（IndexFlatIP + SQLite 映射） |
| `fincat/knowledge/hybrid_retriever.py` | RRF 四通道混合检索器 |
| `fincat/knowledge/abstract_store.py` | 存储抽象接口（AbstractKnowledgeStore + AbstractVectorIndex） |
| `fincat/knowledge/factory.py` | 后端工厂（sqlite / pg） |
| `fincat/knowledge/reindex.py` | 批量重索引脚本 |

### 17.3 修改文件

| 文件 | 改动 |
|------|------|
| `fincat/knowledge/store.py` | 新增 V2 schema（rag_chunks, rag_chunks_fts, rag_files）、chunk 方法、文件追踪 |
| `fincat/knowledge/ingest.py` | 新增 `ingest_with_vectors()` 和 `ingest_file()` 方法 |
| `fincat/knowledge/mount.py` | 新增 `user_id`、`vectorize` 参数，使用 `ingest_with_vectors()` |
| `fincat/agent/tools/rag.py` | 切换到 HybridRetriever，保留 legacy 回退 |
| `fincat/agent/loop.py` | `_create_rag_tool()` 初始化 HybridRetriever 并注入 RAGSearchTool |

### 17.4 不需要修改的文件

| 文件 | 原因 |
|------|------|
| `agent/embedding.py` | 作为依赖直接复用（bge-small-zh-v1.5, 512维） |
| `agent/memory_retrieval_filter.py` | RAG 作为工具，不主动注入 system prompt |
| `agent/context.py` | 不涉及 RAG |
| `knowledge/parsers/` | PDF/HTML 解析器不变 |
| `knowledge/crawlers/` | 爬虫不变 |
| `knowledge/entity_extractor.py` | 实体提取不变 |

---

## 18. 文本分块器

### 18.1 TextChunk 数据模型

```python
@dataclass
class TextChunk:
    chunk_id: str          # "doc_id:section_title:offset_start"
    content: str           # 分块文本
    doc_id: str            # 文档 ID
    section_title: str     # 所属章节标题
    page_start: int | None
    page_end: int | None
    offset_start: int      # 在原始文本中的起始偏移
    offset_end: int        # 在原始文本中的结束偏移
    content_type: str      # "text" | "table" | "list"
    metadata: dict[str, Any]
```

### 18.2 分隔符优先级

```python
DEFAULT_SEPARATORS = ["\n\n", "\n", "。", "；", "，", " "]
```

递归字符分割：先尝试按 `\n\n` 分段，再按 `\n` 分行，再按 `。` 分句，以此类推。适配中文文档。

### 18.3 关键方法

```python
class TextSplitter:
    def __init__(self, chunk_size=1000, chunk_overlap=200, separators=None)
    def split_text(self, text, doc_id="", section_title="", ...) -> list[TextChunk]
    def split_sections(self, sections, doc_id="") -> list[TextChunk]
    def _recursive_split(self, text, separators) -> list[str]
    def _merge_splits(self, splits, separator) -> list[str]
```

---

## 19. FAISS 向量索引

### 19.1 设计思路

复用 `MemoryStoreV2` 的 FAISS 模式：

- SQLite `rag_vector_mapping` 表存储 `faiss_index ↔ chunk_id` 映射
- FAISS `IndexFlatIP`（内积索引，L2 归一化后等价于余弦相似度）
- 内存 `_id_to_idx` / `_idx_to_id` 字典加速查找
- Append-orphan 策略：新向量追加到末尾，旧槽位变孤儿，定期 `rebuild_index()` 压缩
- 持久化到 `vector_dir / "knowledge_vectors.index"`

### 19.2 关键方法

```python
class KnowledgeVectorStore:
    def add_chunks(self, chunks: list[TextChunk]) -> int     # 嵌入 + 索引
    def search(self, query_vec, top_k=20, threshold=0.5) -> list[VectorSearchResult]
    def search_filtered(self, query_vec, category=None, ...) -> list[VectorSearchResult]
    def delete_by_doc_id(self, doc_id: str) -> int
    def rebuild_index(self) -> None                           # 全量重建
    def get_stats(self) -> dict
```

---

## 20. RRF 混合检索器

### 20.1 四通道设计

| 通道 | 数据源 | 权重 | 说明 |
|------|--------|------|------|
| Vector | FAISS rag_chunks 向量 | 1.0 | 语义相似度 |
| FTS5 | rag_chunks_fts BM25 | 0.8 | 关键词匹配 |
| Title | rag_sections 标题 LIKE | 0.6 | 结构匹配 |
| Facts | rag_facts FTS5 | 1.2（利率查询→1.5） | 精确数据 |

### 20.2 RRF 融合公式

```
RRF_score(d) = Σ w_c / (k + rank_c(d))
```

- `k = 60`（常量）
- `w_c` = 通道权重
- `rank_c(d)` = 文档 d 在通道 c 中的排名（0-indexed）

### 20.3 动态权重

查询包含利率/费率关键词时，Facts 通道权重从 1.2 提升到 1.5：

```python
_FACT_KEYWORDS = ["多少", "利率", "费率", "收益率", "税率", "限额", "额度",
                  "起征点", "手续费", "万几", "百分比", "比例", "金额", "利息",
                  "年化", "基准", "LPR", "lpr"]
```

### 20.4 HybridResult 数据模型

```python
@dataclass
class HybridResult:
    chunk_id: str
    content: str
    title: str
    category: str
    rrf_score: float           # RRF 融合得分
    vector_score: float        # Vector 通道贡献
    fts_score: float           # FTS 通道贡献
    channels_matched: list[str]  # 命中的通道列表
    source: str | None
    doc_id: str | None
    page_start: int | None
```

---

## 21. Schema 迁移

### 21.1 新增表

**rag_chunks** — 固定分块内容：

```sql
CREATE TABLE rag_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id TEXT UNIQUE NOT NULL,
    doc_id TEXT NOT NULL,
    section_id INTEGER,
    content TEXT NOT NULL,
    content_type TEXT DEFAULT 'text',
    section_title TEXT,
    page_start INTEGER, page_end INTEGER,
    offset_start INTEGER, offset_end INTEGER,
    chunk_index INTEGER, token_count INTEGER,
    category TEXT NOT NULL DEFAULT 'product',
    source_type TEXT DEFAULT 'static',
    user_id TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
```

**rag_chunks_fts** — FTS5 全文索引：

```sql
CREATE VIRTUAL TABLE rag_chunks_fts USING fts5(content, section_title, tokenize='unicode61');
```

**rag_files** — 文件上传状态：

```sql
CREATE TABLE rag_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT UNIQUE NOT NULL,
    file_name TEXT NOT NULL, file_type TEXT NOT NULL,
    file_path TEXT, file_size INTEGER,
    category TEXT NOT NULL,
    source_type TEXT DEFAULT 'static', user_id TEXT,
    status TEXT DEFAULT 'processing',
    error_message TEXT, doc_id TEXT,
    chunk_count INTEGER DEFAULT 0,
    metadata TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```

### 21.2 rag_documents 新增列

- `source_type TEXT DEFAULT 'static'` — 区分 static / user_upload
- `user_id TEXT` — 上传用户 ID
- `chunk_count INTEGER DEFAULT 0` — 分块数量

---

## 22. Agent 集成

### 22.1 RAG 工具升级

`RAGSearchTool` 新增 `hybrid_retriever` 参数：

```python
class RAGSearchTool(Tool):
    def __init__(self, hybrid_retriever=None, store=None):
        self._retriever = hybrid_retriever
        self._store = store
```

优先使用 `HybridRetriever.search()`，无 retriever 时回退到 legacy 三通道搜索。

### 22.2 loop.py 初始化

```python
def _create_rag_tool(self) -> RAGSearchTool:
    if self._embedding is None:
        return RAGSearchTool()
    try:
        store = get_knowledge_store()
        vector_store = KnowledgeVectorStore(...)
        retriever = HybridRetriever(store, vector_store, self._embedding)
        return RAGSearchTool(hybrid_retriever=retriever, store=store)
    except Exception:
        return RAGSearchTool()  # fallback
```

### 22.3 设计决策：RAG 作为纯工具

RAG 不主动注入 system prompt，由 agent 自主判断何时调用 `rag_search` 工具。理由：
- 减少每轮 token 消耗（大部分查询不需要知识库）
- Agent 可根据对话上下文判断是否需要查资料
- 与 skill 路由系统解耦

---

## 23. 用户上传文档

### 23.1 入口

```python
# mount.py CLI
python -m fincat.knowledge.mount report.pdf --category user_upload --user-id user123

# 程序入口
pipeline = IngestPipeline(embedding=embedding, vector_store=vector_store)
result = pipeline.ingest_file(file_path, category="user_upload", user_id="user123")
```

### 23.2 流程

```
用户上传 PDF
    → mount_file(user_id="user123", vectorize=True)
    → _init_vector_pipeline()  # 初始化 embedding + vector_store
    → IngestPipeline.ingest_with_vectors()
        → Parser → sections → TextSplitter → chunks
        → EmbeddingEngine.embed_batch() → vectors
        → KnowledgeVectorStore.add_chunks() → FAISS
        → RAGKnowledgeStore.add_chunks_batch() → SQLite
    → FileTracker.update_status("completed")
```

### 23.3 数据隔离

通过 `source_type` 和 `user_id` 列区分：
- `source_type="static"` — 管理员预加载的内部文档
- `source_type="user_upload"` + `user_id` — 用户上传的文档

查询时可通过 `search_filtered(source_type="user_upload", user_id="user123")` 过滤。

---

## 24. 批量重索引

### 24.1 CLI 命令

```bash
# 全量重索引（重新分块 + 重新嵌入）
python -m fincat.knowledge.reindex

# 仅重建 FAISS 索引（跳过重新分块，更快）
python -m fincat.knowledge.reindex --rebuild-faiss

# 指定数据库路径
python -m fincat.knowledge.reindex --db ~/.fincat/knowledge.db
```

### 24.2 流程

```
reindex_all():
    → 清除 rag_chunks + rag_chunks_fts + rag_vector_mapping
    → 重建空 FAISS IndexFlatIP
    → 遍历 rag_documents
        → 读取 rag_sections
        → TextSplitter.split_sections() → chunks
        → add_chunks_batch() → SQLite
        → KnowledgeVectorStore.add_chunks() → FAISS
```

---

## 25. 抽象接口与工厂

### 25.1 抽象接口

```python
class AbstractKnowledgeStore(ABC):
    def add_document(...), add_section(...), add_chunk(...)
    def search(...), search_chunks_fts(...)
    def delete_chunks_by_doc_id(...)
    def get_document(...), get_sections(...), get_chunks(...)
    def get_stats(...)

class AbstractVectorIndex(ABC):
    def add_chunks(chunks) -> int
    def search(query_vec, top_k, threshold) -> list
    def search_filtered(query_vec, category, ...) -> list
    def delete_by_doc_id(doc_id) -> int
    def rebuild_index()
    def get_stats() -> dict
```

### 25.2 工厂

```python
def create_knowledge_store(db_path, vector_dir, embedding, backend="sqlite"):
    # "sqlite" → RAGKnowledgeStore + KnowledgeVectorStore
    # "pg"     → PGKnowledgeStore + PGVectorIndex（将来）
```

---

## 26. 性能预算

### 26.1 入库成本

| 操作 | 耗时 | 说明 |
|------|------|------|
| PDF 解析 | ~1-5s/文档 | PyMuPDF |
| 文本分块 | <100ms | 递归字符分割 |
| 批量嵌入 | ~2-5s/100 chunks | bge-small-zh-v1.5 |
| FAISS 索引追加 | <10ms | IndexFlatIP.add |
| SQLite 写入 | <50ms | 批量 INSERT |

### 26.2 查询成本

| 通道 | 操作 | 耗时 |
|------|------|------|
| Vector | query embed + FAISS search | ~50-100ms |
| FTS5 | BM25 搜索 | <10ms |
| Title | LIKE 匹配 | <5ms |
| Facts | FTS5 搜索 | <5ms |
| RRF 融合 | 算术运算 | <1ms |
| **总计** | | **~70-120ms** |

### 26.3 存储开销

| 组件 | 大小 | 说明 |
|------|------|------|
| FAISS 索引 | ~2KB/chunk | 512维 × 4字节 |
| rag_chunks 表 | ~1-2KB/chunk | 文本 + 元数据 |
| rag_vector_mapping 表 | ~200B/chunk | faiss_index ↔ chunk_id |
| 1000 chunks 总计 | ~4MB | |

---

## 27. 数据流全景图

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

入库管线（静态文档 + 用户上传）

    文件 (PDF/HTML/MD)
        │
        ▼
    ┌─────────────────────────────────────────────────────┐
    │  Parser (PDFStructureParser / HTMLContentParser)     │
    │  → DocIndex { doc_id, title, sections[] }            │
    └─────────────────────────────────────────────────────┘
        │
        ▼
    ┌─────────────────────────────────────────────────────┐
    │  TextSplitter (chunk_size=1000, overlap=200)         │
    │  → TextChunk[] { chunk_id, content, offset, ... }   │
    └─────────────────────────────────────────────────────┘
        │
        ▼
    ┌─────────────────────────────────────────────────────┐
    │  EmbeddingEngine.embed_batch()                       │
    │  bge-small-zh-v1.5, 512维                            │
    │  → float32 vectors[]                                 │
    └─────────────────────────────────────────────────────┘
        │
        ├──→ KnowledgeVectorStore.add_chunks() → FAISS IndexFlatIP
        │    + rag_vector_mapping (SQLite)
        │
        └──→ RAGKnowledgeStore.add_chunks_batch()
             → rag_chunks + rag_chunks_fts (SQLite)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

查询管线（Agent 自主调用 rag_search 工具）

    用户消息
        │
        ▼
    ┌─────────────────────────────────────────────────────┐
    │  RAGSearchTool.execute(query)                        │
    │      │                                               │
    │      ▼                                               │
    │  HybridRetriever.search(query, category, top_k)      │
    │      │                                               │
    │      ├──→ Channel 1: Vector (FAISS cosine, w=1.0)    │
    │      ├──→ Channel 2: FTS5 (BM25, w=0.8)              │
    │      ├──→ Channel 3: Title (LIKE, w=0.6)              │
    │      └──→ Channel 4: Facts (FTS5, w=1.2~1.5)         │
    │      │                                               │
    │      ▼                                               │
    │  RRF 融合: score(d) = Σ w/(60+rank)                   │
    │      │                                               │
    │      ▼                                               │
    │  返回 top-K HybridResult                              │
    └─────────────────────────────────────────────────────┘
        │
        ▼
    Agent 根据结果生成回答

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 28. 实施阶段

### Phase 1（已完成）— 基础模块

- [x] 创建 `text_splitter.py`：TextChunk + TextSplitter（递归字符分割）
- [x] 创建 `vector_store.py`：KnowledgeVectorStore（FAISS IndexFlatIP + SQLite 映射）
- [x] 修改 `store.py`：V2 schema 迁移（rag_chunks, rag_chunks_fts, rag_files）、chunk 方法、文件追踪

### Phase 2（已完成）— 入库管线

- [x] 修改 `ingest.py`：新增 `ingest_with_vectors()` 和 `ingest_file()` 方法
- [x] 修改 `mount.py`：新增 `user_id`、`vectorize` 参数，使用 `ingest_with_vectors()`

### Phase 3（已完成）— 混合检索

- [x] 创建 `hybrid_retriever.py`：RRF 四通道混合检索（Vector + FTS5 + Title + Facts）
- [x] 创建 `abstract_store.py`：AbstractKnowledgeStore + AbstractVectorIndex
- [x] 创建 `factory.py`：create_knowledge_store() 工厂

### Phase 4（已完成）— Agent 集成

- [x] 修改 `rag.py`：切换到 HybridRetriever，保留 legacy 回退
- [x] 修改 `loop.py`：`_create_rag_tool()` 初始化 HybridRetriever

### Phase 5（已完成）— 收尾

- [x] 创建 `reindex.py`：批量重索引脚本（`python -m fincat.knowledge.reindex`）
- [x] 测试通过：24 knowledge tests passed
