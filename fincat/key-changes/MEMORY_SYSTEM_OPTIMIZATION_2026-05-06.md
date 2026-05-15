# Fincat 记忆系统优化全景方案（2026-05-06）

> 日期：2026-05-06
> 本文档覆盖记忆系统三个阶段的完整优化路径，从实时预筛到高级记忆能力，再到主动智能。
> 关联文件：memory_prefilter.py, prefilter_keywords.json, memory.py, loop.py, dream_phase1.md, knowledge/store.py, context.py

---

## 总览

| 阶段 | 主题 | 核心能力 | 状态 |
|------|------|----------|------|
| 阶段一 | 实时预筛 + Dream 精炼 | 零成本实时提取 + LLM 每日精炼 | **已完成** |
| 阶段二 | 高级记忆能力 | 实体图谱 + 多跳查询 + 三阶段检索 + 记忆挂载 | 设计中 |
| 阶段三 | 主动智能 | 行为模式预测 + 主动洞察 + 事件驱动 | 待设计 |

---

---

## 阶段一 Context

当前记忆系统依赖 Dream（每日 03:00 定时任务）做 LLM 语义分析来提取和分类记忆，存在两个核心问题：
1. **实时性缺失**：用户对话中产生的高价值信息要等到次日凌晨才被整理，当轮对话无法利用
2. **无金融特化**：分类和提取都是通用 LLM 判断，没有金融场景专属的关键词/实体识别

原计划的 MemoryClassifier + MemoryCompressor 方案（见 COGNIX_MIXED_STORAGE_ARCHITECTURE_UPGRADE_2026-05-01.md）解决了实时性，但完全抛弃了 Dream 的 LLM 语义理解优势。本方案将二者结合：**实时预筛（规则驱动，零成本） + Dream 精炼（LLM 驱动，高质量）**。

---

### 1.1 LLM 语义理解 vs 关键词规则匹配 对比分析

| 维度 | 关键词规则匹配 | LLM 语义理解 |
|------|--------------|-------------|
| **延迟** | 0ms（纯 CPU） | 2-5s（API 调用） |
| **成本** | 0 token | 每次 500-2000 token |
| **召回率** | 60-70%（依赖关键词覆盖率） | 90%+（理解语义和上下文） |
| **精确率** | 85%+（规则明确，误报少） | 75-85%（可能过度提取） |
| **金融实体** | 擅长（正则匹配股票代码 `600519`、指标 `PE`） | 擅长（理解"茅台估值偏高"是投资判断） |
| **隐含信息** | 无法捕捉（"我还是保守一点吧"→ 风险偏好） | 可以捕捉 |
| **去重能力** | 弱（字面匹配） | 强（语义去重） |
| **分类准确性** | 高（规则确定） | 中高（偶有错分） |
| **可维护性** | 需要持续更新关键词库 | 自适应 |

**结论**：两者互补，不是替代关系。关键词做"粗筛"（快、便宜、高精确率），LLM 做"精炼"（慢、贵、高召回率）。

---

### 1.2 当前分类体系 vs 计划分类体系

### 当前 6 类（偏行为域，已上线运行）

| 分类 | 文件 | 用途 |
|------|------|------|
| `preference` | user_preferences.md | 风险偏好、行业偏好、沟通风格 |
| `knowledge` | product_knowledge.md | 产品知识、金融术语 |
| `case` | conversation_cases.md | 服务案例、FAQ |
| `compliance` | compliance_rules.md | 合规规则、禁语 |
| `profile` | user_profile.md | 用户画像、资产快照 |
| `insight` | behavioral_insights.md | 决策逻辑、经验教训 |

### 计划 5 类（偏数据域，未实施）

| 分类 | 用途 |
|------|------|
| `user` | 用户基本信息 |
| `settings` | 系统设置、偏好 |
| `market` | 市场行情、政策 |
| `research` | 研报、分析 |
| `regulation` | 合规、监管 |

### 分析

当前分类**优于**计划分类，原因：
1. **`profile` vs `user`**：`profile` 更精准，包含资产快照、税务身份等金融画像
2. **`insight` 是关键差异**：记录用户决策逻辑（"因为资金小所以保守"），这是理解用户行为模式的核心，计划分类完全没有
3. **`case` 有实用价值**：服务案例对客服场景直接有用，计划分类没有对应项
4. **`market` 和 `research` 可合并到 `knowledge`**：当前 `knowledge` 已经涵盖产品知识和市场信息
5. **`compliance` vs `regulation`**：本质相同，当前命名更贴切

**决定**：保持当前 6 类分类体系不变，不切换到计划的 5 类。

---

### 1.3 MemoryItem（衰减/置信度/频率）与阶段三的关系

MemoryItem 是阶段三"主动智能"的数据基础：

| MemoryItem 属性 | 阶段三用途 |
|-----------------|-----------|
| `frequency >= 3` | 触发 REMIND：主动提醒"你多次关注XX" |
| `decay_score < 0.2` | 触发 ARCHIVE：自动归档过时记忆 |
| `age_days >= 30` | 触发周期性提醒："你一个月没看持仓了" |
| `confidence` | 置信度越高，主动推送的优先级越高 |
| `PATTERN` 触发（未实现） | 检测行为模式变化，主动调整建议 |
| `EVENT` 触发（未实现） | 外部事件驱动的主动通知 |

当前 MemoryMonitor 的三个已实现触发器（frequency/threshold/periodic）就是阶段三的基础骨架。

---

### 1.4 组合架构设计

### 整体流程

```
用户对话
    │
    ▼
┌─────────────────────────────────────────┐
│  层 1：实时预筛（RealTimePreFilter）      │  ← 新增，零 LLM 成本
│  · 关键词规则匹配                         │
│  · 金融实体正则识别（股票代码、财务指标）    │
│  · 高置信度条目 → 直接写入 category markdown │
│  · 中置信度条目 → 加入 Dream 待处理队列     │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  层 2：会话压缩（Consolidator）           │  ← 已有，不变
│  · Token 预算超限时压缩旧消息到 history    │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  层 3：Dream 精炼（每日 03:00）           │  ← 已有，增强
│  · 分析 history + 预筛队列                │
│  · LLM 语义提取（捕捉预筛遗漏的记忆）      │
│  · 去重合并（与预筛结果去重）              │
│  · 过期检测 + 分类修正                    │
│  · 提取 MemoryItem → MemoryMonitor       │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│  层 4：MemoryMonitor 触发器              │  ← 已有，不变
│  · frequency/threshold/periodic 触发     │
│  · 主动推送（阶段三基础）                 │
└─────────────────────────────────────────┘
```

### 核心新增组件：RealTimePreFilter

**文件**：`fincat/agent/memory_prefilter.py` + `fincat/agent/prefilter_keywords.json`

**职责**：在每轮对话结束后，零 LLM 成本地从用户消息和 Agent 回复中提取高价值记忆。

**置信度策略（两级）**：
- **high**：正则命中（股票代码、财务指标、金额）或 high 关键词命中 → 直接写入 category markdown + MemoryItemStore
- **medium**：medium 关键词命中 → 写入 `.prefilter_queue.jsonl`，等 Dream 语义验证

**三层扫描逻辑**：

1. **层 1：金融实体正则（结构化数据）**
   - A股代码 `[036]\d{5}` → `knowledge`
   - 财务指标 `PE|PB|ROE|净利润|营收|毛利率` → `knowledge`
   - 金额 `\d+(\.\d+)?\s*(万|亿|元)` → `profile`
   - 正则命中 = high 置信度，提取关键词前后各 30 字符作为上下文

2. **层 2：关键词匹配（语义暗示）**
   - high 关键词命中 = high 置信度
   - medium 关键词命中 = medium 置信度
   - 命中时提取关键词前后各 30 字符上下文，避免存储脱离语境的碎片

3. **层 3：去重**
   - 同一条信息可能被正则和关键词同时命中，按 category + content 相似度去重

**关键词库方案**：使用可配置 JSON 文件 `fincat/agent/prefilter_keywords.json`，支持运行时热更新（检测 mtime 变化重新加载）。初始词库 ~80 词。

### Dream 增强：处理预筛队列

Dream Phase 1 中除了读取 `history.jsonl`，还读取 `.prefilter_queue.jsonl` 中的待处理条目。Phase 1 prompt 增加预筛队列上下文，指示 LLM 对每条 CONFIRM/RECLASSIFY/DISCARD。

### 集成点

在 `loop.py` 每轮对话结束后的后台任务区域，新增预筛调用：`process_turn()` + `flush()`。

---

### 1.5 阶段一关键文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `fincat/agent/prefilter_keywords.json` | **新建** | 可配置关键词库（~80 词） |
| `fincat/agent/memory_prefilter.py` | **新建** | 实时预筛器核心 |
| `fincat/agent/loop.py` | 修改 | 集成预筛调用 |
| `fincat/agent/memory.py` | 修改 | Dream 读取预筛队列 |
| `fincat/templates/agent/dream_phase1.md` | 修改 | 增加预筛队列上下文 |
| `tests/agent/test_memory_prefilter.py` | **新建** | 预筛器单元测试（21 个，全部通过） |

**不修改的文件**：memory_item.py、memory_monitor.py、memory_manager.py、context.py、分类体系（保持当前 6 类）。

---
---

# 阶段二：P1 高级记忆能力落地方案

## 现状分析

### 已有基础设施

| 组件 | 状态 | 能力 |
|------|------|------|
| RAG Knowledge Store | 已有 | 三通道混合检索（标题+FTS5+事实），rag_facts 有 entity 字段 |
| SQLite Memory Store | 已有 | FTS5 全文索引，memory_index 表，reflection_vault 向量搜索（可选） |
| MemoryItemStore | 已有 | 原子记忆单元，衰减/频率/置信度 |
| PreFilter + Dream | 阶段一完成 | 实时预筛 + LLM 精炼的组合管线 |

### 关键差距

| 能力 | 缺失原因 |
|------|----------|
| 实体关系图谱 | rag_facts.entity 是自由文本，无实体表、无关系表、无归一化 |
| 粒度检索 | memory_index 按文件粒度建索引，无法定位到单条记忆条目 |
| 多跳查询 | 无实体图谱，无法遍历"公司→产品→费率→监管"链路 |
| 统一检索编排 | memory/RAG/reflection 各自独立查询，无协调器 |
| 记忆挂载 | 无外部文档导入工具 |

---

## 五个子功能拆解

### 子功能 1：实体关系图谱

**目标**：在 knowledge.db 中新增实体表和关系表，从已有数据中自动提取实体并建链。

**新增表**（knowledge.db）：

```sql
CREATE TABLE IF NOT EXISTS rag_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT UNIQUE NOT NULL,      -- 归一化 ID，如 "boc_mortgage_rate"
    name TEXT NOT NULL,                   -- 显示名，如 "中国银行房贷利率"
    entity_type TEXT NOT NULL,            -- product / institution / regulation / metric / concept
    aliases TEXT,                         -- JSON 别名列表，如 ["中行房贷", "BOC房贷"]
    metadata TEXT,                        -- JSON 扩展属性
    source TEXT,                          -- 来源：rag_facts / memory / manual
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rag_entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id TEXT NOT NULL,
    target_entity_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,          -- offers / regulates / has_rate / belongs_to / related_to
    weight REAL DEFAULT 1.0,              -- 关系强度
    metadata TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_entity_id) REFERENCES rag_entities(entity_id),
    FOREIGN KEY (target_entity_id) REFERENCES rag_entities(entity_id)
);

CREATE INDEX IF NOT EXISTS idx_entity_relations_source ON rag_entity_relations(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_relations_target ON rag_entity_relations(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_type ON rag_entities(entity_type);
```

**实体提取管线**：

1. **从 rag_facts 提取**：遍历所有 facts，用 entity 字段归一化
   - "中国银行" / "BOC" / "中行" → 同一实体 `boc`
   - entity_type 由 fact_type 推断（rate → metric, product_name → product）

2. **从 memory category 条目提取**：Dream Phase 1 已经输出 `[FILE] category: content`，可以在 Dream 中增加实体提取步骤

3. **从 rag_sections 提取**：用正则 + LLM 从文档章节中提取机构名、产品名、法规名

**实体归一化策略**（不依赖 LLM，纯规则）：
- 维护 `entity_aliases.json` 配置文件，映射别名到归一化 ID
- 正则匹配常见模式：`XX银行`、`XX基金`、`XX规定/办法/通知`
- 对 rag_facts.entity 做去重归一化后，自动建立 fact → entity 的关联

**关系建链**：
- 同一 doc_id 下的 facts 自动建立 `belongs_to` 关系
- entity 出现在同一 section 中的 facts 建立 `related_to` 关系
- fact_type 为 rate/fee 的 entity 建立 `has_rate` 关系

**覆盖范围**：实体图谱同时覆盖 RAG 知识库和长期记忆两个数据源：

| 数据源 | 实体示例 | 来源 |
|--------|---------|------|
| rag_facts (knowledge.db) | 中国银行、公积金贷款、LPR利率 | 公共金融文档 |
| memory category 条目 | 600519贵州茅台、保守型、新能源板块 | 用户对话提取（PreFilter/Dream） |
| MemoryItemStore | 用户持仓标的、关注板块 | Dream Phase 1 提取 |

**跨系统关联**：当记忆中出现"600519贵州茅台"且 RAG 中也有该实体时，自动建立 `memory ↔ rag` 的关联，实现"用户关注的产品 → 产品详情 → 相关法规"的链路。

---

### 子功能 1.5：长期记忆条目级索引

**目标**：将 memory_index 从文件粒度升级到条目粒度，支持精确定位单条记忆。

**当前问题**：

`memory_index` 表每行对应一个 category markdown 文件（如整个 `user_preferences.md`），`search_memory()` 只能告诉你是哪个文件相关，无法定位到具体条目。而 `parse_category_metadata()` 已经能解析每个 `<!-- item:... -->` 块的元数据。

**新增表**（memory.db）：

```sql
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id TEXT UNIQUE NOT NULL,        -- 对应 <!-- item:xxx --> 中的 xxx
    category TEXT NOT NULL,               -- preference / knowledge / case / compliance / profile / insight
    content TEXT NOT NULL,                -- 条目正文
    file_path TEXT NOT NULL,              -- 所在 markdown 文件路径
    frequency INTEGER DEFAULT 1,
    decay_score REAL DEFAULT 1.0,
    confidence REAL DEFAULT 0.5,
    entities TEXT,                         -- JSON: 从条目中提取的实体 ID 列表
    md5_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_entries_fts USING fts5(
    content,
    category,
    tokenize='unicode61'
);
```

**索引构建**：

`sync_from_markdown()` 增强为条目级索引：
- 用 `parse_category_metadata()` 解析每个 `<!-- item:... -->` 块
- 每个条目写入 `memory_entries` 一行
- 同时用 PreFilter 的实体正则提取条目中的实体，写入 `entities` JSON 字段
- MD5 哈希按条目计算，只更新变化的条目

**条目级检索**：

```python
def search_memory_entries(
    self,
    query: str,
    category: str | None = None,
    min_confidence: float = 0.0,
    limit: int = 10,
) -> list[MemoryEntry]:
    """FTS5 搜索单条记忆条目，而非整个文件。"""
    ...
```

**与实体图谱的衔接**：

条目中的实体 ID 与 `rag_entities` 表关联：
- 记忆条目"600519贵州茅台 PE:30.5" → entities: ["600519", "贵州茅台"]
- RAG 实体"贵州茅台" → entity_id: "kweichow_moutai"
- 自动关联：记忆条目 ↔ RAG 实体 → 可以从用户记忆跳转到产品详情

---

### 子功能 2：三阶段按需检索

**目标**：降低 Token 消耗，Agent 默认只看摘要，需要时逐层展开。

**当前问题**：context.py 中 `get_category_summary(max_lines=5)` 每次注入全部 6 个分类的前 5 行，即使大部分与当前问题无关。

**三阶段设计**：

| 阶段 | 触发时机 | 内容 | Token 消耗 |
|------|----------|------|-----------|
| Stage 1: 摘要 | 系统 prompt（每轮） | 实体名称列表 + 分类关键词 | ~200 token |
| Stage 2: 相关条目 | 预筛命中 / 工具调用 | 与当前问题匹配的记忆条目全文 | ~500-1000 token |
| Stage 3: 完整上下文 | Agent 主动 read_file | 完整 category markdown 文件 | 2000+ token |

**实现方案**：

**Stage 1 改造**（修改 context.py）：
- 替换 `get_category_summary(max_lines=5)` 为 `get_entity_summary()`
- 返回格式：每个分类列出实体名称和关键数值，不展开详情

```
# Memory (summaries)
- preference: 风险偏好=保守型, 关注板块=新能源
- knowledge: 600519贵州茅台(PE:30.5), 公积金贷款利率3.1%
- profile: 资产50万, 月入2万, 已婚
```

**Stage 2 改造**（增强 PreFilter）：
- PreFilter 命中时，除了写入 markdown，还将相关条目注入当轮上下文
- 在 loop.py 中，prefilter_items 作为上下文附加到 user message

**Stage 3 保持不变**：
- Agent 已经可以通过 `read_file` 工具读取完整 category markdown

---

### 子功能 3：多跳查询

**目标**：支持"中国银行有哪些理财产品？这些产品的收益率是多少？有没有相关风险提示？"这类跨实体链式查询。

**实现方案**：在 rag_search 工具中新增 `entity_traverse` 模式。

**查询流程**：

```
用户: "中行的理财产品收益率和风险提示"
  ↓
Step 1: 实体解析 → "中行" → entity_id = "boc"
  ↓
Step 2: 关系遍历
  boc --[offers]--> product entities (中行理财A, 中行理财B, ...)
  boc --[regulates]--> regulation entities (银行理财管理办法, ...)
  ↓
Step 3: 关联检索
  对每个关联实体，查询 rag_facts 和 rag_sections
  ↓
Step 4: 结果聚合
  合并去重，按相关性排序，标注来源
```

**核心方法**（新增到 RAGKnowledgeStore）：

```python
def search_by_entity(
    self,
    entity_name: str,
    relation_types: list[str] | None = None,
    max_hops: int = 2,
    top_k: int = 10,
) -> list[RAGResult]:
    """从实体出发，沿关系链遍历，聚合关联的 facts 和 sections。"""
    ...

def resolve_entity(self, name_or_alias: str) -> str | None:
    """将别名解析为归一化 entity_id。先查 aliases 配置，再查 rag_entities 表。"""
    ...
```

**query decomposition**（在 rag_search 工具层）：
- 不需要 LLM 做 query decomposition
- 用正则提取查询中的实体名，直接做 entity traversal
- 复杂查询由 Agent 自行拆分为多次工具调用（已有能力）

---

### 子功能 4：记忆挂载

**目标**：支持一键导入外部研报、财报、行业文档，自动成为可查询记忆。

**实现方案**：复用 knowledge/ingest.py 的 IngestPipeline，新增 CLI 入口 + Agent 工具。

**CLI 入口**：

```bash
python -m fincat.knowledge.mount <file_or_dir> [--category product|regulation|business_rules|tax]
```

**Agent 工具**：

```python
class MemoryMountTool(Tool):
    """挂载外部文档到知识库。Agent 可在对话中调用。"""
    name = "memory_mount"
    description = "导入外部文档（PDF/HTML/Markdown）到知识库，自动成为可查询记忆"

    async def run(self, file_path: str, category: str = "product") -> str:
        # 复用 IngestPipeline
        ...
```

**自动分类**：
- 按文件路径前缀推断：`boc/` → product, `csrc/` → regulation
- 按文件名关键词推断：`费率` → product, `合规` → regulation
- 支持手动指定 `--category` 覆盖

---

## 阶段二实施步骤

### Step 1：实体表 + 关系表（knowledge.db）
- 修改 `knowledge/store.py`：新增 CREATE TABLE 语句
- 新增 `add_entity()`、`add_relation()`、`resolve_entity()` 方法
- 新增 `entity_aliases.json` 配置文件

### Step 2：实体提取管线
- 新建 `knowledge/entity_extractor.py`
- 从 rag_facts 提取：遍历 facts，归一化 entity 字段
- 从 rag_sections 提取：正则匹配机构名、产品名、法规名
- 调用时机：ingest 完成后自动运行，或手动 `python -m fincat.knowledge.extract_entities`

### Step 3：多跳查询（rag_search 增强）
- 在 `knowledge/store.py` 新增 `search_by_entity()` 方法
- 在 `agent/tools/rag.py` 新增 entity_traverse 模式
- 实现实体解析 → 关系遍历 → 关联检索 → 结果聚合

### Step 4：三阶段检索改造
- 修改 `context.py`：Stage 1 摘要改为实体+关键数值格式
- 修改 `loop.py`：Stage 2 将 PreFilter 命中结果注入当轮上下文
- Stage 3 保持 read_file 不变

### Step 5：记忆挂载
- 新建 `knowledge/mount.py`：CLI 入口
- 在 Agent 工具中注册 `memory_mount` 工具
- 复用 IngestPipeline

---

## 阶段二关键文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| **RAG 侧** | | |
| `fincat/knowledge/store.py` | 修改 | 新增实体表、关系表、search_by_entity() |
| `fincat/knowledge/entity_extractor.py` | **新建** | 实体提取 + 归一化管线 |
| `fincat/knowledge/entity_aliases.json` | **新建** | 实体别名配置 |
| `fincat/knowledge/mount.py` | **新建** | 记忆挂载 CLI |
| `fincat/agent/tools/rag.py` | 修改 | 新增 entity_traverse 模式 |
| **长期记忆侧** | | |
| `fincat/agent/memory_sqlite.py` | 修改 | 新增 memory_entries 表、条目级索引、search_memory_entries() |
| `fincat/agent/memory.py` | 修改 | sync_from_markdown() 增强为条目级解析 + 实体提取 |
| **检索编排** | | |
| `fincat/agent/context.py` | 修改 | Stage 1 摘要改为实体+关键数值格式 |
| `fincat/agent/loop.py` | 修改 | Stage 2 预筛结果注入上下文 |
| **测试** | | |
| `tests/knowledge/test_entity_extractor.py` | **新建** | 实体提取测试 |
| `tests/knowledge/test_entity_search.py` | **新建** | 多跳查询测试 |
| `tests/agent/test_memory_entries.py` | **新建** | 条目级索引测试 |

---

## 阶段二实施优先级

| 子功能 | 优先级 | 预计工作量 | 价值 |
|--------|--------|-----------|------|
| 实体表 + 关系表（RAG 侧） | P0 | 1天 | 多跳查询的基础 |
| 条目级索引（长期记忆侧） | P0 | 1天 | 记忆精确定位，跨系统关联的基础 |
| 实体提取管线（双侧） | P0 | 2天 | 自动填充实体图谱，连接 RAG 和记忆 |
| 多跳查询 | P1 | 2天 | 核心差异化能力，跨 RAG+记忆 遍历 |
| 三阶段检索 | P1 | 1天 | Token 成本优化 |
| 记忆挂载 | P2 | 1天 | 用户体验提升 |

**总计**：约 8 天。

---

## 与阶段三的衔接

| 阶段二产出 | 阶段三用途 |
|-----------|-----------|
| 实体图谱（RAG+记忆） | PATTERN 触发器：检测用户关注实体变化趋势 |
| 条目级记忆索引 | 精确触发：基于单条记忆的衰减/频率触发主动提醒 |
| 多跳查询（跨系统） | 主动洞察："你关注的XX产品利率下调了，关联产品YY也受影响" |
| 三阶段检索 | 按需推送：根据用户画像主动推送相关实体信息 |
| 记忆挂载 | 外部事件驱动：导入的研报触发 EVENT 触发器 |

# Phase 3: 主动智能 (Proactive Intelligence) — 实施计划

## Context

Phase 1（实时预筛 + Dream 精炼）和 Phase 2（实体图谱 + 条目级索引 + 多跳查询 + 三阶段检索 + 记忆挂载）均已完成。Phase 3 补全 MemoryMonitor 的主动智能能力：实现两个缺失的触发器类型（PATTERN 和 EVENT），并将已有的 `analyze_trends()` 接入触发器管线。

当前 MemoryMonitor 有 3 个已实现触发器（frequency/threshold/periodic），`analyze_trends()` LLM 方法存在但未接入任何触发器，PATTERN 和 EVENT 仅在枚举中声明，无实现代码。

---

## 架构概览

```
当前：MemoryMonitor.scan() → FREQUENCY / THRESHOLD / TIME → OutboundMessage

目标：MemoryMonitor.scan() → 全部 5 种触发器 → OutboundMessage
      EventSource → EventQueue → check_event() → EVENT 触发器
      每日定时 → analyze_trends() → PatternSnapshot → PATTERN 触发器（深度）
      每次扫描 → check_pattern() → 规则差异检测 → PATTERN 触发器（快速）
```

---

## 3.1 PATTERN 触发器

### 双层架构

**层 1：规则检测（0 LLM，每次扫描运行）**
- 类别分布偏移：某类别占比变化 > 20 个百分点
- 新实体群组：出现 3+ 个前一快照中不存在的 top-20 实体
- 频率异常：某项目频率增长到类别均值 2 倍以上

**层 2：LLM 趋势分析（~500 token，每日一次）**
- 复用已有 `analyze_trends()`（memory_monitor.py:209-253）
- 输出存入 PatternSnapshot，供层 1 做差异比较

### 数据结构

`fincat/agent/pattern_snapshot.py`（新建）：

```python
@dataclass
class PatternSnapshot:
    timestamp: datetime
    category_counts: dict[str, int]       # {"preference": 12, "knowledge": 45}
    category_frequencies: dict[str, int]  # 每类别频率总和
    top_entities: list[str]               # 按频率排序 top-20
    entity_category_map: dict[str, str]   # 实体 → 类别
    trend_analysis: str | None = None     # LLM 输出

class PatternSnapshotStore:
    """JSONL 存储，保留最近 5 个快照。"""
    def save(snapshot) -> None
    def load_latest() -> PatternSnapshot | None
    def load_history(limit=5) -> list[PatternSnapshot]

def build_snapshot(items: list[MemoryItem]) -> PatternSnapshot
```

### `check_pattern()` 逻辑

```python
def check_pattern(current_items, snapshot) -> list[tuple[bool, str]]:
    # 1. 类别分布偏移：(count/total - old_count/old_total) > 0.20
    # 2. 新实体群组：current_top20 - snapshot_top20 >= 3
    # 3. 频率异常：item.frequency > 2 * category_avg_frequency
```

辅助函数 `_extract_top_entities()` 复用 `entity_extractor.py` 的正则模式。

---

## 3.2 EVENT 触发器

### 事件源架构

```
外部来源 → EventSource 适配器 → EventQueue → check_event() → EVENT 触发器
```

### 三个事件源

1. **MarketEventSource** — 轮询 AKShare（个股涨跌幅 >5%，指数 >2%）
2. **DocumentEventSource** — 知识库新增文档时触发
3. **ManualEventSource** — 编程式推送（监管公告、研报等）

### 数据结构

`fincat/agent/event_sources.py`（新建）：

```python
@dataclass
class ExternalEvent:
    event_id: str
    event_type: str      # "market_move" / "new_document" / "regulation_change" / "custom"
    title: str
    summary: str
    entities: list[str]  # 关联实体 ID
    severity: str        # "info" / "warning" / "critical"
    source: str
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

class EventQueue:
    """基于 asyncio.Queue 的有界队列（max_size=100）。"""
    async def push(event) -> None
    async def pop() -> ExternalEvent | None
    def pending_count() -> int

class EventSource(Protocol):
    async def poll() -> list[ExternalEvent]

class MarketEventSource:   # AKShare 轮询
class DocumentEventSource: # rag_documents 新记录
class ManualEventSource:   # 编程式 push
```

### `check_event()` 匹配逻辑

```python
def check_event(event, items, entity_graph=None) -> tuple[bool, str]:
    # 1. 直接实体匹配：event.entities & item_entities
    # 2. 实体图谱传播：entity_graph.get_entity_neighbors() 多跳
    # 3. 类别级匹配：regulation_change → compliance 类项目
```

---

## 3.3 MemoryMonitor 修改

### `Trigger` 数据类扩展

```python
@dataclass
class Trigger:
    kind: TriggerKind
    item: MemoryItem | None   # PATTERN/EVENT 触发器为 None
    alert: AlertLevel
    message: str
    fired_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
```

### `MemoryMonitor.__init__()` 新增参数

```python
def __init__(self, item_store, *,
             snapshot_store=None,     # PatternSnapshotStore
             event_queue=None,        # EventQueue
             knowledge_store=None,    # RAGKnowledgeStore（用于图谱传播）
             ...):
```

### 新增方法

- `scan()` — 在已有 3 种触发器后追加 PATTERN 触发器逻辑
- `scan_events()` — 异步，从 EventQueue 取事件产生 EVENT 触发器
- `scan_with_trends()` — 异步，每日 LLM 趋势分析 + 快照创建（24h 冷却门控）

---

## 3.4 loop.py 接线

| 位置 | 变更 |
|------|------|
| `__init__()` | 创建 EventQueue、PatternSnapshotStore、各 EventSource，注入 MemoryMonitor |
| 新增 `_ensure_pattern_cron_job()` | 注册每日 03:30 pattern_analysis 定时任务 |
| 修改 `_monitor_scan()` | 调用 `scan_events()` 排空事件队列；`item=None` 时跳过归档 |
| 新增 `_poll_event_sources()` | 后台轮询 MarketEventSource + DocumentEventSource |
| `_process_message()` L911 后 | 新增 `_poll_event_sources()` 调度 |
| 定时任务分发 | 处理 `pattern_analysis` 系统事件 → `monitor.scan_with_trends()` |

---

## 3.5 knowledge/store.py 小幅新增

```python
def get_recent_documents(self, since: datetime) -> list[RAGDocument]:
    """查询 rag_documents 表获取指定时间之后新增的文档。"""
```

---

## 3.6 实施步骤

| 步骤 | 内容 | 文件 | 依赖 |
|------|------|------|------|
| 1 | 模式快照基础设施 | `pattern_snapshot.py`（新建） | 无 |
| 2 | PATTERN 触发器实现 | `memory_monitor.py` | 步骤 1 |
| 3 | EVENT 触发器基础设施 | `event_sources.py`（新建） | 无 |
| 4 | 接入 MemoryMonitor.scan() | `memory_monitor.py` | 步骤 2、3 |
| 5 | 接入 loop.py | `loop.py` | 步骤 4 |
| 6 | 每日趋势分析定时任务 | `loop.py` | 步骤 2、5 |
| 7 | 测试 | 4 个新测试文件 | 全部 |

步骤 1 和 3 可并行。

---

## 3.7 关键文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `fincat/agent/pattern_snapshot.py` | **新建** | PatternSnapshot + Store + build_snapshot |
| `fincat/agent/event_sources.py` | **新建** | ExternalEvent + EventQueue + 3 个 EventSource |
| `fincat/agent/memory_monitor.py` | 修改 | check_pattern/check_event、scan 扩展、Trigger 扩展 |
| `fincat/agent/loop.py` | 修改 | 创建新组件、修改 _monitor_scan、新增定时任务 |
| `fincat/knowledge/store.py` | 修改 | 新增 get_recent_documents() |
| `tests/agent/test_pattern_trigger.py` | **新建** | 7 个测试 |
| `tests/agent/test_event_trigger.py` | **新建** | 6 个测试 |
| `tests/agent/test_event_sources.py` | **新建** | 4 个测试 |
| `tests/agent/test_monitor_integration.py` | **新建** | 4 个测试 |

---

## 3.8 风险缓解

| 风险 | 措施 |
|------|------|
| scan() 同步 vs EVENT 异步队列 | scan() 用 get_nowait()；新增独立 scan_events() 异步方法 |
| PATTERN 误报 | 保守阈值（20pp/3+实体）；24h 冷却期 |
| EVENT 噪声 | 仅 frequency>=1 的实体触发；仅 warning/critical 级别 |
| analyze_trends() LLM 成本 | 每日一次门控；500 token 预算；LLM 失败时规则层仍工作 |
| 破坏已有触发器 | 全部增量变更；FREQUENCY/THRESHOLD/TIME 逻辑不改动 |

