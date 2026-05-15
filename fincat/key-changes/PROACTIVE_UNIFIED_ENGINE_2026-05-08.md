# 主动预测统一引擎设计方案（2026-05-08）

> 日期：2026-05-08
> 状态：设计完成，待实施
> 本文档记录主动预测系统的统一引擎设计方案，整合 MemoryMonitor（规则引擎）和 Cron Skills（LLM 执行引擎）为统一的四层架构。
> 关联文件：signal_store.py, proactive_skill.py, topic_store.py, topic_dispatcher.py, loop.py, commands.py, websocket.py, memory.py, dream_phase1.md, context.py

---

## 背景与问题

### 当前系统

finCat 有两套完全独立的主动预测系统：

| 系统 | 触发方式 | 输出目标 | 内容 |
|------|---------|---------|------|
| MemoryMonitor | 每轮对话后（CPU 规则） | `channel="cli"` | 简单文本提醒 |
| Cron Skills | 固定时间点（LLM+工具） | `channel="qq"` | 结构化内容（早报/新闻/行情） |

### 存在的问题

1. **数据源散落**：MemoryItem（items.jsonl）、category markdown（6个）、behavior_rhythm.md、PatternSnapshot、EventQueue 各自独立
2. **两套记忆体系**：behavior_rhythm.md 和 MemoryItem 来源相同（history.jsonl），但格式不同、存储不同
3. **无统一管理**：两套系统互不感知，无去重，无优先级
4. **单一推送**：消息只推送到单一 channel，WebSocket 前端收不到
5. **behavior_rhythm.md 无限增长**：无过期/归档机制
6. **外部事件丢弃**：EventQueue 事件用完即丢，无法回溯

---

## 核心设计决策

### 1. 第 7 个长期记忆：behavior_habits.md

**决策**：新增 `behavior_habits.md` 作为第 7 个 category markdown，由 Dream 每日生成。

**理由**：
- Dream 已在分析 history.jsonl，顺带提取行为模式成本极低
- behavior_rhythm.md 作为中间文件是多余的，其内容应转化为 MemoryItem
- 保留 behavior_habits.md 作为人类可读的行为档案

**Dream Phase 1 新增输出**：
```
[BEHAVIOR] 活跃时段 | 模式描述 | 稳定性: 稳定/下降/观察中
[BEHAVIOR] 周期行为 | 模式描述 | 周期: weekly/daily/monthly | 时间: 周几/几点
[BEHAVIOR] 行为链 | A → B | 频率: N次
```

**写入目标**：
- `memory/behavior_habits.md`（第 7 个 category markdown）
- `items.jsonl`（MemoryItem category="behavior"）

### 2. MemoryItem 统一所有信号

**决策**：扩展 MemoryItem 的 category，新增 `behavior` 和 `deadline` 类型，所有信号统一为 MemoryItem。

**理由**：
- MemoryItem 已有 frequency、decay_score、confidence 等属性，天然适合信号管理
- 不需要新增 BehaviorEvent 数据结构
- behavior_rhythm.md 的内容解析后写入 MemoryItemStore

**扩展后的 category**：
```
preference | knowledge | case | compliance | profile | insight
| behavior    ← 行为观察（活跃时段、周期性行为、行为链）
| deadline    ← 截止事项（华为机试 5/10 截止）
```

### 3. 自动 Cron 生成

**决策**：Dream 检测到稳定的周期性行为（连续 3 周以上）→ 推送询问用户 → 确认后创建 cron job。

**严格控制**：
- 最低置信度：连续 3 周以上才生成
- 最大数量：最多 3 个自动生成的 cron job
- 自动过期：连续 2 次用户无响应 → 自动取消
- 用户否决：说"不用提醒" → 立即取消 + 记录偏好
- 人工确认：首次生成时先推送询问

### 4. 紧急截止推送

**决策**：在定时分析时判断 `importance × 临近度`，只有重要且临近的才推送。

**规则**：
- importance="high" + 今天到期 → priority=3，紧急推送
- importance="high" + 3天内 → priority=2，高优先级
- importance="medium" + 今天到期 → priority=1，中优先级
- low importance → 不主动推送，只在面板中显示

### 5. Cron Skills 整合

**决策**：保留需要外部 API 的 Cron Skills，产出写入 TopicStore 统一分发。删除 behavior-sense（由 Dream 覆盖）。

**保留的 Cron Skills**：
| Skill | 时间 | 外部工具 | 说明 |
|-------|------|---------|------|
| daily-morning-brief | 08:30 | web_search | 早报 |
| news-market | 00:30/04:30/10:30 | stock_news | 市场新闻 |
| market-alert | 工作日 02/05/07:00 | stock_quote | 行情异动 |
| news-agent-tech | 00:30/10:30 | web_search | 技术动态 |

**删除的 Cron Skills**：
- behavior-sense-morning-night（由 Dream + behavior_habits.md 覆盖）
- behavior-sense-noon（由 Dream + behavior_habits.md 覆盖）

---

## 四层架构

```
┌─────────────────────────────────────────────────────────────┐
│  第 1 层：SignalStore（统一信号查询 + 事件缓存）              │
│  ─────────────────────────────────────────────              │
│  读取：MemoryItemStore + 7个category markdown + PatternSnapshot │
│  缓存：ExternalEvent（4小时TTL，最多100条）                  │
│  提供统一查询接口                                             │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  第 2 层：ProactiveSkill（分析决策，规则引擎，零 LLM）        │
│  ─────────────────────────────────────────────              │
│  5 个策略：紧急截止 · 高频关注 · 兴趣衰减 · 模式变化 · 定时   │
│  动态优先级（截止越临近/频率越高 → 优先级越高）               │
│  自动 Cron 候选检测                                           │
└──────────────────────────┬──────────────────────────────────┘
                           │ UnifiedTopic
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  第 3 层：TopicStore（缓冲 + 去重 + 送达追踪）               │
│  ─────────────────────────────────────────────              │
│  同时接收：ProactiveSkill 产出 + Cron Skills LLM 产出        │
│  去重 · 过期 · 持久化 (topics.jsonl)                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  第 4 层：TopicDispatcher（多 channel 分发，零 token）        │
│  ─────────────────────────────────────────────              │
│  每 30 秒轮询，分发到所有已启用 channel                       │
│  CLI · QQ · WebSocket · 未来新 channel                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 数据层全景

```
history.jsonl（对话历史）
    │
    ▼
Dream (每日 03:00, LLM)
    │
    ├→ 7 个 category markdown（长期记忆）
    │   ├── user_preferences.md    (preference)
    │   ├── product_knowledge.md   (knowledge)
    │   ├── conversation_cases.md  (case)
    │   ├── compliance_rules.md    (compliance)
    │   ├── user_profile.md        (profile)
    │   ├── behavioral_insights.md (insight)
    │   └── behavior_habits.md     (behavior) ← 新增第 7 个
    │
    ├→ items.jsonl（MemoryItemStore，所有信号的原子单元）
    │   category: preference | knowledge | case | compliance
    │            | profile | insight | behavior | deadline
    │
    └→ 自动生成 cron job（检测到稳定周期性行为时）
        ├── 用户确认后生效
        └── 连续无响应自动取消

PreFilter (每轮对话后, CPU)
    └→ category markdown + items.jsonl（高置信度直接写入）

EventQueue (每轮对话后轮询)
    └→ 外部事件 → SignalStore 缓存（4小时TTL）

PatternSnapshot (每日 03:30)
    └→ .pattern_snapshots.jsonl（items 分布快照）

Cron Skills (定时, LLM+工具, 保留需要外部API的)
    ├→ daily-morning-brief (08:30) → web_search → 早报
    ├→ news-market (00:30/04:30/10:30) → stock_news → 新闻
    ├→ market-alert (工作日 02/05/07:00) → stock_quote → 行情异动
    ├→ news-agent-tech (00:30/10:30) → web_search → 技术动态
    └→ 产出写入 TopicStore（统一删除 behavior-sense，由 Dream 覆盖）
```

---

## 第 1 层：SignalStore

**新建**: `fincat/agent/signal_store.py`（~120 行）

### 职责
1. 统一查询接口（只读，聚合现有数据）
2. 外部事件缓存（4 小时 TTL，最多 100 条）
3. behavior_habits.md 同步（解析后写入 MemoryItemStore）

### 查询接口

| 方法 | 数据来源 | 用途 |
|------|---------|------|
| `get_active_interests(min_decay)` | items.jsonl | 兴趣衰减检测 |
| `get_high_frequency_items(threshold)` | items.jsonl | 高频关注检测 |
| `get_deadlines(days, importance)` | items.jsonl (category="deadline") | 截止提醒 |
| `get_behavior_patterns()` | items.jsonl (category="behavior") | 行为模式 |
| `get_recent_events(hours)` | 内存缓存 | 外部事件回溯 |
| `get_entity_trends()` | .pattern_snapshots.jsonl | 模式变化 |
| `get_category_text(category)` | category markdown | 原始文本 |
| `get_auto_cron_candidates()` | items.jsonl (behavior, 稳定模式) | 自动 Cron 候选 |

### 不存储新数据
SignalStore 是只读查询层 + 事件缓存，不创建新存储，聚合现有 MemoryItemStore + category markdown + PatternSnapshot。

---

## 第 2 层：ProactiveSkill

**新建**: `fincat/agent/proactive_skill.py`（~150 行）

### 5 个分析策略

| 策略 | 检测条件 | 输出 | 优先级 |
|------|---------|------|--------|
| **紧急截止** | deadline + importance + days_left | "⚠️ XX 今日到期" | 0-3 (动态) |
| **高频关注** | frequency >= 3 | "🔥 你 N 次提到 XX" | 1-3 (动态) |
| **兴趣衰减** | decay < 0.3, freq >= 2 | "❓ 还关注吗" | 1 |
| **模式变化** | PatternSnapshot 偏移 >20% | "📊 行为模式变化" | 1 |
| **定时情境** | 早(8点)/晚(22点) | "🌅 今日待办" | 2 |

### 动态优先级

**截止日期**：
- 今天到期 + importance=high → priority=3
- 明天到期 + importance=high → priority=3
- 3天内 + importance=high → priority=2
- 今天到期 + importance=medium → priority=1
- low importance → 不推送

**高频关注**：
- frequency >= 10 → priority=3
- frequency >= 5 → priority=2
- frequency >= 3 → priority=1

### 自动 Cron 生成

Dream 检测到稳定周期性行为 → ProactiveSkill 检测候选 → 推送询问用户 → 确认后创建 cron

**严格控制**：连续 3 周才生成，最多 3 个，连续 2 次无响应自动取消。

---

## 第 3 层：TopicStore

**新建**: `fincat/agent/topic_store.py`（~100 行）

### UnifiedTopic 数据结构

```python
@dataclass
class UnifiedTopic:
    topic_id: str
    source: str              # "proactive" | "skill" | "event"
    source_name: str         # "deadline" | "high_frequency" | "market-alert" | ...
    category: str            # "alert" | "news" | "reminder" | "insight"
    title: str
    content: str
    priority: int            # 0-3
    entities: list[str]
    created_at: datetime
    expires_at: datetime
    delivered: dict[str, datetime]  # channel → delivered_at
    metadata: dict[str, Any]
```

### 去重策略
同 source_name + 相似 title（token overlap > 0.7）+ 1 小时内 = 重复

### 过期策略
- alert: 24 小时
- news: 48 小时
- reminder: 截止日期后
- insight: 7 天

### 持久化
`workspace/memory/topics.jsonl`

---

## 第 4 层：TopicDispatcher

**新建**: `fincat/agent/topic_dispatcher.py`（~60 行）

- 每 30 秒轮询（零 token 消耗）
- 遍历所有已启用 channel
- 分发未送达话题
- WebSocket 发送结构化 `topics` 事件
- 其他 channel 发送文本消息

---

## 触发时机

| 触发点 | 调用 | 频率 | 说明 |
|--------|------|------|------|
| `loop.py _monitor_scan()` | `signals.sync_behavior_rhythm()` + `skill.analyze("realtime")` | 每轮对话后 | 实时检测 |
| 新增 cron `proactive-analysis` | `skill.analyze("scheduled")` | 每天 08/12/22 时 | 定时检查 |
| 新增 cron `auto-cron-check` | `skill._check_auto_cron()` | 每天 03:30 | 检测新周期行为 |
| `commands.py on_cron_job()` | Cron Skill 产出写入 TopicStore | 每次 skill 执行后 | LLM 产出 |
| `loop.py _monitor_scan()` | 外部事件写入缓存 + TopicStore | 每轮对话后 | 事件驱动 |
| `websocket.py` | `skill.analyze("demand")` | 用户请求时 | 按需分析 |

---

## Dream 改造

### Phase 1 Prompt 修改

**文件**: `fincat/templates/agent/dream_phase1.md`

新增 behavior 提取指令：

```markdown
## 行为模式提取

除了上述 6 个分类，你还需要从对话历史中提取用户的行为模式，输出到 behavior 分类：

### 提取内容
1. **活跃时段**：用户通常在什么时间段活跃
2. **周期性行为**：是否有固定模式（每周五写周报、月初查理财）
3. **行为链**：A 之后通常做 B（看完金价看新闻）
4. **学习轨迹**：知识/兴趣的演变趋势

### 输出格式
[BEHAVIOR] 活跃时段 | 模式描述 | 稳定性: 稳定/下降/观察中
[BEHAVIOR] 周期行为 | 模式描述 | 周期: weekly/daily/monthly | 时间: 周几/几点
[BEHAVIOR] 行为链 | A → B | 频率: N次
```

### Phase 2 修改

**文件**: `fincat/agent/memory.py`

在 Phase 2 中，将 [BEHAVIOR] 输出写入：
- `memory/behavior_habits.md`（第 7 个 category markdown）
- `items.jsonl`（MemoryItem category="behavior"）

### context.py 修改

**文件**: `fincat/agent/context.py`

在 system prompt 中新增 behavior 分类：
```
memory/behavior_habits.md
```

---

## 过期/归档策略

| 数据 | 过期条件 | 处理 |
|------|---------|------|
| MemoryItem (behavior) | 30天无更新 | decay 自动衰减 |
| MemoryItem (deadline) | 过期后 7 天 | 归档到 archive.jsonl |
| MemoryItem (其他) | decay < 0.2 | 归档到 archive.jsonl |
| ExternalEvent 缓存 | 4 小时或超过 100 条 | 自动丢弃 |
| PatternSnapshot | 超过 5 个 | 旧的丢弃 |
| TopicStore 话题 | expires_at 到期 | 自动清理 |
| 自动生成 cron | 连续 2 次无响应 | 自动取消 |

---

## 文件清单

| 操作 | 文件 | 行数 | 说明 |
|------|------|------|------|
| **新建** | `fincat/agent/signal_store.py` | ~120 | 统一信号查询 + 事件缓存 |
| **新建** | `fincat/agent/proactive_skill.py` | ~150 | 5 策略 + 自动 Cron 检测 |
| **新建** | `fincat/agent/topic_store.py` | ~100 | UnifiedTopic + TopicStore |
| **新建** | `fincat/agent/topic_dispatcher.py` | ~60 | 多 channel 分发 |
| **修改** | `fincat/agent/loop.py` | ~15 | 初始化 + _monitor_scan 调用 |
| **修改** | `fincat/cli/commands.py` | ~25 | Cron 产出写入 + 新增 cron job + 删除 behavior-sense |
| **修改** | `fincat/channels/websocket.py` | ~25 | broadcast_topics + request_topics |
| **修改** | `fincat/agent/memory.py` | ~30 | Dream Phase 2 新增 behavior 写入 |
| **修改** | `fincat/templates/agent/dream_phase1.md` | ~20 | 新增 behavior 提取 prompt |
| **修改** | `fincat/agent/context.py` | ~5 | 新增 behavior 分类到 system prompt |
| **新建** | `tests/agent/test_signal_store.py` | ~80 | 测试 |
| **新建** | `tests/agent/test_proactive_skill.py` | ~80 | 测试 |
| **新建** | `tests/agent/test_topic_store.py` | ~60 | 测试 |

**总计**：~430 行新增 + ~120 行修改 + ~220 行测试

---

## 和现有系统的关系

```
PreFilter ──→ MemoryItemStore ──→ SignalStore ──→ ProactiveSkill ──→ TopicStore
Dream ──→ 7 个 category markdown ──┘                                         ↑
                                                                              │
EventQueue ──→ SignalStore 缓存 ──→ TopicStore ─────────────────────────────┘
                                                                              ↑
Cron Skills (早报/新闻/行情) ──→ LLM 产出 ──────────────────────────────────┘
                                                                              │
                                                                       TopicDispatcher
                                                                          │  │  │
                                                                        CLI QQ WS
```

**现有组件改动**：
- MemoryItemStore：新增 category 类型（behavior/deadline），不改核心逻辑
- MemoryStore：新增 behavior_habits.md 分类
- MemoryMonitor：输出从直接发消息改为写入 SignalStore
- PatternSnapshotStore：不改，直接复用
- EventQueue：输出从丢弃改为写入 SignalStore 缓存
- behavior_rhythm.md：不改格式，新增解析方法转为 MemoryItem
- Cron Skills：不改执行逻辑，产出额外写入 TopicStore
- WebSocket channel：新增 broadcast_topics + request_topics

---

## 后续可扩展

1. **前端侧边栏**：监听 WebSocket `topics` 事件，展示话题卡片
2. **用户反馈循环**：记录用户对推送的响应（点击/忽略/关闭），优化推送策略
3. **行为模式细化**：behavior_habits.md 的结构化程度可以进一步提升
4. **智能推送时机**：基于用户活跃时段自动选择最佳推送时间
