# fincat 记忆系统全面升级 — 分类存储架构设计

> 日期：2026-05-03
> 参考：COGNIX 混合存储架构、fincat 现有架构
> 定位：金融智能客服，自进化、个性化、轻量化

---

## 一、核心理念

借鉴行业最佳实践的"Memory as File System"和 COGNIX 的"Markdown 是唯一真相源"，
形成 fincat 的升级记忆系统：

```
┌─────────────────────────────────────────────────────────────┐
│  Markdown 是智能体知识的唯一真实来源                          │
│  SQLite 是派生数据的加速层                                    │
│  记忆项带时间戳、衰减、趋势，支撑主动服务                     │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、最终存储架构

```
┌─────────────────────────────────────────────────────────────┐
│              精确数值 SQLite（直接计算）                      │
│  user_values: key, value, updated_at                        │
│  用途：风险评分、金额计算、产品参数、费率等                    │
│  特点：不存 Markdown，只有 agent 能读写                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│              Memory Items（原子记忆，带时间戳）               │
│  存储：JSONL（append-only） + SQLite 索引                     │
│  核心结构：item_id, timestamp, content, category,            │
│            source, frequency, decay_score, relevance         │
│  用途：记录事件、偏好、行为，支撑趋势分析和主动服务           │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              Memory Categories（分类 Markdown 文件）          │
│  memory/                                                    │
│  ├── user_preferences.md      用户偏好、风险偏好             │
│  ├── product_knowledge.md     产品知识、术语解释             │
│  ├── conversation_cases.md    客服案例、常见问题             │
│  ├── compliance_rules.md      合规规则、禁语列表             │
│  └── agent_reflections.md     反思教训、改进方向             │
│  特点：人类可读、可直接编辑、可独立迁移                       │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              SQLite 索引层（可丢弃，从 MD 重建）              │
│  memory_index: id, category, title, tags, md5_hash, path    │
│  item_index: item_id, category, timestamp, decay_score      │
│  用途：毫秒级检索 → 定位 Markdown → 按需注入 Context          │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、Memory Item 原子记忆模型（核心新增）

### 3.1 数据结构

```python
@dataclass
class MemoryItem:
    """原子记忆项 - 最小的、自包含的记忆单元"""
    item_id: str           # 唯一标识
    timestamp: datetime    # 精确发生时间
    
    # 内容
    content: str           # 自然语言陈述，如 "用户询问了风险评级低于R2的理财产品"
    category: str          # preference | knowledge | case | compliance
    
    # 溯源
    source_session: str    # 来源会话ID
    source_round: int      # 来源轮次
    
    # 频率与衰减
    frequency: int = 0     # 被引用的次数
    last_accessed: datetime | None = None
    decay_score: float = 1.0  # 衰减分数：1.0→0.0
    
    # 置信度
    confidence: float = 0.5  # 置信度随重复出现而升高
```

### 3.2 时间戳与频率衰减

| 机制 | 说明 |
|------|------|
| **精确时间戳** | 每个 MemoryItem 记录发生的确切时间 |
| **频率计数** | content 相同或相似的 Item 自动合并，frequency++ |
| **衰减模型** | 30 天未访问 → decay_score = 0.5，60 天 → 0.2，90 天 → 0.0 |
| **置信度提升** | 出现 1 次 → 0.5，3 次 → 0.7，5 次 → 0.9 |

### 3.3 衰减公式

```
decay_score = base_confidence * e^(-λ * days_since_last_access)

λ = ln(2) / HALF_LIFE_DAYS   # 默认 HALF_LIFE_DAYS = 30
```

---

## 四、主动服务引擎 — Trigger & Monitor（行业先进能力）

### 4.1 概述

主动服务引擎的核心能力是 **Trigger & Monitor**：
Memory Item 不只是被动存储的数据，而是**主动监控的数据源**。
Agent 不需要等用户问，而是**自己判断时机，主动提醒**。

### 4.2 触发器类型

| 触发器 | 说明 | 金融客服场景示例 |
|--------|------|----------------|
| **频率触发** | 同一内容出现 N 次 | 用户 3 次询问"理财产品安全性"→ 标记为高优先级 |
| **时间触发** | 某事件到达特定时间 | 用户每月 5 号查余额 → 5 号自动推送 |
| **阈值触发** | 数值超过阈值 | decay_score < 0.2 → 提示"该偏好是否还适用？" |
| **模式触发** | 行为模式变化 | 连续 3 轮情绪负面 → 主动转人工或安抚 |
| **事件触发** | 外部事件发生 | 新产品上线 → 通知关注该类产品的用户 |

### 4.3 Monitor 监控器

```python
class MemoryMonitor:
    """后台运行，周期性扫描 Memory Items，检测触发条件"""
    
    def scan():
        for item in items:
            if item.decay_score < 0.2:
                trigger("decay_warning", item)
            
            if item.frequency >= 3:
                trigger("high_frequency", item)
            
            if item.timestamp + timedelta(days=30) <= now:
                trigger("periodic_reminder", item)
```

### 4.4 Trigger 触发后的行为

| 级别 | 行为 |
|------|------|
| **提醒** | 主动给用户发消息 "您之前关注的理财产品有变更" |
| **确认** | 征求用户确认 "您的风险偏好还是 '低' 吗？" |
| **更新** | 自动更新 Memory Item 或 Category 文件 |
| **归档** | 将低衰减 Item 归档到历史 |

### 4.5 趋势分析

基于时间戳序列分析用户行为趋势：

```
提取所有 category="preference" 的 MemoryItem
按时间排序 → 分析变化趋势：
  - 风险偏好变化：保守 → 平衡
  - 产品关注转移：理财 → 基金
  - 情绪趋势：正面 → 犹豫 → 负面
```

### 4.6 Monitor Token 消耗分析（Q&A）

**关键设计：Monitor 不调 LLM，纯规则计算。**

| 触发器 | 是否需要 LLM | 说明 |
|--------|-------------|------|
| 频率触发 | ❌ | `frequency > 3` 纯数值比较 |
| 衰减触发 | ❌ | `decay_score < 0.2` 纯公式计算 |
| 时间触发 | ❌ | `timestamp + 30d < now` 纯时间比较 |
| 阈值触发 | ❌ | 纯数值比较 |
| 模式触发 | ⚠️ 仅此 | 每天运行 1 次批量分析，~500 token |

**每轮对话结束后的 Monitor 扫描**：

```
Monitor.scan() → 遍历 MemoryItems
  ├── check_rules()          → CPU 运算，0 token
  ├── check_decay()           → CPU 运算，0 token
  └── check_periodic()       → CPU 运算，0 token
```

**每天 1 次的趋势分析**：

```
Monitor.analyze_trends()     → 1 次 LLM 调用，~500 token
```

**结论**：日常运行 **0 token**，不存在大量消耗问题。

### 4.7 自由文本总结 vs 原子 MemoryItem（Q&A）

| 维度 | 自由文本总结 | 原子 MemoryItem |
|------|------------|----------------|
| 上下文丰富度 | ✅ 保留语义和关联 | ⚠️ 单一事实，可能丢失上下文 |
| 检索能力 | ❌ 只能全文匹配 | ✅ 可按 item_id/category/timestamp 精确查找 |
| 去重 | ❌ 同一事实可能写多次 | ✅ 相同 item 合并，frequency++ |
| 衰减管理 | ❌ 无法对单个事实独立衰减 | ✅ 每个 item 独立衰减 |
| 置信度追踪 | ❌ 无法量化可靠性 | ✅ confidence 随重复出现而升高 |
| 趋势分析 | ❌ 无法按时间线分析行为 | ✅ 时间戳序列 → 行为趋势 |
| 主动触发 | ❌ 无法监听特定事实 | ✅ Trigger 绑定到具体 item |
| Token 消耗 | 全量注入 | ⚠️ item 数量可能增多 |
| 写入成本 | 1 次 LLM 总结 | 1 次 LLM 提取（相同） |
| 人类可读 | ✅ 自然语言流畅 | ❌ 原子化后断裂感 |

**最终设计：两者共存，各司其职**：

```
Dream Phase 1:
  LLM 提取 MemoryItems（结构化 JSON，数据层）
  → 用于检索、监控、去重、衰减、趋势

Dream Phase 2:
  基于 MemoryItems，LLM 生成 Category Markdown（自然语言，表达层）
  → 人类可读、Agent 直接理解

Context 注入:
  优先注入 Category Markdown（自然语言，Token 省）
  + 按需查询 MemoryItems（精确匹配，回溯来源）
```

**互补关系**：

```
MemoryItem（数据层）          Category MD（表达层）
─────────────────────        ─────────────────────
检索精确       ←────────→    阅读流畅
去重合并                     语义完整
衰减独立                     上下文保留
趋势可分析                    Agent 友好
触发可监听                    人工可编辑
```

---

## 五、Dream 升级（从自由文本总结 → 原子项提取 + 分类写入）

### 5.1 现有 Dream 流程

```
Phase 1: LLM 分析 history.jsonl → 自由文本总结
Phase 2: AgentRunner + edit_file → 编辑 MEMORY.md（单文件）
```

## 五、Dream 升级（从自由文本总结 → 原子项提取 + 分类写入）

### 5.1 现有 Dream 流程

```
Phase 1: LLM 分析 history.jsonl → 自由文本总结
Phase 2: AgentRunner + edit_file → 编辑 MEMORY.md（单文件）
```

### 5.2 升级后 Dream 流程

```
Phase 1: 从 history.jsonl 提取 MemoryItems（原子项，带时间戳）
    ├── 识别事件类型（偏好变化、知识获取、投诉处理）
    ├── 去重合并（相同事实合并，frequency++）
    └── 写入 MemoryItems JSONL
    ↓
Phase 2: LLM 整理 MemoryItems → 写入分类 Category Markdown 文件
    ├── user_preferences.md  ← 偏好类 Item
    ├── product_knowledge.md ← 知识类 Item
    ├── conversation_cases.md ← 案例类 Item
    ├── compliance_rules.md  ← 合规类 Item
    └── agent_reflections.md ← 反思类 Item
    ↓
Phase 3（新增）: 同步 SQLite 索引
    └── 从 Markdown 重建索引，更新 md5_hash
```

### 5.3 Category Markdown 文件格式

```markdown
# 用户偏好

<!-- item:pref_001 | ts:2026-05-01T10:30 | freq:3 | decay:0.9 | conf:0.7 -->
- **风险偏好**: 保守型，偏向风险评级 R2 以下的产品

<!-- item:pref_002 | ts:2026-05-01T14:20 | freq:1 | decay:0.5 | conf:0.5 -->
- **关注产品**: 稳利盈、安心理财

<!-- item:pref_003 | ts:2026-05-02T09:15 | freq:2 | decay:0.8 | conf:0.6 -->
- **沟通偏好**: 喜欢简短回答，不喜欢专业术语
```

HTML 注释中包含元数据（item_id + 时间戳 + 频率 + 衰减 + 置信度），人类阅读时不可见，但解析工具可以提取。

---

## 六、上下文注入升级（按需检索，减少 Token）

### 6.1 当前方式

```
build_system_prompt():
    读取整个 MEMORY.md → 全量注入 System Prompt
```

### 6.2 升级方式

```
build_system_prompt():
    1. 从 SQLite 索引检索相关分类文件（关键词/向量匹配）
    2. 只注入相关分类的内容（而非全部）
    3. 先注入摘要（Memory Category 文件的前 5 行）
    4. 如果 LLM 需要更多 → tool call 展开
```

### 6.3 Token 节省

| 场景 | 当前 | 升级后 |
|------|------|--------|
| 简单问询 | 全量注入 | 只注入相关分类 |
| 深度咨询 | 全量注入 | 摘要 + 按需展开 |
| Token 消耗 | 100% | 预计降低 60-80% |

---

## 七、实现计划

### 阶段一：核心存储改造（P0，1 周）

| 任务 | 产出 |
|------|------|
| MemoryItem 数据模型 | `fincat/agent/memory_item.py` |
| Category Markdown 文件结构 | 5 个分类 MD 模板 |
| SQLite 索引表改造 | 新增 memory_index 表 |
| MemoryStore 扩展 | 支持多分类文件读写 |

### 阶段二：Dream 升级（P1，1 周）

| 任务 | 产出 |
|------|------|
| Dream Phase 1 改造 | 从自由文本 → 原子项提取 |
| Dream Phase 2 改造 | 写入分类 Category 文件 |
| Dream Phase 3 新增 | 同步 SQLite 索引 |

### 阶段三：主动服务引擎（P2，2 周）

| 任务 | 产出 |
|------|------|
| Trigger 系统 | 5 类触发器实现 |
| Monitor 后台扫描 | 周期性检测触发条件 |
| 衰减模型 | 时间衰减计算 |
| 趋势分析 | 基于时间戳的行为分析 |

### 阶段四：上下文注入优化（P3，1 周）

| 任务 | 产出 |
|------|------|
| 按需检索 | SQLite 索引快速定位 |
| 摘要优先 | 先摘要后展开 |
| ContextBuilder 改造 | 集成按需注入 |

---

## 八、新增文件清单

| 文件 | 用途 |
|------|------|
| `fincat/agent/memory_item.py` | MemoryItem 数据模型 + 衰减模型 |
| `fincat/agent/memory_monitor.py` | Trigger & Monitor 主动服务引擎 |
| `fincat/agent/memory_index.py` | SQLite 索引层（从 MD 派生）|
| `fincat/agent/memory_categories.py` | 分类 Markdown 文件管理 |
| `fincat/agent/memory_dream_v2.py` | 升级版 Dream 处理器 |

## 九、修改文件清单

| 文件 | 改动 |
|------|------|
| `fincat/agent/memory.py` | MemoryStore 扩展，支持多分类文件 |
| `fincat/agent/context.py` | 按需注入上下文 |
| `fincat/agent/loop.py` | 集成 Monitor 和 Dream v2 |
| `fincat/agent/memory_sqlite.py` | 索引表改造 |

---

## 十、关键指标

| 指标 | 优化前 | 优化后 |
|------|--------|--------|
| Token 消耗（每轮） | 全量注入 | 降低 60-80% |
| 记忆提取精度 | 自由文本（不可检索） | 原子项（可检索） |
| 主动服务能力 | 无 | 5 类触发器 |
| 记忆衰减管理 | 无 | 时间衰减 + 置信度 |
| 人类可读性 | 单文件 | 分类文件 |
| 数据可迁移性 | 手动复制 | 复制 MD 文件夹即可 |

---

## 十一、参考

- COGNIX 混合存储架构 — Markdown 真相源 + SQLite 索引
- fincat 现有架构 — MemoryStore + Dream + SQLiteMemoryStore
