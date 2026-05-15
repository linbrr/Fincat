# Skill 生命周期管理与 Hermes 机制详解（2026-04-23）

> 生成日期：2026-04-23
> 关联文件：skill_evolver.py, skills.py, loop.py, reflection.py, memory_sqlite.py

---

## 目录

1. [Hermes 机制完整架构](#1-hermes-机制完整架构)
2. [所有类的角色与数据流](#2-所有类的角色与数据流)
3. [Deduplication 比较的是什么](#3-deduplication-比较的是什么)
4. [SkillEvolver 完整流程逐行解析](#4-skillevolver-完整流程逐行解析)
5. [当前代码中的严重问题](#5-当前代码中的严重问题)
6. [Skill 生命周期管理系统（新实现）](#6-skill-生命周期管理系统新实现)
7. [向量搜索状态](#7-向量搜索状态)

---

## 1. Hermes 机制完整架构

### 1.1 不是 Fork，是后台协程

Hermes 的各个 Phase 都是**同一进程内的异步协程**，不是子进程。`AgentLoop._schedule_background()` 底层就是 `asyncio.create_task()`，所有 Phase 共用同一个事件循环，不阻塞主循环。

### 1.2 整体数据流

```
用户消息到达
    ↓
AgentLoop._process_message() [主协程]
    ↓
    完成工具调用，累积 tools_used
    ↓
    ├── Phase 4a: SkillEvolver.on_task_completed(task_ctx)   ← asyncio.create_task
    │        ↓
    │   [触发条件] iterations ≤ 2 且 tools_used ≥ 2 且第一个工具不是 web_search*
    │        ↓
    │   SkillDeduplicator.check_similarity(reflection)
    │   │     ├─ ≥ 0.95 exact  → 跳过，不生成
    │   │     ├─ 0.75-0.94 similar → SkillImprover.enhance() 增强旧 skill
    │   │     └─ < 0.75 novel  → _generate_skill() 用 LLM 生成 SKILL.md
    │   │        ↓
    │   └─→ SkillValidator.validate() — 5步验证
    │             ├─ 通过  → 写入 workspace/skills/{name}/SKILL.md
    │             └─ 失败 → 重试一次 → 还失败则写入 .invalid/{name}/
    │
    └── Phase 1: EventBus.publish(TaskReflectionEvent)   ← asyncio.create_task（并行）
             ↓
             TaskReflectionGenerator._on_task_reflection()  ← 后台协程
             ↓
             LLM 生成结构化 JSON 反思（factors、reusability_score、tools_pattern...）
             ↓
             SQLiteMemoryStore.add_reflection()  → 存入 SQLite
```

**两个后台协程（A: SkillEvolver 和 B: TaskReflectionGenerator）同时起步，互不等待。**

---

## 2. 所有类的角色与数据流

### 2.1 类图

```
AgentLoop._process_message()
    │
    ├─→ SkillEvolver.on_task_completed(task_ctx)     [后台协程 A]
    │        │
    │        ├─→ SkillDeduplicator.check_similarity()
    │        │        │
    │        │        └─→ SQLiteMemoryStore.get_reflections_by_tag("task_reflection")
    │        │                    ↓
    │        │             从 reflection_vault 表读取 Reflection.content（JSON 字符串）
    │        │             做 Jaccard 文本分词相似度比较
    │        │
    │        ├─→ SkillImprover.enhance()   （如果 similar）
    │        │        ├─ 读取旧 SKILL.md
    │        │        ├─ 备份到 .versions/v{N}.md
    │        │        └─ LLM 生成增强版（version +1）
    │        │
    │        ├─→ LLM 生成 SKILL.md         （如果 novel）
    │        │
    │        └─→ SkillValidator.validate()   ← 5步验证
    │
    └─→ EventBus.publish(TaskReflectionEvent)  [后台协程 B]
             ↓
             TaskReflectionGenerator._on_task_reflection()
                      ↓
                      LLM 生成结构化 JSON 反思
                      ↓
                      SQLiteMemoryStore.add_reflection()
```

### 2.2 SQLite 表结构

表 `reflection_vault`（memory.db）：

| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER PRIMARY KEY | 自增 ID |
| content | TEXT | JSON 字符串（如 `{"factors": [...], "reusability_score": 8}`） |
| embedding | TEXT | JSON 数组，向量（可选，sqlite-vec 支持，当前未使用） |
| tags | TEXT | 逗号分隔标签（如 `task_reflection,auto_skill_candidate`） |
| source | TEXT | 来源 |
| reflection_type | TEXT | 类型（如 `task_analysis`） |
| created_at | TEXT | ISO 时间字符串 |

---

## 3. Deduplication 比较的是什么

### 3.1 比较的是结构化 Reflection JSON，不是 Q&A 也不是原始工具列表

Deduplication 比较的是 `reflection` dict，它是从 `TaskReflectionGenerator` 生成的结构化 JSON：

```json
{
  "factors": ["使用腾讯行情接口获取实时数据", "计算均线判断趋势"],
  "reusability_score": 8,
  "tools_pattern": "stock_quote + stock_kline + stock_indicator",
  "skill_name_suggestion": "auto-stock-analysis",
  "triggers": ["分析股票", "什么位置", "走势怎么样"],
  "improvements": ["可加入板块对比", "可加入北向资金"]
}
```

### 3.2 Jaccard 相似度计算方式

Deduplicator 将每个 Reflection 的 `tools_pattern + factors` 拼接成分词集合：

```python
new_tools = new_reflection.get("tools_pattern", "")   # "stock_quote + stock_kline"
new_factors = " ".join(new_reflection.get("factors", []))  # 空格拼接所有因素
new_text = f"{new_tools} {new_factors}".lower()
# 例如："stock_quote + stock_kline 使用腾讯行情接口获取实时数据 计算均线判断趋势"
```

然后计算 **Jaccard 相似度**（词集合交集/并集）：

```python
def _jaccard_similarity(text1: str, text2: str) -> float:
    words1 = set(text1.split())
    words2 = set(text2.split())
    if not words1 or not words2:
        return 0.0
    return len(words1 & words2) / len(words1 | words2)
```

**注意：这是纯文本分词比较，不是向量相似度搜索。** `embedding` 字段虽然在表中存在，但 `check_similarity` 方法完全没有使用向量搜索逻辑。

### 3.3 三个阈值

| 分数 | 结论 | 动作 |
|------|------|------|
| ≥ 0.95 | exact 重复 | 跳过，不生成新 skill |
| 0.75–0.94 | similar 相似 | `SkillImprover.enhance()` 增强已有 skill |
| < 0.75 | novel 新任务 | 进入生成流程 |

---

## 4. SkillEvolver 完整流程逐行解析

### 4.1 触发条件判断（skill_evolver.py 第 87–98 行）

```python
should_auto_evolve = (
    ctx.iterations <= 2
    and len(ctx.tools_used) >= 2
    and ctx.tools_used[0] not in ("web_search", "web_fetch", "web_search_tool")
)
# 不满足 → 直接 return None
```

含义：单轮简单任务不走进化；需要多步操作且有实质内容才生成 skill。

### 4.2 生成 Skill 名称（_generate_skill_name）

规则：
- 从 `user_message` 中找英文单词作为 topic
- 格式：`auto-{topic}-{最后一个工具名}`
- 例如：`"帮我分析比亚迪股票"` + 工具 `[stock_quote, exec, write_file]` → `auto-byd-exec`

### 4.3 Deduplication（如果 reflection 不为 None）

```python
dedup = await self.deduplicator.check_similarity(reflection)
# ├─ exact  → 记录 metric，跳过
# ├─ similar → SkillImprover.enhance(matched, old_refl, new_reflection)
# └─ novel  → 进入生成流程
```

### 4.4 生成 SKILL.md（novel 路径）

```python
skill_md = await self._generate_skill(ctx, skill_name)
# 直接调用 provider.chat_with_retry()，不是子 agent
# prompt 要求 LLM 输出完整 SKILL.md（必须以 --- 开头）
# strip_think() 去掉 LLM 的思考内容
# 如果不以 --- 开头 → 返回 None
```

### 4.5 验证

```python
is_valid, errors = self.validator.validate(skill_md)
# ├─ 通过  → 写入 workspace/skills/{name}/SKILL.md
# └─ 失败：
#      - 重试一次（把 errors 传回 LLM 重新生成）
#      - 重试还失败 → 写入 .invalid/{skill_name}/SKILL.md + errors.txt
```

### 4.6 SkillValidator 5步验证（skills.py）

1. **Frontmatter 格式**：以 `---` 开头和结尾
2. **工具名验证**：frontmatter 中的 `tools: [...]` 列表里的工具名必须在 ToolRegistry 中
3. **Description**：20–500 字符
4. **Examples**：存在 `## 示例` 章节
5. **Metadata**：`version:` 和 `created_at:` 字段存在

---

## 5. 当前代码中的严重问题

### 5.1 `reflection` 参数永远为 None — Deduplication 永远被跳过

```python
# loop.py:896
self._skill_evolver.on_task_completed(task_ctx)
#                                                    ↑
#                                         没有传 reflection 参数！

# skill_evolver.py:109
async def on_task_completed(self, ctx: TaskContext, reflection: dict | None = None)
#                                                                  ↑ 默认 None
```

而 `reflection`（结构化 JSON）是由另一个后台协程异步生成的，两个协程同时起步，SkillEvolver 运行时 SQLite 里还没有数据。

**结果：Deduplication 阶段永远被跳过，任何满足条件的任务都会生成新 skill，即使已有高度相似的。**

### 5.2 `_memory_store` 默认为 None

```python
# loop.py:208-209
enable_sqlite = os.environ.get("NANOBOT_ENABLE_SQLITE_MEMORY", "0") == "1"
self._memory_store = SQLiteMemoryStore(db_path) if enable_sqlite else None
```

默认值为 `"0"`，所以 `memory_store=None`，Deduplicator 拿到的 `existing_reflections` 永远为空列表。

### 5.3 修复方向建议

要让 Hermes 机制真正工作，需要：

1. 在 `loop.py` 中传入 `reflection` 参数（从 `_last_task_ctx` 或从 `EventBus` 同步获取）
2. 或者改为在 Phase 1 完成后再触发 Phase 4（用 `await` 串行，或用 Future/结果共享机制）
3. 设置 `NANOBOT_ENABLE_SQLITE_MEMORY=1` 启用 SQLiteMemoryStore

---

## 6. Skill 生命周期管理系统（新实现）

> 2026-04-23 新增，以下功能已实现。

### 6.1 新增文件

| 文件 | 职责 |
|------|------|
| `fincat/agent/skill_tracker.py` | `SkillUsageTracker` — 调用次数记录 + 质量分计算 + stale 检测 |
| `fincat/agent/skill_lifecycle_manager.py` | `SkillLifecycleManager` — stale 标记 + merge 发现 + `.invalid/` 清理 |
| `fincat/agent/skill_packager.py` | `SkillPackager` — `.skill` 打包/安装/bundle |

### 6.2 修改文件

| 文件 | 改动 |
|------|------|
| `fincat/agent/skills.py` | `SkillsLoader` 新增 external_dirs/usage_tracker/content_injector；新增 `_check_activate_conditions()` + `SkillContentInjector` |
| `fincat/agent/context.py` | `ContextBuilder` 接收新参数并传给 `SkillsLoader` |
| `fincat/agent/loop.py` | 创建 `SkillUsageTracker` 并注入到 ContextBuilder、SkillEvolver |
| `fincat/agent/skill_evolver.py` | 生成/改善 skill 时调用 `usage_tracker.record_invocation()` |
| `fincat/config/schema.py` | 新增 `skill_staleness_days`、`skills_dirs`、`external_skills_mutable` 等配置项 |

### 6.3 质量分公式

```
quality = 0.25 * min(1, invocations/20)
        + 0.25 * max(0, 1 - days_since_used/90)
        + 0.30 * success_rate
        + 0.20 * avg_quality_score
```

**Stale 条件**：`days_since_used > skill_staleness_days`（默认 30 天）

### 6.4 新增 frontmatter 支持

```yaml
activate_when:
  - tool_available('stock_quote')   # 检查工具是否在 ToolRegistry 中
  - time_range('09:30-11:30')         # 检查当前时间是否在范围内
  - env('DEBUG_MODE')                 # 检查环境变量是否设置
```

### 6.5 SkillContentInjector 配置注入

```yaml
# 技能内容中可以使用：
{{config.agents.defaults.model}}   # 注入 config 属性
{{env.USER}}                       # 注入环境变量
```

### 6.6 External Skill Directories

Config 中新增：
```python
skills_dirs: list[str] = []              # 外部技能目录列表
external_skills_mutable: bool = False    # 是否允许修改外部技能
```

**Priority 加载顺序**：workspace > external_dirs > builtin

---

## 7. 向量搜索状态

**当前实现中，向量搜索是预留的但未激活。**

- `add_reflection()` 支持传入 `embedding: list[float]`，会写入 `reflection_vault_vec` 表（sqlite-vec）
- 但 `TaskReflectionGenerator` 调用时**没有传入 embedding**，所以 `embedding` 字段为 NULL
- `get_reflections_by_tag()` 只读取 `reflection_vault` 表的文本内容，**没有向量搜索逻辑**
- `SkillDeduplicator.check_similarity()` 使用纯文本 Jaccard（词集合交集/并集）

**如果要启用向量匹配，需要：**
1. `TaskReflectionGenerator` 调用 embedding API 生成向量
2. `SkillDeduplicator` 使用 `sqlite-vec` 的向量搜索 API

---

## 附录：关键代码路径

| 功能 | 文件 | 行号 |
|------|------|------|
| 后台任务调度 | loop.py | `_schedule_background()` 第 714 行 |
| 触发 SkillEvolver | loop.py | 第 896 行 |
| 触发 TaskReflectionGenerator | loop.py | 第 910 行 |
| TaskContext 构造 | loop.py | 第 888–894 行 |
| SkillEvolver 主逻辑 | skill_evolver.py | `on_task_completed()` 第 72 行 |
| Deduplication | skills.py | `SkillDeduplicator.check_similarity()` 第 401 行 |
| Jaccard 相似度 | skills.py | `_jaccard_similarity()` 第 456 行 |
| Skill 生成（LLM 调用） | skill_evolver.py | `_generate_skill()` 第 240 行 |
| Skill 验证 | skills.py | `SkillValidator.validate()` 第 586 行 |
| 5步验证之工具验证 | skills.py | `_validate_tools()` 第 639 行（此处有 Bug，已修复） |
| Reflection 生成 | reflection.py | `TaskReflectionGenerator._on_task_reflection()` 第 109 行 |
| 反思存入 SQLite | memory_sqlite.py | `SQLiteMemoryStore.add_reflection()` 第 635 行 |
| 读取历史 Reflection | memory_sqlite.py | `SQLiteMemoryStore.get_reflections_by_tag()` 第 808 行 |
