# fincat 记忆系统重构 — 完整计划

## Context

### 当前架构问题

**数据层缺失**：
- 没有统一的原始数据层。session jsonl 分散在 50+ 文件中，无统一索引
- history.jsonl 是 LLM 压缩摘要（31条/18K），丢失原始时间戳和细节
- MemoryItem 没有 `resource_id` 字段，无法追溯到原始对话

**主动推送系统冗余**：
- 每条消息后跑 ProactiveSkill，大部分产生 0 个 topic
- SignalStore 是多余的中间层
- MemoryMonitor 和 ProactiveSkill 功能重叠
- PreFilter 关键词匹配太宽泛，83% 的 items.jsonl 数据来自 PreFilter（大部分是垃圾）

**行为检测缺失**：
- MemoryItem 只存 `frequency`（标量计数），不存时间序列
- 无法检测"每周五下午关注黄金"这类周期性行为
- 所有行为模式检测完全依赖 Dream LLM 猜测，不可靠

### 当前数据流全景

```
用户对话
  → session jsonl（原始数据，50+文件，~5MB）
  → PreFilter（关键词匹配，零LLM成本）→ items.jsonl（568条，236K）
  → Consolidator（token超限时LLM压缩）→ history.jsonl（31条，18K，丢失时间戳）
  → Dream（每日03:00，LLM批量提取）→ category markdown（7个md文件）
  → ProactiveSkill（每条消息后运行）→ topics.jsonl（大部分是垃圾）
```

### 现有存储规模

| 文件 | 位置 | 记录数 | 大小 | 删除策略 |
|------|------|--------|------|----------|
| session jsonl | `~/.fincat/workspace/sessions/` | ~50文件 | ~5MB | 不删除 |
| history.jsonl | `~/.fincat/workspace/memory/` | 31条 | 18K | 超1000条截断 |
| items.jsonl | `~/.fincat/workspace/memory/` | 568条 | 236K | 不删除（低decay归档） |
| category md | `~/.fincat/workspace/memory/` | 7文件 | ~50K | 不删除 |
| memory.db | `~/.fincat/workspace/.fincat/` | 空 | 44MB | — |
| knowledge.db | `~/.fincat/` | 102文档/4795切片 | 32MB | — |

---

## 新架构：四层记忆体系

```
┌─────────────────────────────────────────────────────────────┐
│                    L3: 表达层 (Expression)                    │
│  memory.md ← 汇总摘要，注入 system prompt（<500字）          │
│  Category 目录（profile/knowledge/preferences/...）           │
│  ├─ 5个内置 + 自定义 + 归档                                  │
│  └─ 每个 Category = YAML frontmatter + 结构化 md             │
└──────────────────────────┬──────────────────────────────────┘
                           │ Category Manager 维护
┌──────────────────────────┴──────────────────────────────────┐
│                    L2: 记忆层 (Memory)                        │
│  memory.db / memory_item 表 ← 结构化属性（SQLite）            │
│  vector/item_vectors.index ← 嵌入向量（FAISS）                │
│  ├─ resource_id ← 关联原始资源                                │
│  ├─ 三级提取：即时 / 增量批量 / 兜底                          │
│  └─ 向量去重（相似度 > 0.9 跳过）                             │
│                                                              │
│  主动预测系统                                                │
│  ├─ 实时轻量预测：规则匹配 + 向量相似度（零 LLM）            │
│  ├─ 深度模式挖掘：时间周期性 + 语义关联 + 实体关联           │
│  └─ Topic 缓存（Top 3）→ 前端推送                            │
└──────────────────────────┬──────────────────────────────────┘
                           │ 批量提取引擎（LLM）
┌──────────────────────────┴──────────────────────────────────┐
│                    L1: 原始资源层 (Resources)                  │
│  resources/conversations.jsonl ← 每轮对话原始记录            │
│  resources/interaction_logs.jsonl ← 用户交互行为日志         │
│  ├─ resource_id ← 稳定标识                                   │
│  ├─ content_hash ← SHA256去重                                │
│  └─ related_item_ids ← 关联 MemoryItem                      │
└──────────────────────────┬──────────────────────────────────┘
                           │ LLM 输出时自动保存
┌──────────────────────────┴──────────────────────────────────┐
│                    L0: 会话层 (Session)                       │
│  session jsonl ← 当前工作缓冲（已有）                        │
│  └─ 会话结束后数据已在 L1 持久化                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 一、新增 L1 原始资源层 — Resources

### 1.1 目录结构

```
C:\Users\lyw\.fincat\resources\
  ├── conversations.jsonl      # 对话原始记录（替代原 session jsonl 的持久化角色）
  └── interaction_logs.jsonl   # 用户交互行为日志（元数据，非内容）
```

### 1.2 conversations.jsonl — 对话原始记录

原 session jsonl 分散在 50+ 文件中，无统一索引，格式为每会话一个文件。现统一为 `conversations.jsonl`，**每轮对话**（user → assistant + tools）一条记录，格式如下：

```json
{
  "resource_id": "res_a1b2c3d4",
  "type": "conversation",
  "content": "用户：茅台走势咋样\nAI：当前贵州茅台(600519)收盘价1850.00元...",
  "content_hash": "sha256:a1b2c3d4e5f6...",
  "metadata": {
    "timestamp": "2026-05-11T14:30:00+08:00",
    "source": "cli",
    "session_id": "cli_direct",
    "message_id": "msg_abc123",
    "turn_index": 5
  },
  "related_item_ids": ["item_abc123", "item_def456"]
}
```

字段说明：
- `resource_id`：稳定标识，`res_{uuid4().hex[:8]}`
- `content`：该轮完整对话内容（用户问题 + AI回复 + 工具调用结果），原始文本
- `content_hash`：SHA256，用于去重和完整性校验
- `metadata.timestamp`：用户发消息的原始时间
- `metadata.source`：来源渠道（cli/websocket/web）
- `metadata.session_id`：会话标识
- `metadata.message_id`：该轮消息的唯一 ID（供 interaction_logs 引用）
- `metadata.turn_index`：该轮在 session 中的序号
- `related_item_ids`：该轮对话产生的 MemoryItem ID 列表

保存时机：agent loop 完成后、返回响应前，自动写入。

### 1.3 interaction_logs.jsonl — 用户交互行为日志

记录用户和系统的**所有操作行为的元数据**，不是记录内容本身（内容存在 conversations.jsonl）。分三类：

#### （1）对话交互行为

记录用户在对话中的操作：

```json
{
  "log_id": "log_x1y2z3",
  "category": "conversation_interaction",
  "timestamp": "2026-05-11T14:30:00+08:00",
  "user_id": "user001",
  "session_id": "cli_direct",
  "action": "send_message",
  "duration_ms": 0,
  "message_id": "msg_abc123",
  "resource_id": "res_a1b2c3d4"
}
```

`action` 枚举值：
| action | 说明 | 触发时机 |
|--------|------|----------|
| `send_message` | 用户发送消息 | InputBar.onSend |
| `like_reply` | 点赞回复 | MessageBubble 点赞按钮 |
| `dislike_reply` | 点踩回复 | MessageBubble 点踩按钮 |
| `copy_reply` | 复制回复内容 | MessageBubble 复制按钮 |
| `interrupt_reply` | 中断 AI 回复 | InputBar 停止按钮 |
| `retry_message` | 重试消息 | MessageBubble 重试按钮 |

`duration_ms`：对于 `send_message` 为 0；对于 AI 回复相关操作，记录从消息发出到操作的时间差。

#### （2）记忆管理行为

记录用户对记忆系统的操作：

```json
{
  "log_id": "log_m4n5o6",
  "category": "memory_management",
  "timestamp": "2026-05-08T10:00:00+08:00",
  "user_id": "user001",
  "session_id": "cli_direct",
  "action": "view_memory_source",
  "item_id": "item_abc123",
  "category_id": "knowledge",
  "metadata": {}
}
```

`action` 枚举值：
| action | 说明 | 关联字段 |
|--------|------|----------|
| `view_memory_list` | 查看记忆列表 | — |
| `modify_memory` | 修改记忆内容 | `item_id` |
| `delete_memory` | 删除记忆 | `item_id` |
| `view_memory_source` | 点击查看记忆来源 | `item_id` → 回溯 resource_id |
| `export_memory` | 导出记忆 | `category_id` |
| `search_memory` | 搜索记忆 | `metadata.query` |

#### （3）系统操作行为

记录用户在系统层面的操作：

```json
{
  "log_id": "log_s7t8u9",
  "category": "system_operation",
  "timestamp": "2026-05-08T09:00:02+08:00",
  "user_id": "user001",
  "session_id": "cli_direct",
  "action": "click_push_notification",
  "module": "proactive_push",
  "duration_seconds": 120,
  "metadata": {
    "topic_id": "topic_xyz",
    "push_type": "weather_alert",
    "response": "clicked"
  }
}
```

`action` 枚举值：
| action | 说明 | 关联字段 |
|--------|------|----------|
| `login` | 登录 | `metadata.device` |
| `logout` | 登出 | `duration_seconds`（本次会话时长） |
| `switch_module` | 切换功能模块 | `module` |
| `click_push_notification` | 点击主动推送 | `metadata.topic_id`, `metadata.push_type` |
| `ignore_push_notification` | 忽略主动推送 | `metadata.topic_id` |
| `reject_push_notification` | 拒绝主动推送 | `metadata.topic_id` |

示例：`2026-05-08 09:00:02，用户点击了主动推送的天气通知` → `action: "click_push_notification"`, `push_type: "weather_alert"`

### 1.4 大小估算

| 文件 | 每天新增 | 每月 | 每年 |
|------|---------|------|------|
| conversations.jsonl | ~10-20轮 × 1-3KB = ~20-60KB | ~600KB-1.8MB | ~7-22MB |
| interaction_logs.jsonl | ~50-100条 × ~0.3KB = ~15-30KB | ~450KB-900KB | ~5-11MB |
| **合计** | ~35-90KB | ~1-2.7MB | ~12-33MB |

**结论**：纯文本 JSONL，一年约 12-33MB，完全可接受，不需要删减。建议按月归档（`conversations_2026_05.jsonl`），超过 6 个月的文件压缩存储。

### 1.5 与 session jsonl 的关系

原 session jsonl（`~/.fincat/workspace/sessions/*.jsonl`）将被 `conversations.jsonl` 替代：

| 阶段 | 说明 |
|------|------|
| 过渡期 | 两者并存，conversations.jsonl 写入新数据，session jsonl 维持现有会话工作流 |
| 稳定后 | session jsonl 降为纯工作缓冲（当前会话的 LLM 上下文），会话结束后数据已在 conversations.jsonl 持久化 |
| 最终 | session jsonl 可选清理，conversations.jsonl 成为唯一原始数据源 |

### 1.6 保存时机

**conversations.jsonl**：在 `AgentLoop._process_message()` 中，agent loop 完成后、返回响应前：
```
1. 用户消息到达
2. 生成 resource_id = f"res_{uuid4().hex[:8]}"
3. 运行 agent loop（LLM + 工具调用）
4. 保存 conversation → conversations.jsonl（含 related_item_ids）
5. 触发批量提取引擎（P2 增量批量）
6. 返回响应
```

**interaction_logs.jsonl**：在各操作触发点实时写入：
- 对话交互：`_process_message()` 结束时（send_message）、前端事件回调（like/dislike/copy/interrupt）
- 记忆管理：前端记忆管理页面操作回调
- 系统操作：登录/登出中间件、推送点击事件

### 1.7 双向关联机制

| 方向 | 字段 | 用途 |
|------|------|------|
| MemoryItem → resource | `item.resource_id` | 追溯记忆来源 |
| resource → MemoryItem[] | `resource.related_item_ids` | 查看某轮对话产生了哪些记忆 |
| interaction_log → resource | `log.resource_id` | 关联交互行为到具体对话 |
| interaction_log → message | `log.message_id` | 关联交互行为到具体消息 |

### 1.8 需要修改的文件

| 操作 | 文件 | 改动 |
|------|------|------|
| **新建** | `fincat/agent/resource_store.py` | `ResourceStore` 类：conversations + interaction_logs 的读写、查询、按月归档 |
| **修改** | `fincat/agent/loop.py` | `_process_message()` 中集成 ResourceStore：生成 resource_id → 保存 conversation → 写入 send_message 日志 |
| **修改** | `fincat/config/paths.py` | 新增 `get_resources_dir() -> Path` |
| **新建** | `tests/test_resource_store.py` | ResourceStore 单元测试 |

**ResourceStore 类接口**：
```python
class ResourceStore:
    def __init__(self, resources_dir: Path): ...
    def add_conversation(self, content: str, metadata: dict, related_item_ids: list[str]) -> str:  # 返回 resource_id
    def add_interaction_log(self, category: str, action: str, metadata: dict) -> str:  # 返回 log_id
    def get_by_resource_id(self, resource_id: str) -> dict | None: ...
    def query_by_session(self, session_id: str) -> list[dict]: ...
    def query_by_timerange(self, start: str, end: str) -> list[dict]: ...
    def _rotate_if_needed(self) -> None:  # 按月归档
```

### 1.9 详细修改清单

#### 1.9.1 新建 `fincat/agent/resource_store.py`

**新建类 `ResourceStore`**：

```python
class ResourceStore:
    """L1 原始资源层：conversations + interaction_logs 的读写、查询"""

    def __init__(self, resources_dir: Path):
        """初始化，确保目录和文件存在"""
        self._dir = resources_dir
        self._conv_path = resources_dir / "conversations.jsonl"
        self._log_path = resources_dir / "interaction_logs.jsonl"

    # --- 写入 ---
    def add_conversation(self, content: str, metadata: dict, related_item_ids: list[str]) -> str:
        """写入一条对话记录，返回 resource_id。自动计算 content_hash。"""

    def add_interaction_log(self, category: str, action: str, metadata: dict) -> str:
        """写入一条交互日志，返回 log_id。category: conversation_interaction/memory_management/system_operation"""

    # --- 查询 ---
    def get_by_resource_id(self, resource_id: str) -> dict | None:
        """按 resource_id 精确查找（全量扫描，数据量小时可接受）"""

    def query_by_session(self, session_id: str) -> list[dict]:
        """按 session_id 查询所有对话记录"""

    def query_by_timerange(self, start: str, end: str) -> list[dict]:
        """按时间范围查询对话记录"""

    def query_logs(self, category: str = None, action: str = None, limit: int = 100) -> list[dict]:
        """查询交互日志，支持按 category/action 过滤"""

    def read_recent_conversations(self, hours: int = 24) -> list[dict]:
        """读取最近 N 小时的对话记录（供 PatternMiner 使用）"""

    def read_recent_logs(self, hours: int = 24) -> list[dict]:
        """读取最近 N 小时的交互日志（供 PatternMiner 使用）"""

    # --- 维护 ---
    def _rotate_if_needed(self) -> None:
        """按月归档：文件名加 _YYYY_MM 后缀，创建新文件"""
```

#### 1.9.2 修改 `fincat/agent/loop.py` — `AgentLoop` 类

**新增导入**：
```python
from fincat.agent.resource_store import ResourceStore
```

**`__init__()` (line 151) — 新增初始化**：
```python
# 在 __init__ 中新增
self._resource_store = ResourceStore(get_resources_dir())
```

**`_process_message()` (line 890) — 插入 Resource 保存逻辑**：

在 agent loop 完成后、返回响应前（约 line 1050 附近，`_save_turn()` 之后），新增：
```python
# --- L1 Resource 落盘 ---
resource_id = self._resource_store.add_conversation(
    content=f"用户：{user_text}\nAI：{final_content}",
    metadata={
        "timestamp": msg.timestamp,
        "source": channel,
        "session_id": session_key,
        "message_id": msg.id,
        "turn_index": len(session.messages),
    },
    related_item_ids=[],  # 后续由 BatchExtractor 回填
)
self._resource_store.add_interaction_log(
    category="conversation_interaction",
    action="send_message",
    metadata={"message_id": msg.id, "resource_id": resource_id},
)
```

**`_monitor_scan()` (line 828) — 移除 ProactiveSkill 调用**：
```python
# 删除这一行（line 854）：
self._proactive_skill.analyze("realtime")
```

#### 1.9.3 修改 `fincat/config/paths.py` — 新增路径函数

**新增函数**：
```python
def get_resources_dir() -> Path:
    """返回 ~/.fincat/resources/ 目录"""
    return ensure_dir(get_data_dir() / "resources")
```

---

## 二、MemoryItem 重构 — 结构化存储 + 向量索引

### 2.1 核心存储结构（两部分分离存储）

| 部分 | 存储介质 | 路径 | 用途 |
|------|---------|------|------|
| Item 结构化属性 | SQLite `memory_item` 表 | `~/.fincat/memory.db` | 存储除向量外的所有元数据，支持条件查询/关联/更新 |
| Item 嵌入向量 | FAISS 索引 | `~/.fincat/vector/item_vectors.index` | 语义相似度检索 |
| 向量↔Item映射 | SQLite `vector_mapping` 表 | `~/.fincat/memory.db` | 索引序号与 item_id 的映射 |

### 2.2 SQLite 表结构

```sql
-- 原始对话记录（conversations.jsonl 的 SQLite 镜像，供 FK 引用）
CREATE TABLE conversations (
    resource_id TEXT PRIMARY KEY,
    content_hash TEXT,
    timestamp TEXT NOT NULL,
    session_id TEXT,
    message_id TEXT
);

-- Item 结构化属性
CREATE TABLE memory_item (
    item_id         TEXT PRIMARY KEY,
    resource_id     TEXT,
    category_id     TEXT,
    memory_type     TEXT NOT NULL,
    summary         TEXT NOT NULL,
    content         TEXT,
    importance_score REAL DEFAULT 0.5,
    entities        TEXT DEFAULT '[]',
    tags            TEXT DEFAULT '[]',
    created_at      TEXT NOT NULL,          -- Item 创建时间（LLM 提取完成写入时）
    embedded_at     TEXT,                    -- 向量嵌入生成时间（FAISS 索引写入时）
    last_accessed_at TEXT,
    access_count    INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    extra           TEXT DEFAULT '{}',
    FOREIGN KEY (resource_id) REFERENCES conversations(resource_id)
);

-- Item 向量映射
CREATE TABLE vector_mapping (
    faiss_index  INTEGER PRIMARY KEY,
    item_id      TEXT NOT NULL UNIQUE,
    summary_hash TEXT,
    embedded_at  TEXT NOT NULL,              -- 向量生成时间（与 memory_item.embedded_at 一致）
    model_name   TEXT NOT NULL DEFAULT 'bge-small-zh-v1.5',  -- 嵌入模型标识（模型变更时用于判断是否需要重建）
    FOREIGN KEY (item_id) REFERENCES memory_item(item_id)
);

CREATE INDEX idx_memory_item_resource ON memory_item(resource_id);
CREATE INDEX idx_memory_item_category ON memory_item(category_id);
CREATE INDEX idx_memory_item_type ON memory_item(memory_type);
CREATE INDEX idx_memory_item_active ON memory_item(is_active);
```

`memory_type` 枚举值（固定，不可自定义）：
| 类型 | 说明 | 示例 |
|------|------|------|
| `preference` | 用户偏好 | "喜欢冲浪"、"不吃海鲜" |
| `fact` | 事实信息 | "身份证号 xxx"、"预算 5000" |
| `knowledge` | 知识类 | "三亚 5 月是旺季" |
| `event` | 事件/计划 | "下周去三亚玩 3 天" |
| `goal` | 目标/愿望 | "想学潜水" |
| `behavior` | 行为模式 | "每周五关注黄金" |

### 2.3 Embedding 模型与向量索引配置

**Embedding 模型**：`bge-small-zh-v1.5`（BAAI 发布，512 维，中文语义检索专优）

| 指标 | 值 |
|------|-----|
| 维度 | 512 |
| 模型大小 | ~93MB |
| 延迟 | ~30ms（CPU） |
| 成本 | 零（本地推理） |
| 质量 | 中文 MTEB 排名前列，优于 text2vec-base-chinese |

**FAISS 向量索引配置**：

```yaml
vector_config:
  provider: "faiss"
  index_type: "Flat"          # 记忆量 <1万条时用 Flat，100% 准确率，速度够用
  # index_type: "IVF1024,Flat"  # 记忆量 >1万条时切换 IVF，检索速度更快
  metric_type: "cosine"       # 余弦相似度，最适合语义检索
  quantization: null          # <1万条不启用量化；>1万条可开启 PQ8（内存减 75%，准确率损失 <1%）
  dimension: 512              # 与 bge-small-zh-v1.5 一致
  index_path: "~/.fincat/vector/item_vectors.index"
```

**索引选择策略**：
| 记忆量 | index_type | quantization | 说明 |
|--------|-----------|-------------|------|
| <1万条 | `Flat` | 不启用 | 100% 准确率，暴力搜索速度完全够用 |
| 1万~10万条 | `IVF1024,Flat` | 不启用 | 倒排索引加速，训练数据需 ≥1000 条 |
| >10万条 | `IVF1024,Flat` | `PQ8` | 8 位量化，内存减少 75%，准确率损失 <1% |

**注意**：IVF 索引需要先训练（`faiss.train()`），切换索引类型时需从 SQLite 重建。`rebuild_faiss_index()` 方法需支持两种索引类型的自动切换。

### 2.4 Item 建立触发时机

对比原 history.jsonl 的触发方式（仅 Consolidator token 超限时 + Dream 每日 03:00）：

| 优先级 | 触发场景 | 延迟 | 覆盖率 |
|--------|---------|------|--------|
| **P1 即时** | 用户显式要求记住（"把这个存下来"）；系统检测到高价值敏感信息（身份证/手机号/地址/账号/重要待办/核心偏好变更）；用户上传文件后手动点击"提取记忆" | 10秒内 | ~5% |
| **P2 增量批量** | 待提取缓存池满 N 条新 Resource；距离上次批量提取超过 1 分钟（哪怕池没满也触发）；所有普通对话场景默认走此流程 | 1~3分钟 | ~90% |
| **P3 兜底** | 会话结束（用户 15 分钟无操作 / 手动结束）；凌晨空闲时段自动处理之前提取失败的 Resource | 会话结束时 | ~5%（兜底） |

### 2.5 Item 完整建立流程

以用户发「下周我和女朋友去三亚玩3天，预算5000，喜欢冲浪，不吃海鲜」为例：

```
第一步：Resource 落盘
  用户消息实时写入 conversations.jsonl，生成 resource_id: res_xyz789
  进入待提取缓存池

第二步：触发批量提取
  缓存池满 N 条 → 后台调用 LLM 提取 Item
  提取 Prompt 模板：batch_extract.md
  要求 LLM：「拆分独立语义点，去重，标注 memory_type，输出 JSON」

第三步：LLM 返回结果
  返回 5 个独立语义点：
  ① event: "下周和女朋友去三亚玩3天"
  ② fact: "旅行预算5000元"
  ③ preference: "喜欢冲浪"
  ④ preference: "不吃海鲜"
  ⑤ fact: "同行人：女朋友"

第四步：去重校验
  对每个语义点的 summary 生成 embedding
  与已有 Item 向量比对（FAISS cosine 检索），相似度 > 0.9 的跳过
  匹配到的已有 Item：更新 last_accessed_at 和 access_count

第五步：生成结构化 Item
  给每个新语义点生成唯一 item_id
  填充所有字段：memory_type, summary, content, importance_score, entities, tags
  设置 created_at = 当前时间（Item 创建时间）

第六步：写入数据库
  原子写入 SQLite memory_item 表（此时 embedded_at = NULL，向量尚未生成）

第七步：生成向量
  用 summary 字段调用 bge-small-zh-v1.5 生成 512 维向量
  写入 FAISS 索引，更新 vector_mapping 表（含 embedded_at、model_name）
  回填 memory_item.embedded_at = 当前时间
  注意：created_at 和 embedded_at 通常相差毫秒级，但分开记录便于：
  - 向量重建时判断哪些 Item 需要重新嵌入
  - 模型升级时（如从 bge-small 升级到 bge-base）批量重建向量

第八步：关联 Category
  匹配所属 Category（5 个固定 + 自定义 + 归档）
  设置 category_id，更新 Category 的摘要

第九步：同步更新 MD 文件
  按固定格式写入对应 category 的 .md 文件
  重新生成 memory.md 汇总摘要

第十步：完成通知
  用户主动要求 → 通知「已为你提取 5 条记忆」
  普通场景 → 完全静默
```

### 2.6 去重/更新/删除/重试规则

**去重规则**：
- 相同语义的 Item 只存一份（向量相似度 > 0.9 视为相同）
- 匹配到已有 Item 时，只更新 `last_accessed_at` 和 `access_count`，不创建新记录

**更新规则**：
- 用户修改 MD 文件中的 Item 内容 → 自动重新生成向量 → 更新关联的 Category 摘要
- 用户在 UI 修改 Item → 同上

**删除规则**：
- 删除 MD 中的 Item 条目 → `is_active` 设为 false，移动到未分类（归档）
- 不物理删除，可恢复

**失败重试**：
- 提取失败的 Resource 自动重试 3 次，间隔 1/5/10 分钟
- 都失败 → 凌晨空闲时段再重试
- 不会丢失任何 Resource

### 2.7 与原 items.jsonl 的对比

| 维度 | 原 items.jsonl (MemoryItemStore) | 新 memory_item 表 |
|------|--------------------------------|-------------------|
| 存储 | JSONL 追加文件 | SQLite 关系表 |
| 去重 | token 相似度 0.8 | 向量相似度 0.9 |
| 字段 | 10 个（无 entities/tags/extra） | 15 个（完整结构化） |
| 类型 | 7 个 category（部分重叠） | 6 个 memory_type（正交） |
| 查询 | 全量加载到内存 + 条件过滤 | SQL 索引查询 |
| 向量 | 无 | FAISS 索引 |
| 触发 | PreFilter 关键词 + Dream LLM | 三级触发（即时/批量/兜底） |

### 2.8 需要修改的文件

| 操作 | 文件 | 改动 |
|------|------|------|
| **新建** | `fincat/agent/memory_store_v2.py` | `MemoryStoreV2` 类：SQLite memory_item 表的 CRUD，FAISS 向量读写 |
| **新建** | `fincat/agent/embedding.py` | `EmbeddingEngine` 类：bge-small-zh-v1.5 模型加载 + 向量生成 |
| **新建** | `fincat/agent/batch_extractor.py` | `BatchExtractor` 类：P1/P2/P3 三级触发 + LLM 提取 + 去重 + 写入 |
| **新建** | `templates/agent/batch_extract.md` | 批量提取 Prompt 模板 |
| **新建** | `fincat/agent/schema.sql` | SQLite 建表语句（conversations + memory_item + vector_mapping） |
| **修改** | `fincat/config/paths.py` | 新增 `get_vector_dir()`, `get_memory_db_path()` |
| **修改** | `pyproject.toml` | 添加 `faiss-cpu`, `sentence-transformers`（加载 bge-small-zh-v1.5）依赖 |
| **新建** | `tests/test_memory_store_v2.py` | MemoryStoreV2 单元测试 |
| **新建** | `tests/test_batch_extractor.py` | BatchExtractor 单元测试 |

**MemoryStoreV2 类接口**：
```python
class MemoryStoreV2:
    def __init__(self, db_path: Path, vector_dir: Path, embedding: EmbeddingEngine): ...
    def add_item(self, resource_id: str, memory_type: str, summary: str, content: dict, ...) -> str:  # 返回 item_id，自动设置 created_at
    def get_item(self, item_id: str) -> dict | None: ...
    def query(self, memory_type: str = None, category_id: str = None, is_active: bool = True, limit: int = 50) -> list[dict]: ...
    def update_item(self, item_id: str, **fields) -> None: ...
    def deactivate_item(self, item_id: str) -> None:  # 软删除
    def search_similar(self, summary: str, threshold: float = 0.9) -> dict | None:  # 向量去重，返回含 embedded_at 的记录
    def embed_and_index(self, item_id: str, summary: str) -> None:  # 生成向量 + 写入 FAISS + 更新 embedded_at
    def rebuild_faiss_index(self, index_type: str = "Flat") -> None:  # 从 SQLite 重建 FAISS，支持 Flat/IVF 切换
    def get_unembedded_items(self) -> list[dict]:  # 查询 embedded_at 为 NULL 的 Item（用于补建向量）
```

**BatchExtractor 类接口**：
```python
class BatchExtractor:
    def __init__(self, store: MemoryStoreV2, provider: LLMProvider, model: str, resource_store: ResourceStore): ...
    async def extract_immediate(self, resource: dict) -> list[str]:  # P1 即时提取
    async def extract_batch(self, resources: list[dict]) -> list[str]:  # P2 批量提取
    async def extract_pending(self) -> list[str]:  # P3 兜底提取
    def _build_prompt(self, resources: list[dict]) -> str: ...
    def _dedup_and_save(self, extracted: list[dict], resource_id: str) -> list[str]:  # 去重 + 写入
```

### 2.9 详细修改清单

#### 2.9.1 新建 `fincat/agent/schema.sql`

```sql
-- conversations 表（ResourceStore 的 SQLite 镜像）
CREATE TABLE IF NOT EXISTS conversations (
    resource_id  TEXT PRIMARY KEY,
    content_hash TEXT,
    timestamp    TEXT NOT NULL,
    session_id   TEXT,
    message_id   TEXT
);

-- memory_item 表
CREATE TABLE IF NOT EXISTS memory_item (
    item_id          TEXT PRIMARY KEY,
    resource_id      TEXT,
    category_id      TEXT,
    memory_type      TEXT NOT NULL,
    summary          TEXT NOT NULL,
    content          TEXT,
    importance_score REAL DEFAULT 0.5,
    entities         TEXT DEFAULT '[]',
    tags             TEXT DEFAULT '[]',
    created_at       TEXT NOT NULL,
    embedded_at      TEXT,
    last_accessed_at TEXT,
    access_count     INTEGER DEFAULT 0,
    is_active        INTEGER DEFAULT 1,
    extra            TEXT DEFAULT '{}',
    FOREIGN KEY (resource_id) REFERENCES conversations(resource_id)
);

-- vector_mapping 表
CREATE TABLE IF NOT EXISTS vector_mapping (
    faiss_index  INTEGER PRIMARY KEY,
    item_id      TEXT NOT NULL UNIQUE,
    summary_hash TEXT,
    embedded_at  TEXT NOT NULL,
    model_name   TEXT NOT NULL DEFAULT 'bge-small-zh-v1.5',
    FOREIGN KEY (item_id) REFERENCES memory_item(item_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_item_resource ON memory_item(resource_id);
CREATE INDEX IF NOT EXISTS idx_memory_item_category ON memory_item(category_id);
CREATE INDEX IF NOT EXISTS idx_memory_item_type ON memory_item(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_item_active ON memory_item(is_active);
```

#### 2.9.2 新建 `fincat/agent/embedding.py`

**新建类 `EmbeddingEngine`**：

```python
class EmbeddingEngine:
    """bge-small-zh-v1.5 模型加载 + 向量生成"""

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5", device: str = "cpu"):
        """加载模型。首次加载约 2-3 秒，后续调用 ~30ms/条"""
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name, device=device)
        self._dimension = 512

    @property
    def dimension(self) -> int:
        """返回向量维度（512）"""

    def embed(self, text: str) -> list[float]:
        """单条文本 → 512 维向量"""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量文本 → 向量列表（batch 比逐条快 3-5 倍）"""

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦相似度"""
```

#### 2.9.3 新建 `fincat/agent/memory_store_v2.py`

**新建类 `MemoryStoreV2`**：

```python
class MemoryStoreV2:
    """L2 记忆层：SQLite memory_item 表 + FAISS 向量索引"""

    def __init__(self, db_path: Path, vector_dir: Path, embedding: EmbeddingEngine):
        """初始化 SQLite 连接 + FAISS 索引。首次运行时执行 schema.sql 建表。"""
        # self._db = sqlite3.connect(db_path)
        # self._faiss = faiss.read_index(vector_dir / "item_vectors.index") 或新建 Flat 索引
        # self._embedding = embedding

    # --- Item CRUD ---
    def add_item(self, resource_id: str, memory_type: str, summary: str, content: dict,
                 category_id: str = None, importance_score: float = 0.5,
                 entities: list[str] = None, tags: list[str] = None) -> str:
        """创建 Item，返回 item_id。自动设置 created_at。"""

    def get_item(self, item_id: str) -> dict | None:
        """按 item_id 查询"""

    def query(self, memory_type: str = None, category_id: str = None,
              is_active: bool = True, limit: int = 50) -> list[dict]:
        """条件查询"""

    def update_item(self, item_id: str, **fields) -> None:
        """更新指定字段"""

    def deactivate_item(self, item_id: str) -> None:
        """软删除：is_active = 0"""

    # --- 向量操作 ---
    def search_similar(self, summary: str, threshold: float = 0.9) -> dict | None:
        """向量去重：summary → embedding → FAISS cosine 检索，相似度 > threshold 返回已有 Item"""

    def embed_and_index(self, item_id: str, summary: str) -> None:
        """生成向量 → 写入 FAISS → 更新 vector_mapping（embedded_at, model_name）→ 回填 memory_item.embedded_at"""

    def rebuild_faiss_index(self, index_type: str = "Flat") -> None:
        """从 SQLite memory_item 全量重建 FAISS 索引。支持 Flat/IVF 切换。"""

    def get_unembedded_items(self) -> list[dict]:
        """查询 embedded_at IS NULL 的 Item（用于补建向量）"""

    # --- 维护 ---
    def close(self) -> None:
        """关闭 SQLite 连接，保存 FAISS 索引"""
```

#### 2.9.4 新建 `fincat/agent/batch_extractor.py`

**新建类 `BatchExtractor`**：

```python
class BatchExtractor:
    """三级提取引擎：P1 即时 / P2 增量批量 / P3 兜底"""

    def __init__(self, store: MemoryStoreV2, provider, model: str,
                 resource_store: ResourceStore, category_manager=None):
        """初始化，注入 MemoryStoreV2 + LLMProvider + ResourceStore"""
        # self._pending_pool: list[dict] = []  # 待提取 Resource 缓存池
        # self._last_batch_time: float = 0

    # --- P1 即时提取 ---
    async def extract_immediate(self, resource: dict) -> list[str]:
        """用户显式要求记住 / 高价值敏感信息。10 秒内完成。返回 item_id 列表。"""

    # --- P2 增量批量 ---
    async def extract_batch(self, resources: list[dict]) -> list[str]:
        """缓存池满 N 条或距上次提取 > 1 分钟。调 LLM 提取 + 去重 + 写入。返回 item_id 列表。"""

    def add_to_pending(self, resource: dict) -> None:
        """将 Resource 加入待提取缓存池"""

    def should_flush(self) -> bool:
        """判断是否应触发批量提取：池满 或 距上次 > 1 分钟"""

    # --- P3 兜底 ---
    async def extract_pending(self) -> list[str]:
        """会话结束 / 凌晨空闲时处理失败的 Resource"""

    # --- 内部 ---
    def _build_prompt(self, resources: list[dict]) -> str:
        """构建批量提取 Prompt（读取 templates/agent/batch_extract.md）"""

    async def _dedup_and_save(self, extracted: list[dict], resource_id: str) -> list[str]:
        """对 LLM 返回的语义点：向量去重 → 写入 memory_item → 生成向量 → 关联 Category"""

    def _classify_memory_type(self, text: str) -> str:
        """从 LLM 输出中提取 memory_type"""
```

#### 2.9.5 新建 `templates/agent/batch_extract.md`

**Prompt 模板**（指导 LLM 如何从对话中提取结构化记忆）：
```markdown
你是一个记忆提取引擎。从以下对话中提取独立的语义记忆点。

## 规则
1. 每个语义点独立，不合并不同主题
2. 去掉问句（用户在问问题不算记忆）
3. 去掉意见征求（"觉得/认为/怎么样"）
4. 保留：事实、偏好、事件、目标、行为模式
5. 结构化意图：将模糊表述转为 "动作+对象"（如 "买入黄金"）

## 输出格式（JSON 数组）
[
  {"memory_type": "event|fact|preference|knowledge|goal|behavior", "summary": "一句话摘要", "content": "详细内容", "entities": ["实体1"], "tags": ["标签1"]}
]
```

#### 2.9.6 修改 `fincat/config/paths.py` — 新增路径函数

```python
def get_vector_dir() -> Path:
    """返回 ~/.fincat/vector/ 目录"""
    return ensure_dir(get_data_dir() / "vector")

def get_memory_db_path() -> Path:
    """返回 ~/.fincat/memory.db 路径"""
    return get_data_dir() / "memory.db"
```

#### 2.9.7 修改 `pyproject.toml` — 新增依赖

```toml
[project.dependencies]
# ... 现有依赖 ...
faiss-cpu = ">=1.7.4"
sentence-transformers = ">=2.2.0"  # 加载 bge-small-zh-v1.5
```

---

## 三、Category 重构 — 目录化 + 结构化 + 摘要注入

### 3.1 核心变化

| 维度 | 原设计（7 个 md 文件） | 新设计（目录化） |
|------|----------------------|-----------------|
| 结构 | 7 个平铺 md 文件 | 4-5 个一级文件夹 + 多级子目录 md 文件 |
| 注入 | 7 个 md 全量注入 system prompt | 汇总为 memory.md 摘要注入（大幅缩减） |
| Category | 固定 7 个，不可扩展 | 固定 5 个 + 自定义（按语义自动创建） |
| 格式 | `<!-- item:... -->` HTML 注释元数据 | YAML frontmatter + 结构化内容 |
| 生命周期 | 无归档机制 | 过期 Category 自动归档到 archive/ |

### 3.2 目录结构

```
~/.fincat/workspace/memory/
├── memory.md                    # 汇总摘要，注入 system prompt（取代原 7 个 md 全量注入）
├── profile/                     # 用户画像（md 文件可自定义，按话题聚合）
│   ├── basic.md                # 示例：基本信息（年龄、职业、收入）
│   └── risk.md                 # 示例：风险偏好、投资经验
├── knowledge/                   # 知识库（md 文件可自定义，按话题聚合）
│   ├── fund.md                 # 示例：基金相关知识
│   ├── market.md               # 示例：市场规则、交易机制
│   └── financial_terms.md      # 示例：金融术语
├── preferences/                 # 用户偏好（md 文件可自定义，按话题聚合）
│   ├── investment.md           # 示例：投资风格偏好
│   └── communication.md        # 示例：沟通偏好（语言、风格）
├── behavioral_insights/         # 行为洞察（md 文件可自定义，按话题聚合）
│   ├── decision_patterns.md    # 示例：决策模式
│   └── habits.md               # 示例：行为习惯
├── compliance/                  # 合规规则（md 文件可自定义，按话题聚合）
│   ├── risk_warnings.md        # 示例：风险提示规则
│   └── restrictions.md         # 示例：限制规则
├── custom/                      # 自定义分类（系统自动创建）
│   ├── travel/
│   │   └── 2026/
│   │       └── 三亚旅行计划.md
│   ├── work/
│   │   └── 项目A.md
│   └── stocks/
│       └── 茅台追踪.md
└── archive/                     # 归档（过期的 Category 整体移入）
    └── 2026/
        └── 04/
            └── 旧项目.md
```

**5 个固定文件夹下的 md 文件规则**：
- 文件夹（profile/knowledge/...）是固定的，但里面的 **md 文件可以自由创建和命名**
- 核心原则：**同一个话题的内容尽量聚合在同一个 md 文件中**，避免碎片化
- 例如：所有基金相关知识放在 `knowledge/fund.md`，而不是每个知识点一个文件
- 子目录可选：当某个话题下文件过多时，可按子话题建子目录（如 `knowledge/financial/`）
- 系统不会自动拆分 md 文件，只会在内容过多时建议用户拆分

### 3.3 Category 标准格式

每个 Category 是一个独立的 md 文件，格式如下：

```markdown
---
category_id: cate_3xY7pQ2z
name: 2026年5月三亚旅行计划
type: custom
created_at: 2026-05-11T01:00:00Z
updated_at: 2026-05-11T01:00:00Z
last_accessed_at: 2026-05-11T01:00:00Z
activity_score: 0.85
is_active: true
tags: ["旅行", "三亚", "2026年5月"]
parent_id: cate_travel_123
auto_generated: true
---

# 2026年5月三亚旅行计划

> 摘要：用户计划5月15日和女友去三亚玩3天，预算5000元，喜欢冲浪，不吃海鲜，
> 住宿预算300~500元/晚，优先选靠海的酒店。

## 记忆条目

- [2026-05-08] ✨【偏好】用户喜欢冲浪，水平入门级，需要教练指导 | item_id: item_001
- [2026-05-08] 📌【事实】旅行时间5月15日~17日共3天 | item_id: item_002
- [2026-05-08] 💰【事实】整体预算5000元，住宿预算300~500元/晚 | item_id: item_003
- [2026-05-08] ❌【偏好】用户不吃海鲜 | item_id: item_004
- [2026-05-09] 🏨【偏好】优先选海棠湾/亚龙湾靠海酒店 | item_id: item_005
- [2026-05-10] 📌【事实】已订5月15日南航机票，票价1200元 | item_id: item_006
```

**Frontmatter 字段说明**：
| 字段 | 类型 | 说明 |
|------|------|------|
| `category_id` | string | 全局唯一 ID，`cate_{uuid8}` |
| `name` | string | Category 名称 |
| `type` | string | `system`（内置）/ `custom`（自动创建）/ `user`（用户手动创建） |
| `created_at` | ISO 8601 | 创建时间 |
| `updated_at` | ISO 8601 | 最后更新时间 |
| `last_accessed_at` | ISO 8601 | 最后访问时间 |
| `activity_score` | float | 活跃度评分（0-1），基于访问频率和 recency |
| `is_active` | bool | 是否活跃（归档 = false） |
| `tags` | string[] | 标签列表 |
| `parent_id` | string | 父 Category ID（支持层级） |
| `auto_generated` | bool | 是否自动生成（用户手动修改过 = false） |

**记忆条目格式**：`- [日期] emoji【类型】内容 | item_id: xxx`

emoji 映射：
- ✨ 偏好（preference）
- 📌 事实（fact）
- 📚 知识（knowledge）
- 📅 事件（event）
- 🎯 目标（goal）
- 📊 行为（behavior）

### 3.4 内置 Category（5 个固定 + 1 个归档）

| 文件夹 | 对应原 category | 说明 |
|--------|----------------|------|
| `profile/` | profile | 用户画像（基本信息、风险偏好） |
| `knowledge/` | knowledge | 知识库（金融知识、市场规则） |
| `preferences/` | preference | 用户偏好（投资风格、沟通偏好） |
| `behavioral_insights/` | insight + behavior | 行为洞察（决策模式、行为习惯） |
| `compliance/` | compliance | 合规规则（风险提示、禁用语） |
| `archive/` | — | 归档（过期 Category 整体移入） |

### 3.5 自定义 Category 创建规则

**自动创建条件**：当批量提取引擎发现多个 Item 属于同一语义主题，自动创建自定义 Category。

**归类优先级**（高→低）：
1. **用户手动修改优先**：`auto_generated: false` 的 Category，批量任务不修改内容，只追加新 Item
2. **自定义规则匹配**：可预设规则（如"所有包含项目 A 的 Item 归到 `work/项目A`"），匹配后不走自动聚类
3. **系统内置目录**：preference 类 Item → `preferences/`，knowledge 类 → `knowledge/`
4. **自动按时间分组**：同类主题按年/月建子目录，如 `travel/2026/三亚旅行计划.md`
5. **自动按语义聚类**：主题类 Item 自动归到语义子目录（旅行 → `travel/`，工作 → `work/`）

**噪声处理**：
- 聚类时的孤立 Item（无法归到任何簇、匹配不到已有分类）→ 暂留未分类
- 第二天重新聚类，避免生成无效小分类

**重复检测**：
- 生成新 Category 前，与已有 Category 摘要做向量比对
- 相似度 > 0.7 → 直接合并到已有 Category，不重复生成

### 3.6 归档机制

**触发条件**（满足任一）：
- `activity_score < 0.2` 且超过 30 天无访问
- Category 关联的事件已过期（如旅行结束、项目完成）
- 用户手动标记归档

**归档操作**：
1. `is_active` 设为 false
2. 整个 md 文件移动到 `archive/{year}/{month}/`
3. Category 下的 Item 的 `is_active` 设为 false
4. 从 memory.md 摘要中移除该 Category

**恢复**：用户可在 UI 中取消归档，文件移回原位置。

### 3.7 memory.md — 汇总摘要（注入 system prompt）

原来 7 个 md 全量注入 system prompt（每个 ~150 字压缩摘要，总计 ~1000 字 + HTML 注释噪音）。
现改为**只注入 memory.md 汇总摘要**，大幅缩减 token 用量。

**memory.md 结构**：

```markdown
# 用户记忆摘要

## 画像
- 风险偏好：稳健型，R2 及以下产品
- 投资经验：新手，5 万存款
- 活跃时段：工作日晚 20:00-23:00

## 偏好
- 价格显示人民币
- 回复风格简洁专业
- 关注新能源电池行业（比亚迪、宁德时代）

## 近期事件
- [进行中] 三亚旅行计划（5月15日~17日，预算5000）
- [进行中] 茅台走势追踪（每周五查看）

## 行为洞察
- 每周五下午关注黄金价格
- 市场下跌时倾向恐慌卖出

## 合规
- 不推荐 R3 以上产品
- 必须包含风险提示
```

**生成方式**：由 Category Manager 自动维护，每次 Category 更新后重新生成。

**更新时机**：
- Item 写入/更新/删除后
- Category 创建/归档后

**Token 预算**：memory.md 目标 < 500 字（约 300 tokens），远小于原 7 个 md 的 ~1000+ tokens。

### 3.8 需要修改的文件

| 操作 | 文件 | 改动 |
|------|------|------|
| **新建** | `fincat/agent/category_manager.py` | `CategoryManager` 类：目录管理、归档、memory.md 生成、md 文件读写 |
| **新建** | `fincat/agent/category_index.py` | `CategoryIndex` 类：Category 元数据索引（从 frontmatter 解析） |
| **新建** | `templates/agent/batch_extract.md` | 批量提取 Prompt 模板（指导 LLM 如何分配 Category） |
| **修改** | `fincat/agent/context.py` | `build_system_prompt()` 改为注入 memory.md 而非 7 个 md；导入从 `MemoryStore` 改为 `CategoryManager`；删除 `get_entity_summary()` + 7 个 `read_file` 提示（第 68-75 行），替换为 `category_manager.read_memory_md()` |
| **修改** | `fincat/agent/memory.py` | 移除旧 MemoryStore 的 `CATEGORY_FILES` 字典、`_init_category_files()`、`read_category()`、`category_path()`、`all_categories()`、`get_memory_context()`、`get_entity_summary()` 等方法；Dream 类删除 `_build_category_file_context()`、Phase 2 的 `read_file/edit_file` 工具调用，仅保留 Skill 发现逻辑 |
| **新建** | `tests/test_category_manager.py` | CategoryManager 单元测试 |

**CategoryManager 类接口**：
```python
class CategoryManager:
    def __init__(self, memory_dir: Path, embedding: EmbeddingEngine): ...
    def get_or_create_category(self, name: str, type: str = "custom", parent_id: str = None) -> str:  # 返回 category_id
    def add_item_to_category(self, category_id: str, item_id: str, item_data: dict) -> None: ...
    def archive_category(self, category_id: str) -> None: ...
    def restore_category(self, category_id: str) -> None: ...
    def regenerate_memory_md(self) -> None:  # 重新生成 memory.md
    def read_category_md(self, category_id: str) -> str: ...
    def find_best_category(self, summary: str, memory_type: str) -> str | None:  # 向量匹配最佳 Category
    def _parse_frontmatter(self, md_content: str) -> dict: ...
    def _update_activity_score(self, category_id: str) -> None: ...
```

### 3.9 详细修改清单

#### 3.9.1 新建 `fincat/agent/category_index.py`

**新建类 `CategoryIndex`**：

```python
class CategoryIndex:
    """Category 元数据索引，从 YAML frontmatter 解析，内存缓存"""

    def __init__(self, memory_dir: Path):
        """扫描 memory_dir 下所有 .md 文件，解析 frontmatter 构建索引"""
        # self._index: dict[str, dict] = {}  # category_id → {name, type, path, tags, ...}

    def scan(self) -> None:
        """扫描目录，重建索引。启动时调用一次。"""

    def get(self, category_id: str) -> dict | None:
        """按 category_id 查询元数据"""

    def get_by_name(self, name: str) -> dict | None:
        """按 name 查询"""

    def list_all(self) -> list[dict]:
        """返回所有 Category 元数据"""

    def list_by_type(self, type: str) -> list[dict]:
        """按 type 过滤：system/custom/user"""

    def upsert(self, category_id: str, metadata: dict) -> None:
        """更新或插入索引条目"""

    def remove(self, category_id: str) -> None:
        """从索引中移除"""

    def _parse_frontmatter(self, md_path: Path) -> dict | None:
        """解析单个 .md 文件的 YAML frontmatter"""
```

#### 3.9.2 新建 `fincat/agent/category_manager.py`

**新建类 `CategoryManager`**：

```python
class CategoryManager:
    """目录化 Category 管理：目录管理、归档、memory.md 生成、md 文件读写"""

    BUILTIN_FOLDERS = ["profile", "knowledge", "preferences", "behavioral_insights", "compliance"]

    def __init__(self, memory_dir: Path, embedding: EmbeddingEngine):
        """初始化目录结构 + 加载 CategoryIndex"""
        # self._memory_dir = memory_dir
        # self._index = CategoryIndex(memory_dir)
        # self._embedding = embedding

    # --- Category 生命周期 ---
    def get_or_create_category(self, name: str, type: str = "custom", parent_id: str = None) -> str:
        """获取或创建 Category。返回 category_id。自动创建目录和 md 文件。"""

    def archive_category(self, category_id: str) -> None:
        """归档：is_active=false，移动到 archive/{year}/{month}/"""

    def restore_category(self, category_id: str) -> None:
        """恢复归档：移回原位置"""

    # --- Item 写入 ---
    def add_item_to_category(self, category_id: str, item_id: str, item_data: dict) -> None:
        """将 Item 写入对应 Category 的 md 文件（YAML frontmatter + 条目列表）"""

    def remove_item_from_category(self, category_id: str, item_id: str) -> None:
        """从 Category 的 md 文件中移除 Item 条目"""

    # --- 读取 ---
    def read_category_md(self, category_id: str) -> str:
        """读取 Category 的 md 文件内容"""

    def read_memory_md(self) -> str:
        """读取 memory.md 汇总摘要（注入 system prompt）"""

    def find_best_category(self, summary: str, memory_type: str) -> str | None:
        """向量匹配最佳 Category：summary embedding ↔ 各 Category 摘要 embedding"""

    # --- memory.md 维护 ---
    def regenerate_memory_md(self) -> None:
        """重新生成 memory.md：遍历所有活跃 Category，提取摘要段落，拼接为 <500 字的汇总"""

    # --- 自动管理 ---
    def auto_create_category(self, items: list[dict]) -> str | None:
        """BatchExtractor 调用：多个 Item 属于同一语义主题时自动创建 Category"""

    def check_archive_candidates(self) -> list[str]:
        """检查 activity_score < 0.2 且 30 天无访问的 Category，返回待归档列表"""

    def _update_activity_score(self, category_id: str) -> None:
        """基于访问频率和 recency 更新活跃度评分"""

    def _parse_frontmatter(self, md_content: str) -> dict:
        """解析 YAML frontmatter"""

    def _write_frontmatter(self, md_path: Path, metadata: dict, body: str) -> None:
        """写入带 frontmatter 的 md 文件"""
```

#### 3.9.3 修改 `fincat/agent/context.py` — `ContextBuilder` 类

**`__init__()` (line 24) — 改变导入**：
```python
# 原来：
from fincat.agent.memory import MemoryStore
self.memory = MemoryStore(workspace)

# 改为：
from fincat.agent.category_manager import CategoryManager
from fincat.agent.embedding import EmbeddingEngine
embedding = EmbeddingEngine()
self._category_manager = CategoryManager(workspace / "memory", embedding)
# 保留 MemoryStore 用于 history.jsonl（Consolidator 仍需要）
self.memory = MemoryStore(workspace)  # 暂时保留，后续 Consolidator 迁移后可移除
```

**`build_system_prompt()` (line 53) — 替换记忆注入**：
```python
# 删除原来的内容（line 65-77）：
# entity_summary = self.memory.get_entity_summary()
# if entity_summary:
#     parts.append(f"# Memory (entity summaries)\n\n{entity_summary}\n\n" ...)
#
# 替换为：
memory_md = self._category_manager.read_memory_md()
if memory_md:
    parts.append(f"# 用户记忆摘要\n\n{memory_md}\n\n"
                 "以上是用户的记忆摘要，包含画像、偏好、近期事件、行为洞察和合规规则。"
                 "如需详情，使用 read_file 读取对应 Category 文件。")
```

#### 3.9.4 修改 `fincat/agent/memory.py` — `MemoryStore` 类

**删除以下方法**（旧 Category 系统，由 CategoryManager 替代）：
- `_init_category_files()` (line 82) — 不再需要初始化 7 个 md
- `category_path()` (line 234) — 由 CategoryManager 管理
- `read_category()` (line 241) — 由 CategoryManager 管理
- `write_category()` (line 244) — 由 CategoryManager 管理
- `append_category_entry()` (line 247) — 由 CategoryManager 管理
- `all_categories()` (line 285) — 由 CategoryManager 管理
- `parse_category_metadata()` (line 289) — 由 CategoryManager 管理
- `get_memory_context()` (line 334) — 由 CategoryManager.read_memory_md() 替代
- `get_category_summary()` (line 350) — 由 CategoryManager.read_memory_md() 替代
- `get_entity_summary()` (line 371) — 由 CategoryManager.read_memory_md() 替代

**保留以下方法**（Consolidator / Dream 仍需要）：
- `read_memory()` / `write_memory()` — MEMORY.md 读写（可能后续也迁移）
- `read_soul()` / `write_soul()` — SOUL.md 读写
- `read_user()` / `write_user()` — USER.md 读写
- `append_history()` / `read_unprocessed_history()` / `compact_history()` — history.jsonl（Consolidator 需要）
- `raw_archive()` — 兜底归档

**`CATEGORY_FILES` 字典**（line 42）：删除整个字典定义。

---

## 四、主动预测系统 — 实时轻量预测 + 深度模式挖掘

### 4.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│              主动预测系统（零 LLM 实时 + LLM 批量）       │
│                                                          │
│  实时层（零 LLM）                                        │
│  ├─ 规则匹配：显式意图模式（"买入" → 推荐分析）          │
│  ├─ 向量相似度：当前对话 ↔ Topic 模板库                  │
│  └─ 触发时机：用户登录 / 上下文检测到高频模式            │
│                                                          │
│  深度层（每日 03:00 批量）                                │
│  ├─ 时间周期性挖掘：30 天交互日志 → 周期模式             │
│  ├─ 语义关联挖掘：话题转移概率 → 预测性推荐              │
│  ├─ 实体关联挖掘：业务实体 + 个人实体关联                │
│  └─ 输出：置信度 ≥ 0.8 的模式 → Topic 缓存               │
│                                                          │
│  Topic 缓存（Top 3）                                     │
│  └─ 前端推送，用户可交互（点击/忽略/拒绝）               │
└─────────────────────────────────────────────────────────┘
```

### 4.2 第一部分：实时轻量预测（零 LLM）

**核心原则**：整个识别过程不调用 LLM，只用规则匹配 + 向量相似度计算。

**触发时机**：
1. **用户打开 APP / 登录系统时**：加载 Topic 缓存，展示 Top 3 推送
2. **检测到当前上下文出现高频关联模式时**：实时匹配用户当前对话内容

**匹配方式**：

| 方式 | 输入 | 方法 | 示例 |
|------|------|------|------|
| 规则匹配 | 用户消息 | 正则/关键词匹配意图模板 | "买入黄金" → 推荐黄金分析报告 |
| 向量相似度 | 当前对话 embedding | 与 Topic 模板库做 cosine 检索 | 对话提到"旅行" → 匹配"三亚旅行计划" |
| 上下文关联 | 当前对话 + 历史模式 | 查表（语义关联概率表） | 用户问了特斯拉 → 85% 概率问比亚迪 |

**规则库结构**（`rules.json`）：

```json
{
  "rules": [
    {
      "rule_id": "rule_001",
      "trigger": {"type": "keyword", "pattern": "买入|建仓|加仓"},
      "context": {"category": "stock|fund|gold"},
      "action": {
        "topic_template": "「{entity}」买入时机分析",
        "content_template": "基于近期走势和技术指标，为您生成{entity}的买入时机分析报告",
        "category": "insight",
        "priority": 2
      },
      "confidence": 0.85
    }
  ]
}
```

### 4.3 第二部分：深度模式挖掘（每日 03:00 批量）

**数据输入**：过去 30 天的 `interaction_logs.jsonl` + `conversations.jsonl`，每条带精确时间戳。

#### 模式类型 1：时间周期性模式（金融定位，最高权重）

对应场景：周报/交易监控/日程提醒/行情查看等周期行为。

**挖掘流程**：

```
1. 数据输入：拉取过去 30 天交互日志，每条带精确时间戳
2. 特征提取：按时间维度聚合行为
   ├─ 周维度：周一~周日的行为差异（如"周五下午查黄金"）
   ├─ 天维度：0~23 点的行为分布（如"早 9 点查行情"）
   └─ 特殊时间：节假日、月初/月末、发薪日
3. 挖掘算法：频繁项集挖掘 + 时间序列自相关分析
4. 置信度计算：相同行为在同一时间点重复出现的次数占比
   └─ ≥ 0.8 的模式生效 → 写入 Topic 缓存
```

**权重**：时间周期性模式给予**最高权重**（基础 priority + 2），因为这类模式（如交易监控、行情查看）最符合金融 AI 定位。

#### 模式类型 2：语义关联模式

**场景关联**：用户提到「三亚旅行」→ 后续 85% 概率查询预算、酒店、冲浪点。

**问答关联**：用户问了 A 话题 → 下一轮 70% 概率问 B 话题。

**挖掘流程**：
```
1. 从 conversations.jsonl 提取对话序列（话题转移链）
2. 统计话题转移概率矩阵：P(B|A) = count(A→B) / count(A)
3. 概率 ≥ 0.7 的关联 → 生成预测性 Topic
```

#### 模式类型 3：实体关联模式

**业务实体关联**：今天问了特斯拉 → 明天可能问其他汽车股票（比亚迪、蔚来）。

**个人实体关联**：用户提到"女朋友" → 关联到之前的旅行/消费类话题。

**挖掘流程**：
```
1. 从 memory_item 表提取 entities 字段
2. 构建实体共现图：两个实体在同一 resource 中出现 → 建立边
3. 高频共现实体对 → 生成关联推荐
```

### 4.4 自动置信度调整

每次用户与 Topic 交互后，根据反馈调整规则置信度：

| 用户行为 | 置信度调整 |
|---------|-----------|
| 点击 Topic（click） | +0.05 |
| 忽略 Topic（ignore） | -0.02 |
| 拒绝 Topic（reject） | -0.10 |
| 通过 Topic 发起对话 | +0.10 |

置信度范围：[0.1, 1.0]。低于 0.3 的规则自动禁用。

### 4.5 Topic 缓存与推送

**存储**：`~/.fincat/workspace/memory/topics.jsonl`，保留**优先级前 3**（匹配前端 3 个卡片位置）。

**Topic 格式**（匹配前端 `Topic` 接口）：

```json
{
  "topic_id": "topic_abc123",
  "source": "pattern_miner",
  "source_name": "深度模式挖掘",
  "category": "reminder",
  "title": "周五黄金行情监控",
  "content": "根据您的行为模式，您通常在周五下午查看黄金行情。当前金价较上周下跌 2.3%，建议关注支撑位。",
  "priority": 3,
  "created_at": "2026-05-11T03:00:00Z",
  "pattern_type": "time_periodicity",
  "confidence": 0.92,
  "expires_at": "2026-05-12T03:00:00Z"
}
```

前端 `Topic` 接口字段：`topic_id`, `source`, `source_name`, `category`(`'alert'|'news'|'reminder'|'insight'`), `title`, `content`, `priority`, `created_at`

**前端 Topic category 映射**：

| 挖掘模式 | 默认 category | 说明 |
|---------|--------------|------|
| 时间周期性（交易监控） | `reminder` | 定时提醒类 |
| 时间周期性（行情查看） | `alert` | 需要关注的变动 |
| 语义关联 | `insight` | 洞察推荐类 |
| 实体关联 | `news` | 相关资讯类 |

**优先级权重规则**：

| 模式类型 | 基础 priority | 加权后 | 说明 |
|---------|--------------|--------|------|
| 时间周期性（金融相关） | 2 | **+2 → 4** | 交易监控、行情查看，符合金融定位 |
| 时间周期性（非金融） | 2 | +0 → 2 | 普通周期行为 |
| 语义关联（高概率 ≥ 0.85） | 1 | +1 → 2 | 强关联推荐 |
| 实体关联 | 1 | +0 → 1 | 弱关联 |

最终取 Top 3 写入 topics.jsonl，前端展示。

**Topic 过期处理**：
- `expires_at` 到期后自动从 topics.jsonl 移除
- 每日 03:00 深度挖掘完成后，清理过期 Topic，补充新 Topic
- 用户点击/拒绝的 Topic 立即移除

**推送时机**：
1. **定时推送**：每日 03:00 挖掘完成后，写入 Topic 缓存
2. **首次打开前端**：WebSocket 推送 topics.jsonl 内容，展示 Top 3
3. **用户登录时**：实时轻量预测补充更新 Topic

**前端交互回调**：
- 用户点击 Topic → `interaction_logs.jsonl` 记录 `click_push_notification` → 置信度 +0.05
- 用户忽略 Topic → 记录 `ignore_push_notification` → 置信度 -0.02
- 用户拒绝 Topic → 记录 `reject_push_notification` → 置信度 -0.10 → 立即移除该 Topic

### 4.6 移除旧 ProactiveSkill 每条消息触发

**文件**: `fincat/agent/loop.py` `_monitor_scan()`

删除 `self._proactive_skill.analyze("realtime")`。主动预测仅在以下时机运行：
- 用户登录 / 打开前端（实时轻量预测）
- 每日 03:00（深度模式挖掘）
- 上下文检测到高频模式（实时规则匹配）

### 4.7 Dream 精简为 Skill 发现

Dream 原有职责全部拆走：
- 记忆提取 → 批量提取引擎（第二部分）
- Category 编辑 → Category Manager（第三部分）
- 模式挖掘 → 主动预测系统（第四部分）

Dream 仅保留 **Skill 发现**：从 conversations.jsonl 中发现可重复工作流 → 创建 Skill。

**需要删除的 Dream 方法**（`fincat/agent/memory.py`）：
- `_build_category_file_context()` — 构建 7 个 category 文件上下文（第 944 行）
- `_phase2()` 中的 `read_file/edit_file/write_file` 工具调用 — Category 文件编辑
- `_extract_memory_items()` — 从 history.jsonl 提取 MemoryItem（第 842 行）
- `_run_phase2_agent()` — Phase 2 AgentRunner 调用
- `_build_phase2_prompt()` — Phase 2 Prompt 构建

**保留的 Dream 方法**：
- `run()` — 主入口（精简为只调用 Skill 发现）
- `_phase1()` — 分析对话，输出 [SKILL] 标记
- `_create_skill_from_analysis()` — 从分析结果创建 Skill

### 4.8 移除 SignalStore

**删除**: `fincat/agent/signal_store.py`

### 4.9 需要修改的文件

| 操作 | 文件 | 改动 |
|------|------|------|
| **新建** | `fincat/agent/prediction_engine.py` | `PredictionEngine` 类：实时规则匹配 + 向量相似度检索 |
| **新建** | `fincat/agent/pattern_miner.py` | `PatternMiner` 类：时间周期性 + 语义关联 + 实体关联挖掘 |
| **新建** | `fincat/agent/topic_cache.py` | `TopicCache` 类：Top 3 管理、过期清理、置信度调整、WebSocket 推送 |
| **新建** | `fincat/agent/rules.json` | 预测规则库 |
| **新建** | `templates/agent/pattern_mine.md` | 深度挖掘 Prompt 模板（LLM 辅助语义分析） |
| **修改** | `fincat/agent/loop.py` | 移除 `_monitor_scan()` 中的 `proactive_skill.analyze()`；集成 TopicCache |
| **修改** | `fincat/agent/memory.py` | Dream 精简为 Skill 发现 |
| **修改** | `fincat/agent/proactive_skill.py` | **删除**，由 PredictionEngine + TopicCache 替代 |
| **修改** | `fincat/agent/signal_store.py` | **删除** |
| **修改** | `fincat/agent/memory_monitor.py` | 移除 periodic/frequency 触发 |
| **修改** | `fincat/channels/websocket.py` | 新增 Topic 推送 WebSocket 事件 |
| **新建** | `tests/test_prediction_engine.py` | PredictionEngine 单元测试 |
| **新建** | `tests/test_pattern_miner.py` | PatternMiner 单元测试 |

**PredictionEngine 类接口**：
```python
class PredictionEngine:
    def __init__(self, rules_path: Path, topic_cache: TopicCache, embedding: EmbeddingEngine): ...
    def match_rules(self, user_message: str) -> list[dict]:  # 规则匹配
    def match_similar_topics(self, conversation_embedding: list[float], top_k: int = 3) -> list[dict]:  # 向量检索
    def predict_from_context(self, current_message: str, session_history: list[dict]) -> list[dict]:  # 上下文关联
    def update_confidence(self, rule_id: str, feedback: str) -> None:  # 置信度调整
```

**PatternMiner 类接口**：
```python
class PatternMiner:
    def __init__(self, interaction_logs: Path, conversations: Path, memory_db: Path): ...
    async def mine_time_periodicity(self, days: int = 30) -> list[dict]:  # 时间周期性
    async def mine_semantic_association(self, days: int = 30) -> list[dict]:  # 语义关联
    async def mine_entity_association(self) -> list[dict]:  # 实体关联
    def _compute_confidence(self, pattern: dict) -> float: ...
    async def run_daily(self) -> list[dict]:  # 每日 03:00 主入口
```

**TopicCache 类接口**：
```python
class TopicCache:
    def __init__(self, cache_path: Path, max_topics: int = 3): ...
    def set_topics(self, topics: list[dict]) -> None:  # 写入 Top N
    def get_topics(self) -> list[dict]:  # 读取当前 Topic
    def remove_topic(self, topic_id: str) -> None:  # 用户拒绝后移除
    def clean_expired(self) -> None:  # 清理过期 Topic
    def adjust_confidence(self, topic_id: str, feedback: str) -> float:  # 返回新置信度
    async def push_to_frontend(self, ws_channel) -> None:  # WebSocket 推送
```

### 4.10 详细修改清单

#### 4.10.1 新建 `fincat/agent/rules.json`

```json
{
  "version": 1,
  "rules": [
    {
      "rule_id": "rule_buy_analysis",
      "trigger": {"type": "keyword", "pattern": "买入|建仓|加仓|定投"},
      "context": {"category": "stock|fund|gold"},
      "action": {
        "topic_template": "「{entity}」买入时机分析",
        "content_template": "基于近期走势和技术指标，为您生成{entity}的买入时机分析报告",
        "category": "insight",
        "priority": 2
      },
      "confidence": 0.85
    },
    {
      "rule_id": "rule_market_monitor",
      "trigger": {"type": "keyword", "pattern": "行情|走势|涨跌|大盘"},
      "context": {"category": "stock|index"},
      "action": {
        "topic_template": "今日行情速报",
        "content_template": "当前市场行情概览，包含您关注的标的最新动态",
        "category": "alert",
        "priority": 1
      },
      "confidence": 0.75
    }
  ]
}
```

#### 4.10.2 新建 `fincat/agent/prediction_engine.py`

**新建类 `PredictionEngine`**：

```python
class PredictionEngine:
    """实时轻量预测：规则匹配 + 向量相似度（零 LLM）"""

    def __init__(self, rules_path: Path, topic_cache: 'TopicCache', embedding: EmbeddingEngine):
        """加载 rules.json + TopicCache + EmbeddingEngine"""
        # self._rules = json.loads(rules_path.read_text())["rules"]
        # self._topic_cache = topic_cache
        # self._embedding = embedding

    def match_rules(self, user_message: str) -> list[dict]:
        """规则匹配：正则/关键词匹配 trigger.pattern，返回匹配的 action 列表"""

    def match_similar_topics(self, conversation_embedding: list[float], top_k: int = 3) -> list[dict]:
        """向量检索：当前对话 embedding ↔ Topic 模板库 cosine 检索"""

    def predict_from_context(self, current_message: str, session_history: list[dict]) -> list[dict]:
        """上下文关联：当前对话 + 历史模式 → 查语义关联概率表"""

    def update_confidence(self, rule_id: str, feedback: str) -> None:
        """置信度调整：click +0.05, ignore -0.02, reject -0.10。写回 rules.json"""

    def predict(self, user_message: str, session_history: list[dict] = None) -> list[dict]:
        """主入口：合并规则匹配 + 向量检索 + 上下文关联，去重后返回 Top N"""
```

#### 4.10.3 新建 `fincat/agent/pattern_miner.py`

**新建类 `PatternMiner`**：

```python
class PatternMiner:
    """深度模式挖掘：时间周期性 + 语义关联 + 实体关联（每日 03:00 批量）"""

    def __init__(self, resource_store: ResourceStore, memory_db: Path, embedding: EmbeddingEngine):
        """注入 ResourceStore + memory.db + EmbeddingEngine"""

    async def mine_time_periodicity(self, days: int = 30) -> list[dict]:
        """时间周期性挖掘（最高权重）：
        1. 拉取过去 N 天 interaction_logs
        2. 按时间维度聚合（周维度 + 天维度）
        3. 频繁项集挖掘 + 自相关分析
        4. 置信度 ≥ 0.8 → 输出 PeriodicPattern
        """

    async def mine_semantic_association(self, days: int = 30) -> list[dict]:
        """语义关联挖掘：
        1. 从 conversations 提取话题转移链
        2. 统计话题转移概率矩阵 P(B|A)
        3. 概率 ≥ 0.7 → 生成预测性 Topic
        """

    async def mine_entity_association(self) -> list[dict]:
        """实体关联挖掘：
        1. 从 memory_item 提取 entities 字段
        2. 构建实体共现图
        3. 高频共现实体对 → 生成关联推荐
        """

    def _compute_confidence(self, pattern: dict) -> float:
        """计算模式置信度"""

    async def run_daily(self) -> list[dict]:
        """每日 03:00 主入口：三种挖掘合并 → 过滤置信度 ≥ 0.8 → 返回 Topic 列表"""
```

#### 4.10.4 新建 `fincat/agent/topic_cache.py`

**新建类 `TopicCache`**：

```python
class TopicCache:
    """Topic 缓存：Top 3 管理、过期清理、置信度调整、WebSocket 推送"""

    def __init__(self, cache_path: Path, max_topics: int = 3):
        """加载 topics.jsonl，max_topics=3 匹配前端 3 个卡片位置"""

    def set_topics(self, topics: list[dict]) -> None:
        """写入 Top N Topic（按 priority 排序，取前 max_topics）"""

    def get_topics(self) -> list[dict]:
        """读取当前 Topic 列表（供前端推送）"""

    def remove_topic(self, topic_id: str) -> None:
        """用户拒绝后移除该 Topic"""

    def clean_expired(self) -> None:
        """清理 expires_at 已过期的 Topic"""

    def adjust_confidence(self, topic_id: str, feedback: str) -> float:
        """调整置信度：click +0.05, ignore -0.02, reject -0.10。返回新置信度。
        低于 0.3 的规则自动禁用。"""

    async def push_to_frontend(self, ws_channel) -> None:
        """WebSocket 推送：调用 ws_channel.send_topics()"""

    def on_user_feedback(self, topic_id: str, feedback: str) -> None:
        """用户反馈回调：调整置信度 + 记录 interaction_log + 移除被拒绝的 Topic"""
```

#### 4.10.5 新建 `templates/agent/pattern_mine.md`

**Prompt 模板**（PatternMiner 中 LLM 辅助语义分析）：
```markdown
你是一个行为模式分析引擎。根据以下用户交互数据，识别可预测的行为模式。

## 输入
- 过去 30 天的交互日志（带时间戳）
- 话题转移链
- 实体共现图

## 输出格式
[
  {"type": "time_periodicity|semantic_association|entity_association", "description": "模式描述", "confidence": 0.0-1.0, "suggested_topic": {"title": "...", "content": "...", "category": "reminder|alert|insight|news", "priority": 1-3}}
]

## 规则
1. 置信度 < 0.8 的模式不输出
2. 时间周期性模式给予最高权重（金融相关 +2）
3. 同一模式不重复输出
```

#### 4.10.6 修改 `fincat/agent/loop.py` — `AgentLoop` 类

**`_monitor_scan()` (line 828) — 移除 ProactiveSkill，集成 TopicCache**：
```python
# 删除（line 854）：
self._proactive_skill.analyze("realtime")

# 新增：TopicCache 过期清理
self._topic_cache.clean_expired()
```

**`_process_message()` (line 890) — 新增实时预测**：
```python
# 在 agent loop 完成后、返回响应前，新增：
predictions = self._prediction_engine.predict(user_text, session_history=session.messages[-10:])
if predictions:
    self._topic_cache.set_topics(predictions)
    await self._topic_cache.push_to_frontend(self._ws_channel)
```

**`__init__()` (line 151) — 新增初始化**：
```python
from fincat.agent.prediction_engine import PredictionEngine
from fincat.agent.topic_cache import TopicCache
from fincat.agent.pattern_miner import PatternMiner

self._topic_cache = TopicCache(workspace / "memory" / "topics.jsonl")
self._prediction_engine = PredictionEngine(
    rules_path=Path(__file__).parent / "rules.json",
    topic_cache=self._topic_cache,
    embedding=self._embedding,
)
self._pattern_miner = PatternMiner(
    resource_store=self._resource_store,
    memory_db=get_memory_db_path(),
    embedding=self._embedding,
)
```

**删除以下初始化**（line 278-291）：
```python
# 删除 ProactiveSkill 和 SignalStore 的初始化
self._signal_store = SignalStore(...)  # 删除
self._proactive_skill = ProactiveSkill(...)  # 删除
```

**`_ensure_pattern_cron_job()` (line 426) — 改为调用 PatternMiner**：
```python
# 原来注册的 proactive_analysis cron 改为调用 PatternMiner
# 03:00 深度挖掘
topics = await self._pattern_miner.run_daily()
self._topic_cache.set_topics(topics)
await self._topic_cache.push_to_frontend(self._ws_channel)
```

#### 4.10.7 修改 `fincat/agent/memory_monitor.py` — `MemoryMonitor` 类

**`_check_item()` (line 397) — 移除 periodic 和 frequency 触发**：
```python
# 删除 check_frequency() 调用（由 PatternMiner 的时间周期性替代）
# 删除 check_periodic() 调用（由 PatternMiner 的时间周期性替代）
# 保留 check_decay_threshold()（归档仍需要）
```

**`scan()` (line 266) — 精简**：
```python
# 只保留 decay 归档触发，移除 frequency/periodic
# _check_item() 内部只调 check_decay_threshold()
```

#### 4.10.8 修改 `fincat/channels/websocket.py` — `WebSocketChannel` 类

**新增方法**：
```python
async def push_topics(self, topics: list[dict]) -> None:
    """主动推送 Topic 到所有连接的客户端"""
    for chat_id in self._connections:
        await self.send_topics_to_connection(chat_id, topics)
```

**`on_connect` 回调注册**（在 `loop.py` 中）：
```python
# 用户连接时推送当前 Topic 缓存
@ws_channel.on_connect
async def on_connect(chat_id):
    topics = self._topic_cache.get_topics()
    if topics:
        await ws_channel.send_topics_to_connection(chat_id, topics)
```

#### 4.10.9 删除文件

| 文件 | 原因 |
|------|------|
| `fincat/agent/proactive_skill.py` | 由 PredictionEngine + TopicCache 替代 |
| `fincat/agent/signal_store.py` | 不再需要，TopicCache 直接管理 Topic |

---

## 完整文件清单汇总

| 操作 | 文件 | 所属部分 | 说明 |
|------|------|---------|------|
| **新建** | `fincat/agent/resource_store.py` | 一 | ResourceStore：conversations + interaction_logs 读写 |
| **新建** | `fincat/agent/memory_store_v2.py` | 二 | MemoryStoreV2：SQLite memory_item + FAISS 向量 |
| **新建** | `fincat/agent/embedding.py` | 二 | EmbeddingEngine：bge-small-zh-v1.5 模型加载 + 向量生成 |
| **新建** | `fincat/agent/batch_extractor.py` | 二 | BatchExtractor：P1/P2/P3 三级触发 + LLM 提取 |
| **新建** | `fincat/agent/schema.sql` | 二 | SQLite 建表语句 |
| **新建** | `fincat/agent/category_manager.py` | 三 | CategoryManager：目录管理、归档、memory.md |
| **新建** | `fincat/agent/category_index.py` | 三 | CategoryIndex：frontmatter 解析索引 |
| **新建** | `fincat/agent/prediction_engine.py` | 四 | PredictionEngine：实时规则匹配 + 向量相似度 |
| **新建** | `fincat/agent/pattern_miner.py` | 四 | PatternMiner：时间周期性 + 语义关联 + 实体关联 |
| **新建** | `fincat/agent/topic_cache.py` | 四 | TopicCache：Top 3 管理、置信度、WebSocket 推送 |
| **新建** | `fincat/agent/rules.json` | 四 | 预测规则库 |
| **新建** | `templates/agent/batch_extract.md` | 二三 | 批量提取 Prompt 模板（含 Category 分配指导） |
| **新建** | `templates/agent/pattern_mine.md` | 四 | 深度挖掘 Prompt 模板 |
| **修改** | `fincat/agent/loop.py` | 一四 | 集成 ResourceStore + TopicCache；移除 ProactiveSkill |
| **修改** | `fincat/agent/context.py` | 三 | 导入改为 CategoryManager；build_system_prompt 改为注入 memory.md；删除 get_entity_summary + 7 个 read_file 提示 |
| **修改** | `fincat/agent/memory.py` | 三四五 | MemoryStore 删除 CATEGORY_FILES 相关方法；Dream Phase 2 补 SkillValidator 验证 + 注入最近 SkillEvolution；Dream Skill 发现数据源改为 memory_item (behavior) |
| **修改** | `fincat/agent/skill_evolver.py` | 五 | `on_task_completed()` 加 `_quick_skip()` 预过滤，减少 70%+ LLM 调用 |
| **修改** | `templates/agent/dream_phase1.md` | 五 | 更新 Skill 发现 Prompt：数据源从 history.jsonl 改为 behavior Item 列表 |
| **修改** | `fincat/agent/memory_monitor.py` | 四 | 移除 periodic/frequency 触发，保留 decay 归档和 scan_events |
| **修改** | `fincat/config/paths.py` | 一二 | 新增路径函数 |
| **修改** | `fincat/channels/websocket.py` | 四 | 新增 Topic 推送事件 |
| **修改** | `pyproject.toml` | 二 | 添加 `faiss-cpu`, `sentence-transformers`（bge-small-zh-v1.5）依赖 |
| **删除** | `fincat/agent/proactive_skill.py` | 四 | 由 PredictionEngine + TopicCache 替代 |
| **删除** | `fincat/agent/signal_store.py` | 四 | 不再需要 |
| **新建** | `tests/test_resource_store.py` | 一 | ResourceStore 单元测试（~10 用例） |
| **新建** | `tests/test_memory_store_v2.py` | 二 | MemoryStoreV2 + EmbeddingEngine 单元测试（~15 用例） |
| **新建** | `tests/test_batch_extractor.py` | 二 | BatchExtractor 单元测试（~8 用例） |
| **新建** | `tests/test_category_manager.py` | 三 | CategoryManager + CategoryIndex 单元测试（~12 用例） |
| **新建** | `tests/test_prediction_engine.py` | 四 | PredictionEngine 单元测试（~8 用例） |
| **新建** | `tests/test_pattern_miner.py` | 四 | PatternMiner 单元测试（~6 用例） |
| **新建** | `tests/test_topic_cache.py` | 四 | TopicCache 单元测试（~8 用例） |
| **新建** | `tests/test_skill_evolver.py` | 五 | SkillEvolver._quick_skip() 单元测试（~5 用例） |
| **新建** | `tests/test_integration_memory_flow.py` | 六 | 端到端集成测试（3 用例） |

---

## 验证

1. **Resources 写入**：发一条消息后，`conversations.jsonl` 新增一条记录，`resource_id` 格式正确
2. **双向关联**：MemoryItem 的 `resource_id` 指向正确的 resource；resource 的 `related_item_ids` 包含正确的 item_id
3. **SQLite 完整性**：memory_item 表写入后，vector_mapping 表有对应记录，FAISS 索引可检索
4. **向量去重**：连续发送语义相同的两条消息，第二条不创建新 Item，只更新 access_count
5. **Category 自动创建**：发送多条旅行相关消息后，`custom/travel/` 目录下自动出现 Category md 文件
6. **memory.md 注入**：system prompt 中只包含 memory.md 摘要（<500 字），不再有 7 个 md 的内容
7. **Topic 推送**：每日 03:00 后 topics.jsonl 更新，前端 WebSocket 收到 Top 3 Topic
8. **置信度调整**：用户点击 Topic 后，rules.json 中对应规则的 confidence +0.05
9. **交互日志**：用户点赞回复后，interaction_logs.jsonl 新增一条 `like_reply` 记录
10. **大小**：运行一周后检查 `resources/` 目录大小，确认在预期范围内

---

## 五、Skills 自进化系统优化 + 记忆系统适配

### 5.1 现有架构问题

| 问题 | 影响 | 严重度 |
|------|------|--------|
| SkillEvolver 每次 turn 都调 LLM | 简单问答（"几点了"）也触发 LLM 判断，浪费 token | **高** |
| Dream Phase 2 不走 SkillValidator | 用原始 `write_file` 创建 Skill，可能格式违规 | **高** |
| Dream 读取 history.jsonl | LLM 压缩摘要（31 条），丢失原始时间戳和细节，Skill 发现质量低 | **中** |
| Dream 和 SkillEvolver 无协调 | 两条路径独立写 `skills/`，各自做去重但不共享状态 | **低** |

### 5.2 修改 1：SkillEvolver 零 LLM 预过滤

**问题**：`loop.py:1090-1106`，每次 agent turn 完成后都触发 `SkillEvolver.on_task_completed()`，即使只是简单问答。

**改法**：在 `_llm_judge_worthiness()` 之前加一层 `_quick_skip()`，不满足条件直接跳过，不调 LLM。

```python
# skill_evolver.py — on_task_completed() 开头新增
def _quick_skip(self, ctx: TaskContext) -> bool:
    """零 LLM 快速跳过明显不值得保存的任务"""
    # 1. 无工具调用且回复很短 → 简单问答
    if not ctx.tools_used and len(ctx.assistant_response) < 500:
        return True
    # 2. 只有 1 个查询类工具（不涉及多步工作流）
    QUERY_TOOLS = {"stock_quote", "stock_kline", "web_search", "web_fetch", "read_file"}
    if len(ctx.tools_used) == 1 and ctx.tools_used[0] in QUERY_TOOLS:
        return True
    # 3. 消息过短（<20 字）→ 非任务型对话
    if len(ctx.user_message) < 20:
        return True
    return False
```

**效果**：预过滤后 SkillEvolver 的 LLM 调用量预计减少 **70-80%**。

### 5.3 修改 2：Dream Phase 2 补 SkillValidator 验证

**问题**：Dream Phase 2 用 `AgentRunner` + 原始 `write_file` 创建 `SKILL.md`，不经过 `SkillValidator` 的 5 步验证（frontmatter、工具名、描述长度、示例、metadata）。

**改法**：Phase 2 完成后，对写入的每个 Skill 补一次验证，不合格的移到 `skills/.invalid/`。

```python
# memory.py Dream.run() — Phase 2 完成后新增
if result and result.tool_events:
    for event in result.tool_events:
        if event["name"] == "write_file" and event["status"] == "ok":
            skill_path = Path(event["detail"])
            if skill_path.exists() and skill_path.name == "SKILL.md":
                is_valid, errors = SkillValidator(self._tools).validate(
                    skill_path.read_text(encoding="utf-8")
                )
                if not is_valid:
                    logger.warning("Dream created invalid skill {}: {}", skill_path.parent.name, errors)
                    invalid_dir = self._skills_dir / ".invalid"
                    invalid_dir.mkdir(exist_ok=True)
                    skill_path.parent.rename(invalid_dir / skill_path.parent.name)
```

### 5.4 修改 3：Dream 数据源从 history.jsonl 改为 memory_item (behavior)

**问题**：Dream Phase 1 读取 `history.jsonl`（LLM 压缩摘要，31 条），信息丢失严重。重构后 history.jsonl 将被 conversations.jsonl 替代。

**改法**：Dream Skill 发现改为读取 `memory_item` 表中 `memory_type="behavior"` 的条目。

| 数据源 | 优点 | 缺点 |
|--------|------|------|
| `history.jsonl`（当前） | 已有实现 | 压缩摘要，丢失时间戳和细节 |
| `resources/conversations.jsonl` | 原始对话，信息最完整 | 数据量大（每天 10-20 轮），需筛选 |
| `memory_item (behavior)`（推荐） | 结构化行为模式，噪声小，数据量小 | 依赖 BatchExtractor 先运行 |

**选择 `memory_item (behavior)` 的理由**：
1. 已经是 BatchExtractor 提取的结构化行为模式（如"每周五查黄金"、"买入前先看K线"），质量高于原始对话
2. 避免 Dream 重复处理原始对话（BatchExtractor 已经做过一轮提取）
3. 数据量小（预计 <100 条 behavior Item），LLM 处理成本低
4. 与记忆系统重构自然衔接：BatchExtractor 负责提取，Dream 负责从提取结果中发现 Skill

```python
# memory.py Dream.run() 修改数据源
# 原来：
entries = self.store.read_unprocessed_history(since_cursor=last_cursor)
if not entries:
    return False
batch = entries[:self.max_batch_size]
history_text = "\n".join(f"[{e['timestamp']}] {e['content']}" for e in batch)

# 改为：
behavior_items = self._item_store.query(memory_type="behavior", is_active=True)
if not behavior_items:
    return False
history_text = "\n".join(
    f"[{item['created_at']}] {item['summary']}" for item in behavior_items
)
```

### 5.5 修改 4：Dream + SkillEvolver 协调 — 注入最近 Skill 变更

**问题**：Dream 和 SkillEvolver 两条路径独立写 `skills/`，各自做去重但不共享状态。Dream 可能创建一个 SkillEvolver 刚创建的 Skill。

**改法**：Dream Phase 2 prompt 中追加最近 24 小时的 `skill_evolution.jsonl` 记录，让 LLM 知道哪些 Skill 刚被创建/修改。

```python
# memory.py Dream.run() — Phase 2 prompt 构建时追加
recent_evolutions = self._read_recent_skill_evolutions(hours=24)
if recent_evolutions:
    skills_section += "\n\n## Recently Created/Modified (last 24h)\n"
    skills_section += "\n".join(
        f"- {e['skill_name']} ({e['action']} at {e['timestamp']})"
        for e in recent_evolutions
    )
    skills_section += "\n\nDo NOT create skills that overlap with the above."

def _read_recent_skill_evolutions(self, hours: int = 24) -> list[dict]:
    """读取最近 N 小时的 skill_evolution.jsonl"""
    evolution_file = self.memory_dir / "skill_evolution.jsonl"
    if not evolution_file.exists():
        return []
    cutoff = datetime.now() - timedelta(hours=hours)
    results = []
    for line in evolution_file.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if datetime.fromisoformat(entry["timestamp"]) > cutoff:
            results.append(entry)
    return results
```

### 5.6 与记忆系统的适配性

| 组件 | 适配性 | 说明 |
|------|--------|------|
| `SkillEvolver` | ✅ 完全兼容 | 每次 turn 后触发，不依赖任何记忆组件 |
| `SkillManager` | ✅ 完全兼容 | 纯文件操作 + 自有 JSONL 日志 |
| `SkillUsageTracker` | ✅ 完全兼容 | 独立 JSONL，不依赖记忆架构 |
| `SkillLifecycleManager` | ✅ 完全兼容 | 只读 skills/ 目录 + UsageTracker |
| `SkillMemoryProvider` | ✅ 完全兼容 | 独立 SQLite 表 `skill_memory_ledger` |
| `SkillValidator` | ✅ 完全兼容 | 只依赖 ToolRegistry |
| `SkillPackager` | ✅ 完全兼容 | 纯文件打包 |
| `Dream`（Skill 路径） | ⚠️ 需适配 | 数据源从 history.jsonl 改为 memory_item (behavior) |

### 5.7 Skills 与记忆系统的交互点

```
┌─────────────────────────────────────────────────────────────┐
│                    Skills 自进化系统                          │
│                                                              │
│  SkillEvolver ──→ skill_manage ──→ SKILL.md                 │
│       │                              │                       │
│       │ (每 turn 后，经 _quick_skip 预过滤)                   │
│       ▼                              ▼                       │
│  TaskContext                    skills/ 目录                 │
│                                                              │
│  Dream ──→ [SKILL] 标记 ──→ write_file ──→ SKILL.md         │
│       │                              │                       │
│       │ (每日 03:00)                 │ (补 SkillValidator 验证)│
│       ▼                              ▼                       │
│  memory_item (behavior)        .invalid/ (不合格的)          │
│       ↑                                                     │
│       │ 数据源                                               │
│  BatchExtractor (第二部分)                                   │
├─────────────────────────────────────────────────────────────┤
│  协调机制：                                                  │
│  Dream Phase 2 prompt 注入最近 24h skill_evolution.jsonl     │
│  → 避免重复创建 SkillEvolver 刚创建的 Skill                   │
└─────────────────────────────────────────────────────────────┘
```

### 5.8 需要修改的文件

| 操作 | 文件 | 改动 | 优先级 |
|------|------|------|--------|
| **修改** | `fincat/agent/skill_evolver.py` | `on_task_completed()` 开头加 `_quick_skip()` 预过滤 | **高** — 省 70%+ LLM 调用 |
| **修改** | `fincat/agent/memory.py` | Dream Phase 2 完成后补 `SkillValidator` 验证；数据源改为 `memory_item (behavior)`；Phase 2 prompt 注入最近 SkillEvolution 记录 | **高** — 防止格式违规 + 提升发现质量 |
| **修改** | `templates/agent/dream_phase1.md` | 更新 Phase 1 Prompt，数据源从 history.jsonl 改为 behavior Item 列表 | **中** — 依赖记忆系统重构完成 |

**不需要修改的文件**：
- `skill_manage.py` — 完全独立
- `skill_tracker.py` — 完全独立
- `skill_lifecycle_manager.py` — 完全独立
- `skill_memory_provider.py` — 完全独立
- `skill_packager.py` — 完全独立
- `skills.py` — 完全独立

---

## 六、完整测试计划

### 6.1 测试策略

| 层级 | 范围 | 工具 | 覆盖率目标 |
|------|------|------|-----------|
| 单元测试 | 单个类/方法 | pytest + mock | 新建类 100%，修改类 80% |
| 集成测试 | 多组件协作 | pytest + 临时目录 | 关键路径 100% |
| 端到端测试 | 完整数据流 | pytest + 真实 SQLite/FAISS | 主流程 3 条 |

### 6.2 测试文件清单

| 文件 | 测试目标 | 测试数量 |
|------|---------|---------|
| `tests/test_resource_store.py` | ResourceStore | ~10 |
| `tests/test_memory_store_v2.py` | MemoryStoreV2 + EmbeddingEngine | ~15 |
| `tests/test_batch_extractor.py` | BatchExtractor | ~8 |
| `tests/test_category_manager.py` | CategoryManager + CategoryIndex | ~12 |
| `tests/test_prediction_engine.py` | PredictionEngine | ~8 |
| `tests/test_pattern_miner.py` | PatternMiner | ~6 |
| `tests/test_topic_cache.py` | TopicCache | ~8 |
| `tests/test_skill_evolver.py` | SkillEvolver._quick_skip() | ~5 |
| `tests/test_integration_memory_flow.py` | 端到端数据流 | ~3 |

### 6.3 各测试文件详细用例

#### 6.3.1 `tests/test_resource_store.py`

```python
class TestResourceStore:
    def test_add_conversation_returns_resource_id(self, tmp_path):
        """写入一条对话，返回 res_ 开头的 resource_id"""

    def test_add_conversation_content_hash(self, tmp_path):
        """相同内容两次写入，content_hash 一致"""

    def test_add_interaction_log_categories(self, tmp_path):
        """三种 category（conversation_interaction/memory_management/system_operation）都能写入"""

    def test_get_by_resource_id_found(self, tmp_path):
        """按 resource_id 查找已存在的记录"""

    def test_get_by_resource_id_not_found(self, tmp_path):
        """按 resource_id 查找不存在的记录，返回 None"""

    def test_query_by_session(self, tmp_path):
        """同一 session_id 写入 3 条，查询返回 3 条"""

    def test_query_by_timerange(self, tmp_path):
        """时间范围查询：范围内 2 条，范围外 1 条"""

    def test_query_logs_with_filters(self, tmp_path):
        """按 category + action 过滤交互日志"""

    def test_read_recent_conversations(self, tmp_path):
        """读取最近 24 小时对话，排除 24 小时前的"""

    def test_rotate_if_needed(self, tmp_path):
        """文件超过阈值时自动按月归档"""
```

#### 6.3.2 `tests/test_memory_store_v2.py`

```python
class TestEmbeddingEngine:
    def test_embed_returns_512_dim_vector(self):
        """单条文本 embedding 返回 512 维向量"""

    def test_embed_batch_consistency(self):
        """同一条文本单条 embedding 和 batch embedding 结果一致"""

    def test_cosine_similarity_identical(self):
        """相同文本余弦相似度 = 1.0"""

    def test_cosine_similarity_different(self):
        """不同文本余弦相似度 < 0.9"""

class TestMemoryStoreV2:
    def test_add_item_returns_id(self, tmp_path):
        """创建 Item 返回 item_id，created_at 自动填充"""

    def test_add_item_sqlite_row(self, tmp_path):
        """写入后 SQLite 查询到完整记录"""

    def test_get_item_not_found(self, tmp_path):
        """查询不存在的 item_id 返回 None"""

    def test_query_by_memory_type(self, tmp_path):
        """按 memory_type 过滤"""

    def test_query_by_category_id(self, tmp_path):
        """按 category_id 过滤"""

    def test_update_item(self, tmp_path):
        """更新指定字段后重新查询验证"""

    def test_deactivate_item(self, tmp_path):
        """软删除后 is_active=0，query 默认不返回"""

    def test_search_similar_returns_match(self, tmp_path):
        """语义相似的 summary 检索到已有 Item（相似度 > 0.9）"""

    def test_search_similar_no_match(self, tmp_path):
        """语义不同的 summary 检索不到"""

    def test_embed_and_index_updates_embedded_at(self, tmp_path):
        """embed_and_index 后 embedded_at 非 NULL"""

    def test_embed_and_index_vector_mapping(self, tmp_path):
        """vector_mapping 表有对应记录，model_name=bge-small-zh-v1.5"""

    def test_rebuild_faiss_index_flat(self, tmp_path):
        """从 SQLite 重建 Flat 索引，检索结果一致"""

    def test_get_unembedded_items(self, tmp_path):
        """写入 2 条 Item（1 条已嵌入，1 条未嵌入），get_unembedded_items 返回 1 条"""

    def test_close_and_reopen(self, tmp_path):
        """关闭后重新打开，数据不丢失"""
```

#### 6.3.3 `tests/test_batch_extractor.py`

```python
class TestBatchExtractor:
    def test_add_to_pending(self, tmp_path):
        """add_to_pending 后 pending_pool 增加"""

    def test_should_flush_pool_full(self, tmp_path):
        """池满 N 条时 should_flush 返回 True"""

    def test_should_flush_timeout(self, tmp_path):
        """距上次 > 1 分钟时 should_flush 返回 True"""

    def test_extract_immediate(self, tmp_path):
        """P1 即时提取：1 条 Resource → 生成 N 个 Item + 向量"""

    def test_extract_batch(self, tmp_path):
        """P2 批量提取：3 条 Resource → LLM 提取 → 去重 → 写入"""

    def test_dedup_skips_similar(self, tmp_path):
        """去重：语义相似的 Item 不重复创建，只更新 access_count"""

    def test_extract_pending_retries(self, tmp_path):
        """P3 兜底：之前失败的 Resource 重新提取"""

    def test_related_item_ids_backfill(self, tmp_path):
        """提取完成后，Resource 的 related_item_ids 被回填"""
```

#### 6.3.4 `tests/test_category_manager.py`

```python
class TestCategoryIndex:
    def test_scan_finds_all_md_files(self, tmp_path):
        """扫描目录后索引包含所有带 frontmatter 的 md 文件"""

    def test_parse_frontmatter(self, tmp_path):
        """解析 YAML frontmatter 返回正确字段"""

    def test_get_by_id(self, tmp_path):
        """按 category_id 查询"""

    def test_list_by_type(self, tmp_path):
        """按 type 过滤 system/custom/user"""

class TestCategoryManager:
    def test_get_or_create_existing(self, tmp_path):
        """获取已存在的 Category，不重复创建"""

    def test_get_or_create_new(self, tmp_path):
        """创建新 Category，目录和 md 文件自动生成"""

    def test_add_item_to_category(self, tmp_path):
        """写入 Item 后 md 文件包含该条目"""

    def test_archive_category(self, tmp_path):
        """归档后 is_active=false，文件移到 archive/"""

    def test_restore_category(self, tmp_path):
        """恢复归档后文件移回原位置"""

    def test_regenerate_memory_md(self, tmp_path):
        """regenerate_memory_md 后 memory.md 包含所有活跃 Category 摘要"""

    def test_memory_md_under_500_chars(self, tmp_path):
        """memory.md 内容 < 500 字"""

    def test_find_best_category(self, tmp_path):
        """向量匹配：summary 与 knowledge/fund.md 最相似 → 返回 knowledge category_id"""

    def test_auto_create_category(self, tmp_path):
        """多个旅行相关 Item → 自动创建 custom/travel/ Category"""

    def test_archive_candidates(self, tmp_path):
        """30 天无访问 + activity_score < 0.2 → 出现在候选列表"""

    def test_builtin_folders_exist(self, tmp_path):
        """5 个内置文件夹（profile/knowledge/preferences/behavioral_insights/compliance）自动创建"""
```

#### 6.3.5 `tests/test_prediction_engine.py`

```python
class TestPredictionEngine:
    def test_match_rules_keyword(self):
        """用户消息包含 '买入黄金' → 匹配 rule_buy_analysis"""

    def test_match_rules_no_match(self):
        """用户消息 '今天天气不错' → 无匹配"""

    def test_match_rules_context_filter(self):
        """规则要求 context.category=stock，消息无 stock 实体 → 不匹配"""

    def test_update_confidence_click(self):
        """click 反馈后 confidence +0.05"""

    def test_update_confidence_reject(self):
        """reject 反馈后 confidence -0.10"""

    def test_low_confidence_disabled(self):
        """confidence < 0.3 的规则不再匹配"""

    def test_predict_merges_sources(self):
        """规则匹配 + 向量检索合并去重"""

    def test_predict_returns_empty_on_no_match(self):
        """无任何匹配时返回空列表"""
```

#### 6.3.6 `tests/test_pattern_miner.py`

```python
class TestPatternMiner:
    def test_mine_time_periodicity_weekly(self, tmp_path):
        """连续 5 周周五 15:00 查黄金 → 输出 period_type=weekly, day_of_week=4"""

    def test_mine_time_periodicity_daily(self, tmp_path):
        """连续 20 天早 9:00 查行情 → 输出 period_type=daily, peak_hours=(9,10)"""

    def test_mine_time_periodicity_low_confidence(self, tmp_path):
        """不规律的行为 → 置信度 < 0.8 → 不输出"""

    def test_mine_semantic_association(self, tmp_path):
        """三亚旅行 → 85% 概率查预算 → 输出语义关联"""

    def test_mine_entity_association(self, tmp_path):
        """特斯拉 + 比亚迪高频共现 → 输出实体关联"""

    def test_run_daily_merges_results(self, tmp_path):
        """三种挖掘合并 + 过滤置信度 < 0.8 → 返回 Topic 列表"""
```

#### 6.3.7 `tests/test_topic_cache.py`

```python
class TestTopicCache:
    def test_set_topics_sorted_by_priority(self, tmp_path):
        """写入 5 个 Topic，get_topics 返回 priority 最高的 3 个"""

    def test_get_topics_empty(self, tmp_path):
        """无 Topic 时返回空列表"""

    def test_remove_topic(self, tmp_path):
        """移除后 get_topics 不再返回该 Topic"""

    def test_clean_expired(self, tmp_path):
        """expires_at 已过期的 Topic 被清理"""

    def test_adjust_confidence_click(self, tmp_path):
        """click 反馈后对应 Topic 的 confidence +0.05"""

    def test_adjust_confidence_reject_removes(self, tmp_path):
        """reject 反馈后 Topic 被移除"""

    def test_on_user_feedback_logs_interaction(self, tmp_path):
        """用户反馈后 interaction_logs.jsonl 新增记录"""

    def test_persistence_across_reopen(self, tmp_path):
        """关闭后重新打开，Topic 不丢失"""
```

#### 6.3.8 `tests/test_skill_evolver.py`

```python
class TestSkillEvolverQuickSkip:
    def test_skip_no_tools_short_response(self):
        """无工具 + 回复 < 500 字 → 跳过"""

    def test_skip_single_query_tool(self):
        """只有 stock_quote 一个查询工具 → 跳过"""

    def test_skip_short_message(self):
        """用户消息 < 20 字 → 跳过"""

    def test_no_skip_complex_task(self):
        """3+ 工具 + 长消息 → 不跳过"""

    def test_no_skip_error_fixing(self):
        """有工具调用 + 回复包含错误修复 → 不跳过"""
```

#### 6.3.9 `tests/test_integration_memory_flow.py`

```python
class TestIntegrationMemoryFlow:
    async def test_full_resource_to_memory_item_flow(self, tmp_path):
        """端到端：用户消息 → ResourceStore 写入 → BatchExtractor 提取 → MemoryStoreV2 写入 → FAISS 可检索
        验证：
        1. conversations.jsonl 新增 1 条
        2. memory_item 表新增 N 条
        3. vector_mapping 表新增 N 条
        4. FAISS 检索 summary 返回对应 Item
        5. Item 的 resource_id 指向正确的 Resource
        """

    async def test_full_category_memory_md_flow(self, tmp_path):
        """端到端：memory_item 写入 → CategoryManager 分配 Category → md 文件更新 → memory.md 更新
        验证：
        1. Category 目录下出现对应 md 文件
        2. md 文件包含 frontmatter + Item 条目
        3. memory.md 包含该 Category 的摘要
        4. memory.md < 500 字
        """

    async def test_full_prediction_topic_flow(self, tmp_path):
        """端到端：PatternMiner 挖掘 → TopicCache 写入 → 前端推送
        验证：
        1. PatternMiner.run_daily() 返回置信度 ≥ 0.8 的 Topic
        2. TopicCache.get_topics() 返回 Top 3
        3. Topic 格式匹配前端 Topic 接口
        4. 用户 reject 后 Topic 被移除
        """
```

### 6.4 测试运行命令

```bash
# 全部测试
pytest tests/ -v

# 按模块运行
pytest tests/test_resource_store.py -v
pytest tests/test_memory_store_v2.py -v
pytest tests/test_batch_extractor.py -v
pytest tests/test_category_manager.py -v
pytest tests/test_prediction_engine.py -v
pytest tests/test_pattern_miner.py -v
pytest tests/test_topic_cache.py -v
pytest tests/test_skill_evolver.py -v
pytest tests/test_integration_memory_flow.py -v

# 只运行集成测试
pytest tests/test_integration_memory_flow.py -v -m integration

# 覆盖率报告
pytest tests/ --cov=fincat.agent --cov-report=html
```

### 6.5 测试前置条件

| 依赖 | 说明 |
|------|------|
| `pytest` | 测试框架 |
| `pytest-asyncio` | 异步测试支持 |
| `pytest-cov` | 覆盖率报告 |
| `tmp_path` fixture | 每个测试用例使用独立临时目录 |
| `bge-small-zh-v1.5` 模型 | 首次运行自动下载（~93MB） |
| `faiss-cpu` | 向量索引 |
