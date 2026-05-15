# Agentic RAG 架构设计方案

> 日期：2026-05-04
> 状态：架构设计
> 主题：单 Agent + Agentic RAG vs Coordinator 多 Agent 的选型分析与演进路线

---

## 一、背景

fincat 是面向金融领域的智能客服 Agent。当前架构为单 Agent ReAct 循环（AgentRunner），通过 ToolRegistry 注册工具供 LLM 自主调用。现有检索能力包括：

- **Memory 预取**：跨轮记忆被动注入用户消息（[loop.py:806](fincat/agent/loop.py)）
- **WebSearch/WebFetch**：网络搜索和网页抓取工具
- **SQLite FTS5 + sqlite-vec**：底层全文搜索和向量搜索能力已有（[memory_sqlite.py](fincat/agent/memory_sqlite.py)），但未封装为 Agent 可调用的检索工具

缺少一个关键能力：**Agent 可以主动调用的专业知识检索工具（RAG）**。

本文讨论两个架构决策：
1. 是否引入 Coordinator（多 Agent 编排）？
2. 如何设计 RAG 检索层？

---

## 二、三种架构模式对比

### 2.1 传统 RAG（Pipeline RAG）

```
用户问 → [固定管道] → 回答
         ├─ Embedding 模型把问题转成向量
         ├─ 向量数据库检索 Top-K 文档
         ├─ 把文档拼到 Prompt 里
         └─ LLM 基于文档生成回答
```

**特征**：LLM 是被动的。检索是一次性的预处理步骤，Agent 无法控制检索策略。

### 2.2 Agentic RAG

```
用户问 → Agent (LLM) 自主决策:
         ├─ "我是否需要检索？"
         ├─ "应该查什么？" → 自己构造 query
         ├─ "结果好不好？" → 不好就换个角度重查
         ├─ "还需要查什么？" → 多跳检索
         └─ 综合所有检索结果 → 生成回答
```

**特征**：LLM 是主动的。检索是 Agent 手中的一件工具，在 ReAct 循环中按需调用。

### 2.3 Coordinator（多 Agent 编排）

```
用户问 → Coordinator Agent（编排者）
         ├─ 分类意图 + 拆解任务
         ├─ 派发给 RAG Agent："你去查产品说明书"
         ├─ 派发给 Compliance Agent："你去查监管规定"
         ├─ 等待各 Agent 返回结果
         └─ 综合 → 生成回答
```

**特征**：多个 Agent 分工协作。每个 Agent 有独立的 ReAct 循环和工具集。

---

## 三、以金融客服场景对比三种模式

以真实问题为例：

> "我想买XX理财产品，它风险大吗？符合我的投资偏好吗？"

### 3.1 传统 RAG 流程

```
1. 固定检索: "XX理财产品 风险"
2. 返回 5 个文档片段（产品说明书、论坛帖、新闻...混在一起）
3. LLM 基于这些片段回答
```

**问题**：
- 检索是盲目的——不知道用户偏好保守型（memory 里有）
- 来源混杂——无法区分权威性（产品说明书 vs 论坛帖子）
- 不会多跳——不会因为查到 R2 就再查 R2 的监管规定

### 3.2 Agentic RAG 流程（推荐）

```
Agent 第1步: "我需要查产品信息"
  → 调用 rag_search("XX理财产品 风险等级 产品说明书")
  → 获得: R2（中风险），投资于债券+货币市场工具

Agent 第2步: "R2产品，我需要对照监管要求"
  → 调用 rag_search("理财产品 R2 风险等级 适当性管理")
  → 获得: R2 适合稳健型及以上投资者

Agent 第3步: "用户是什么偏好？"（从已注入的 Memory 上下文得知）
  → 用户偏好保守型，风险偏好低，高频咨询理财产品收益

Agent 第4步: 综合判断
  "产品是 R2 中风险，你是保守型投资者。R2 的适当性要求匹配
   你的风险等级。但需要注意该产品有 30 天封闭期..."
```

**为什么这是 Agentic RAG 的关键**：

| 环节 | 传统 RAG | Agentic RAG |
|------|----------|-------------|
| 检索时机 | 对话前固定执行 | Agent 推理中按需决定 |
| 检索策略 | 单次、单 query | 多跳、自适应调整 |
| 信息融合 | 所有文档一视同仁 | Agent 权衡来源权威性 |
| Memory 整合 | 独立管道，与 RAG 不互通 | 同一 LLM 上下文内自然融合 |
| 结果评估 | 无 | 不满意可以换 query 重查 |

### 3.3 Coordinator 流程

```
Coordinator: "这需要产品知识 + 偏好匹配"
  ├─ 派发给 RAG Agent: "查XX理财产品的风险等级和监管规定"
  │   └─ RAG Agent 的 ReAct 循环: 查产品说明书 → 查监管规定 → 返回
  └─ 派发给 Preference Agent: "评估该产品是否匹配用户偏好"
      └─ Preference Agent 的 ReAct 循环: 查用户历史 → 计算匹配 → 返回

Coordinator 综合两个 Agent 的返回结果 → 生成最终回答
```

**问题**：
- RAG Agent 不知道用户偏好，Preference Agent 不知道产品细节——信息割裂
- 多了一层调度 LLM 调用，增加了延迟和 token 消耗
- 对于这种常见问答，Coordinator 是过度设计

---

## 四、决策：不用 Coordinator，用 Agentic RAG

### 4.1 为什么当前不用 Coordinator

| 维度 | Agentic RAG | Coordinator |
|------|-------------|-------------|
| LLM 调用次数 | 每次检索 1 次 LLM tool call | 每个子 Agent 多次 LLM 调用 + 1 次 Coordinator |
| 延迟 | 低（工具调用级别） | 高（嵌套 ReAct 循环） |
| 信息融合 | 同一 LLM 上下文内自然融合 | Coordinator 需要显式汇总 |
| 适合场景 | 单轮 QA、多跳检索、信息综合 | 并行子任务、审批流水线、异构工具集 |
| 调试难度 | 低（一条推理链路） | 高（多条独立推理链路） |
| 成本 | 每次检索按 tool call 计费 | N 个 Agent + Coordinator 的叠加 token 消耗 |

**核心理念**：让 Agent 变聪明（给它更好的工具），而不是让架构变复杂（加更多 Agent）。

### 4.2 什么时候需要引入 Coordinator

以下三个信号出现时，才考虑引入 Coordinator：

**信号 1：需要并行执行独立子任务**

> "帮我同时查三只基金的近一年收益率，然后比较它们的风险调整收益"

**信号 2：需要人机协同审批流水线**

> "根据你的分析，帮我生成一份投资建议报告，发给我确认后执行"

**信号 3：子任务有独立的工具和知识边界**

> "分析技术面的同时查基本面数据，还要做合规检查"

### 4.3 演进路线

```
Phase 1（现在）: 单 Agent + Agentic RAG
   └─ 加一个 rag_search 工具到现有 ReAct 循环
  
Phase 2（未来）: 单 Agent + Agentic RAG + Coordinator
   └─ 简单检索 → 直接调 rag_search 工具
   └─ 复杂多步子任务 → 通过 Coordinator 派给专用 Agent
```

---

## 五、Agentic RAG 具体设计

### 5.1 架构图

```
AgentLoop
  ├─ MemoryManager.prefetch_all()      ← 跨轮记忆被动注入（保持）
  │   └─ SkillMemoryProvider
  │
  ├─ ContextBuilder.build_messages()   ← 系统提示 + 记忆摘要 + 金融上下文（保持）
  │
  └─ AgentRunner.run() [ReAct 循环]
      ├─ RAGSearchTool                 ← 🆕 专业知识检索（FTS5 + 向量混合）
      ├─ WebSearchTool                 ← 网络搜索
      ├─ WebFetchTool                  ← 网页抓取
      ├─ ReadFileTool / GrepTool       ← 本地文件操作
      ├─ SpawnTool                     ← 后台子 Agent
      └─ ...其他工具
```

---

## 六、知识库设计（核心部分）

### 6.1 设计原则

**轻量化优先**。不引入外部向量数据库（如 Milvus/Pinecone），不依赖云端 embedding API。全部能力自包含在 fincat 项目内。

**存储**：复用 SQLite（fincat 已有），在 `memory.db` 中新增知识库表。

**检索方法**：三层混合检索（标题结构 + FTS5 全文 + SQL 精确数据），Agent 调用单一 `rag_search` 工具，内部自动路由到对应通道。

---

### 6.2 三层混合检索架构

#### 6.2.1 为什么不用单一方案

| 方案 | 优势 | 劣势 | 适合场景 |
|------|------|------|---------|
| 纯向量检索 | 语义理解强 | 需要 Ollama（1.3GB）、金融标题精确匹配弱 | 通用问答 |
| 纯标题层级 | 章节精确匹配 | 无层级结构的内容无法检索 | 法规文件 |
| 纯 FTS5 | 关键词匹配快 | 无法查精确数值 | 模糊搜索 |
| **三层混合** | **互补覆盖** | 实现稍复杂 | **金融全场景** |

#### 6.2.2 架构图

```
Agent 调用 rag_search(query, category, top_k)
    │
    ▼
┌─────────────────────────────────────────────────┐
│                 路由判断（纯规则）                  │
├──────────────────┬──────────────────────────────┤
│ query 匹配正则？  │ query 含数值关键词？           │
│ 第[一二三\\d]+[章条款] │ 利率/费率/多少/收益率/价格 │
├──────┬───────────┼───────────┬──────────────────┤
│  是  │    否     │    是     │       否         │
│  ▼   │    ▼      │    ▼     │       ▼          │
│通道1 │  通道2    │  通道3    │     通道2        │
│标题  │  FTS5     │  SQL      │     FTS5         │
│匹配  │  全文     │  精确     │     全文         │
└──────┴───────────┴───────────┴──────────────────┘
    │         │          │
    ▼         ▼          ▼
┌─────────────────────────────────────────────────┐
│            多维度评分排序 → Top-K                  │
│  来源去重 → 结果格式化 → 返回给 Agent              │
└─────────────────────────────────────────────────┘
```

#### 6.2.3 路由逻辑

```python
import re

# 通道1触发条件：章节号匹配
_HEADING_RE = re.compile(r"第[一二三四五六七八九十百零\d]+[章条款节]")

# 通道3触发条件：精确数值查询关键词
_EXACT_KEYWORDS = [
    "利率", "费率", "多少", "收益率", "价格", "税率", "限额", "额度",
    "佣金", "手续费", "印花税", "过户费", "起征点", "LPR", "公积金利率",
]

def _route_query(query: str) -> list[int]:
    """判断查询应走哪些通道，返回通道编号列表"""
    channels = []

    # 通道1：章节号精确匹配
    if _HEADING_RE.search(query):
        channels.append(1)

    # 通道3：精确数值查询
    if any(kw in query for kw in _EXACT_KEYWORDS):
        channels.append(3)

    # 通道2：默认兜底（总是执行，除非通道1精确命中且无其他需求）
    if not channels or len(query) > 10:
        channels.append(2)

    return channels
```

#### 6.2.4 三个检索通道

**通道1 — 标题结构匹配**

```python
def _search_by_title(query: str, category: str | None, top_k: int) -> list[dict]:
    """精确标题/路径匹配"""
    sql = """
        SELECT s.*, d.title as doc_title, d.category
        FROM rag_sections s
        JOIN rag_documents d ON s.doc_id = d.doc_id
        WHERE (s.title LIKE ? OR s.path LIKE ?)
    """
    params = [f"%{query}%", f"%{query}%"]
    if category and category != "all":
        sql += " AND d.category = ?"
        params.append(category)
    sql += " ORDER BY s.level ASC LIMIT ?"
    params.append(top_k)
    return _execute(sql, params)
```

**通道2 — FTS5 全文搜索**

```python
def _search_by_fts5(query: str, category: str | None, top_k: int) -> list[dict]:
    """FTS5 BM25 全文搜索"""
    # 分词：中文按字拆分作为 FTS5 query
    fts_query = " OR ".join(query)

    sql = """
        SELECT s.*, d.title as doc_title, d.category,
               rank AS fts_rank
        FROM rag_sections_fts fts
        JOIN rag_sections s ON s.id = fts.rowid
        JOIN rag_documents d ON s.doc_id = d.doc_id
        WHERE rag_sections_fts MATCH ?
    """
    params = [fts_query]
    if category and category != "all":
        sql += " AND d.category = ?"
        params.append(category)
    sql += " ORDER BY rank LIMIT ?"
    params.append(top_k)
    return _execute(sql, params)
```

**通道3 — SQL 精确数据查询**

```python
def _search_facts(query: str, top_k: int) -> list[dict]:
    """精确数值查询（rag_facts 表）"""
    sql = """
        SELECT * FROM rag_facts_fts
        WHERE rag_facts_fts MATCH ?
        LIMIT ?
    """
    return _execute(sql, [query, top_k])
```

#### 6.2.5 多维度评分排序

```python
def _rank_results(results: list[dict], query: str, top_k: int) -> list[dict]:
    """合并三个通道结果，多维度评分排序"""
    query_keywords = _extract_keywords(query)
    seen_ids = set()
    scored = []

    for r in results:
        # 去重
        if r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])

        # 1. 层级权重（高层级标题更权威）
        level_score = {1: 3.0, 2: 2.0, 3: 1.0}.get(r.get("level", 1), 0.5)

        # 2. 标题关键词命中率
        title_keywords = _extract_keywords(r.get("title", ""))
        title_hit = len(query_keywords & title_keywords) / max(len(query_keywords), 1)

        # 3. 内容关键词密度
        content_keywords = _extract_keywords(r.get("content", ""))
        content_hit = len(query_keywords & content_keywords) / max(len(content_keywords), 1)

        # 4. 路径匹配
        path_match = 1.0 if any(kw in r.get("path", "") for kw in query_keywords if len(kw) >= 2) else 0.0

        # 5. 通道来源加分（精确数据通道结果加分）
        channel_bonus = 0.3 if r.get("source_channel") == 3 else 0.0

        total_score = (
            level_score * 0.30 +
            title_hit * 0.25 +
            content_hit * 0.20 +
            path_match * 0.10 +
            channel_bonus * 0.15
        )
        scored.append((total_score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_k]]
```

---

### 6.3 统一入库管道：PDF + HTML → DocIndex

#### 6.3.1 设计思路

PDF 和 HTML 是两种不同格式，但最终都转为统一的 `DocIndex` 模型入库。复用已有的 `PDFStructureParser.parse_text()` 方法。

```
                ┌─ PDF 文件 ──→ PDFStructureParser.parse_pdf()  ─┐
                │                                                  │
financial_data/ │                                                  ├→ DocIndex → SQL 入库
                │                                                  │
                └─ HTML 文件 ─→ HTMLContentParser.parse_html() ───┘
                                      │
                              1. 去噪（去 nav/footer/script）
                              2. 提取正文区域
                              3. 调用 parse_text() → DocIndex
```

#### 6.3.2 HTML 解析器

```python
class HTMLContentParser:
    """将 HTML 文件解析为 DocIndex（复用 PDFStructureParser.parse_text）"""

    CONTENT_SELECTORS = [
        ".detail-news", ".TRS_Editor", "#UCAP-CONTENT",
        "article", ".content", ".article-content",
        ".pages_content", ".detail_content", ".text_content",
    ]

    def parse_html(self, path: Path, category: str, source: str = "") -> DocIndex:
        html = path.read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")

        # 1. 去噪
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # 2. 找正文区域
        content_el = None
        for sel in self.CONTENT_SELECTORS:
            content_el = soup.select_one(sel)
            if content_el:
                break
        if not content_el:
            content_el = soup.find("body") or soup

        # 3. 提取纯文本
        text = content_el.get_text(separator="\n")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        # 4. 过滤低质量内容
        if len(text) < 200:
            return None  # 内容太少，跳过

        # 5. 复用已有的 parse_text 方法
        parser = PDFStructureParser()
        return parser.parse_text(text, doc_id=path.stem, category=category, source=source)
```

#### 6.3.3 数据质量过滤

入库前自动过滤低质量文件：

| 过滤条件 | 动作 | 原因 |
|----------|------|------|
| 文件 < 500 字节 | 跳过 | 空页面或错误页面 |
| HTML 正文 < 200 字 | 跳过 | SPA 框架页面、无实质内容 |
| JS/CSS 代码占比 > 50% | 跳过 | Next.js SSR 营销页面 |
| 正文区域只有链接列表 | 跳过 | 索引页，非详情页 |

---

### 6.4 精确数据层：rag_facts 表

#### 6.4.1 为什么需要单独的精确数据表

FTS5 搜索 "LPR利率多少" 会返回一堆包含"LPR"和"利率"的文档段落，但用户要的是一个精确数字（如 "1Y: 3.10%, 5Y: 3.60%"）。这类精确数值不适合用全文搜索。

#### 6.4.2 表设计

```sql
-- 结构化精确数据表
CREATE TABLE IF NOT EXISTS rag_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_type TEXT NOT NULL,       -- 'rate' / 'product' / 'fee' / 'limit' / 'policy'
    entity TEXT NOT NULL,          -- 'LPR_1Y' / '中银平稳智荟' / '印花税'
    key TEXT NOT NULL,             -- '年化收益率' / '当前利率' / '税率'
    value TEXT NOT NULL,           -- '1.85%' / '3.10%' / '0.05%'
    unit TEXT,                     -- '%' / '天' / '元'
    effective_date TEXT,           -- 生效日期
    expiry_date TEXT,              -- 失效日期（NULL=长期有效）
    source TEXT,                   -- 来源
    source_url TEXT,
    metadata TEXT,                 -- JSON 扩展
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- FTS5 索引（模糊查实体名）
CREATE VIRTUAL TABLE IF NOT EXISTS rag_facts_fts USING fts5(
    entity, key, value,
    content='rag_facts',
    content_rowid='id',
    tokenize='unicode61'
);
```

#### 6.4.3 数据提取策略

| 来源 | 提取方法 | 示例 |
|------|---------|------|
| BOC PDF 文件名 | 正则匹配 `数字%` | `中银平稳智荟...105天1.85%` → entity=中银平稳智荟, key=收益率, value=1.85% |
| BOC PDF 内容 | PyMuPDF + 正则提取费率表 | 产品说明书中的风险等级、投资期限 |
| PBC LPR 页面 | 二次爬取详情页 + 解析 | 1Y=3.10%, 5Y=3.60% |
| 税务/政策 HTML | 正则提取关键数值 | 印花税0.05%、公积金利率3.1% |

```python
def extract_facts_from_filename(filename: str) -> list[dict]:
    """从 BOC PDF 文件名提取产品信息"""
    facts = []

    # 匹配收益率：如 "1.85%" 或 "0.6000%至2.0000%"
    rates = re.findall(r"(\d+\.?\d*)\s*%", filename)
    # 匹配期限：如 "105天" 或 "177天"
    term_match = re.search(r"(\d+)\s*天", filename)
    # 匹配产品名：取文件名中第一个数字序列之前的部分
    name_match = re.match(r"[A-Z]*\d*(.+?)(?:\d{4}年|\d+天)", filename)

    if rates and name_match:
        product_name = name_match.group(1).strip()
        facts.append({
            "fact_type": "product",
            "entity": product_name,
            "key": "年化收益率",
            "value": "~".join(rates) + "%",
            "unit": "%",
        })
        if term_match:
            facts.append({
                "fact_type": "product",
                "entity": product_name,
                "key": "投资期限",
                "value": term_match.group(1),
                "unit": "天",
            })

    return facts
```

---

### 6.5 数据库表设计（总览）

在现有 `memory_sqlite.py` 的 SQLite 数据库中新增以下表：

```sql
-- ========== 文档层 ==========

-- 文档索引表
CREATE TABLE IF NOT EXISTS rag_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT UNIQUE NOT NULL,       -- 文档唯一标识（文件名哈希）
    title TEXT NOT NULL,               -- 文档总标题
    category TEXT NOT NULL,            -- product / regulation / term / tax / livelihood / case
    source TEXT,                       -- 来源名称（证监会/人民银行/中行...）
    source_url TEXT,                   -- 来源链接
    file_path TEXT,                    -- 原始文件路径
    doc_type TEXT,                     -- 'pdf' / 'html'
    sections_count INTEGER DEFAULT 0,  -- 切片数量
    toc TEXT,                          -- 目录 JSON
    metadata TEXT,                     -- JSON 扩展字段
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 文档切片表（按标题层级切分）
CREATE TABLE IF NOT EXISTS rag_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,              -- 关联 rag_documents
    title TEXT NOT NULL,               -- 切片标题
    level INTEGER DEFAULT 1,           -- 标题层级 1-3
    path TEXT NOT NULL,                -- 层级路径，如 "第5章 > 5.2 风险评估"
    content TEXT NOT NULL,             -- 该切片下的完整内容
    page_start INTEGER,
    page_end INTEGER,
    keywords TEXT,                     -- 逗号分隔关键词
    metadata TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (doc_id) REFERENCES rag_documents(doc_id) ON DELETE CASCADE
);

-- ========== 精确数据层 ==========

-- 结构化精确数据表
CREATE TABLE IF NOT EXISTS rag_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_type TEXT NOT NULL,           -- rate / product / fee / limit / policy
    entity TEXT NOT NULL,              -- 实体名（产品名/税种/政策名）
    key TEXT NOT NULL,                 -- 属性名（收益率/利率/税率）
    value TEXT NOT NULL,               -- 属性值（1.85% / 3.10%）
    unit TEXT,                         -- 单位
    effective_date TEXT,               -- 生效日期
    expiry_date TEXT,                  -- 失效日期
    source TEXT,
    source_url TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ========== FTS5 索引 ==========

-- 切片全文索引
CREATE VIRTUAL TABLE IF NOT EXISTS rag_sections_fts USING fts5(
    title, content, keywords,
    content='rag_sections',
    content_rowid='id',
    tokenize='unicode61'
);

-- 事实全文索引
CREATE VIRTUAL TABLE IF NOT EXISTS rag_facts_fts USING fts5(
    entity, key, value,
    content='rag_facts',
    content_rowid='id',
    tokenize='unicode61'
);
```

#### 表关系与字段说明

| 表 | 用途 | 切分方式 | 关键字段 |
|---|---|---|---|
| `rag_documents` | 文档元数据 | 1 文件 = 1 行 | doc_id, title, category, doc_type |
| `rag_sections` | 文档切片 | 1 标题 = 1 行 | doc_id(FK), title, level, path, content, keywords |
| `rag_facts` | 精确数值 | 1 事实 = 1 行 | fact_type, entity, key, value |
| `rag_sections_fts` | 切片全文索引 | FTS5 虚拟表 | title, content, keywords |
| `rag_facts_fts` | 事实全文索引 | FTS5 虚拟表 | entity, key, value |

---

### 6.6 检索工具接口

#### 6.6.1 Agent 工具定义

```python
class RAGSearchTool(Tool):
    """RAG 检索工具 — Agent 主动调用"""
    name = "rag_search"
    description = (
        "Search the financial knowledge base for professional information. "
        "Use this when you need: "
        "- Product details (funds, wealth management products, rates) "
        "- Regulatory compliance (CSRC, CBIRC regulations) "
        "- Financial terminology and industry knowledge "
        "- Precise data (interest rates, fees, tax rates) "
        "This is NOT for recent news — use web_search for that."
    )
    parameters = {
        "query": {"type": "string", "description": "自然语言检索查询"},
        "top_k": {"type": "integer", "description": "返回数量（默认5）", "default": 5},
        "category": {
            "type": "string",
            "description": "可选过滤: product / regulation / tax / livelihood / all",
            "enum": ["product", "regulation", "tax", "livelihood", "term", "case", "all"],
            "default": "all"
        }
    }
```

#### 6.6.2 检索返回格式

```python
@dataclass
class RAGResult:
    id: int
    title: str
    content: str
    category: str
    source: str | None
    score: float
    source_channel: int  # 1=标题匹配, 2=FTS5, 3=SQL精确
    metadata: dict
```

Agent 收到的 tool result 格式：

```
检索结果 (top_k=3):
========================================
[1] 标题: 商业性个人住房贷款利率
    分类: livelihood | 来源: 人民银行
    得分: 0.92 | 通道: 标题匹配+FTS5
    路径: 信贷政策 > 住房贷款
    内容: 首套住房贷款利率下限为LPR-20BP...
========================================
[2] 标题: LPR利率
    分类: rate | 来源: 人民银行
    得分: 0.88 | 通道: SQL精确查询
    内容: 1Y: 3.10%, 5Y: 3.60%（2026年4月）
========================================
...
```

---

### 6.7 知识来源与入库规划

| 来源 | 文件数 | 类型 | category | 入库方式 |
|------|--------|------|----------|---------|
| BOC 产品 PDF | 50 | PDF | product | `parse_pdf()` + 文件名提取 facts |
| 证券业务规则 | 37 | HTML | regulation | `parse_html()` → `parse_text()` |
| 民生政策 | 30 | HTML | livelihood | `parse_html()` → `parse_text()` |
| DISC-FIN-SFT | ~1000 | JSON | product/regulation/term/case | 直接写入 rag_sections |
| **总计** | **~1100+** | — | — | — |

---

### 6.8 后续可选：叠加向量检索（Phase 2）

当前方案不依赖 embedding，后续可按需叠加 Ollama bge-m3 向量检索：

1. 用户安装 Ollama + `ollama pull bge-m3:latest`
2. 新增 `rag_knowledge_vec` 向量索引表
3. 批量预计算已有知识的 embedding
4. 在检索流程中增加向量通道，与现有三通道 RRF 融合

**向量检索增强的场景**：同义词匹配（"房贷"="住房贷款"="按揭"）、自然语言模糊查询。

这一步完全可选，当前三层混合检索已覆盖主要场景。

---

## 七、知识库管理（CRUD）

### 7.1 工具接口

```python
class RAGSearchTool(Tool):
    """RAG 检索工具 — Agent 主动调用"""
    name = "rag_search"
    description = (
        "Search the financial knowledge base for professional information. "
        "Use this when you need authoritative knowledge about: "
        "- Product details (funds, wealth management products) "
        "- Regulatory compliance (CSRC, CBIRC regulations) "
        "- Financial terminology and industry knowledge. "
        "- Common Q&A cases in financial services. "
        "This is NOT for recent news or real-time data — use web_search for that."
    )
    parameters = {
        "query": {"type": "string", "description": "自然语言检索查询"},
        "top_k": {"type": "integer", "description": "返回文档数量（默认5）", "default": 5},
        "category": {
            "type": "string",
            "description": "可选过滤: product / regulation / term / case / all（默认all）",
            "enum": ["product", "regulation", "term", "case", "all"],
            "default": "all"
        }
    }


class RAGManageTool(Tool):
    """RAG 管理工具 — 管理员手动操作（不暴露给普通用户）"""
    name = "rag_manage"
    description = "Manage knowledge base: add, update, delete, import documents."
    parameters = {
        "action": {"type": "string", "enum": ["add", "delete", "import", "stats"]},
        "title": {"type": "string"},
        "content": {"type": "string"},
        "category": {"type": "string"},
        "source_url": {"type": "string"},
        "doc_id": {"type": "integer"},  # 用于删除
    }
```

### 7.2 检索返回格式

```python
@dataclass
class RAGChunk:
    id: int
    title: str
    content: str
    category: str
    source: str | None
    source_url: str | None
    score: float           # RRF 融合后的综合得分
    rank_fts: int | None   # FTS5 排名（None 表示未命中）
    rank_vec: int | None   # 向量搜索排名（None 表示未命中）
```

Agent 收到的 tool result 格式：

```
检索结果 (top_k=3):
========================================
[1] 标题: 什么是R2风险等级？
分类: term | 来源: DISC-FIN-SFT
得分: 0.92 | FTS排名: 1 | 向量排名: 2
内容:
问题：什么是R2风险等级？
答案：R2（中风险）适合稳健型及以上投资者...
========================================
...
```

---

## 八、与现有 Memory 系统的关系

```
知识来源              │ 存储方式              │ 检索方式      │ Agent 感知
─────────────────────┼──────────────────────┼──────────────┼──────────────────
用户偏好/历史         │ 分类 Markdown          │ Memory 预取   │ 被动注入到用户消息
                      │ items.jsonl           │              │
─────────────────────┼──────────────────────┼──────────────┼──────────────────
产品/法规/术语知识    │ rag_knowledge 表        │ RAG 检索      │ 主动调 rag_search 工具
                      │ (FTS5 + sqlite-vec)   │ 工具          │
─────────────────────┼──────────────────────┼──────────────┼──────────────────
实时行情/新闻         │ WebSearchTool         │ 实时网络搜索  │ 主动调 web_search 工具
                      │                       │              │
─────────────────────┼──────────────────────┼──────────────┼──────────────────
投资组合/资产         │ SQLite assets 表       │ SQL 查询      │ 金融上下文注入
                      │                       │              │
─────────────────────┼──────────────────────┼──────────────┼──────────────────
反思经验             │ reflection_vault        │ FTS5 + vec    │ 跨轮预取（已有）
                      │                       │              │
─────────────────────┼──────────────────────┼──────────────┼──────────────────
金融专业知识          │ rag_knowledge 表        │ RAG 检索      │ 主动调 rag_search 工具
                      │ (DISC-FIN-SFT 等)     │ 工具          │
```

**三层信息边界**：
- **Memory（记忆）** = 用户个人信息（偏好/历史/案例）→ 被动注入
- **Knowledge（知识）** = 共享专业知识（产品/法规/术语/DISC-FIN-SFT）→ 主动检索
- **Data（数据）** = 结构化数值（投资组合/行情）→ 金融上下文注入

---

## 九、实施步骤

### Phase 1：数据库建表 + 入库管道

1. 在 `memory_sqlite.py` 新增 `init_rag_tables()` 方法，创建 rag_documents / rag_sections / rag_facts 及对应 FTS5 虚拟表
2. 实现 `fincat/knowledge/parsers/html_parser.py` — HTMLContentParser（HTML 去噪 → parse_text → DocIndex）
3. 实现 `fincat/knowledge/ingest.py` — 统一入库脚本
   - 遍历 `financial_data/` 所有 PDF + HTML
   - PDF → `parse_pdf()` → DocIndex
   - HTML → `parse_html()` → DocIndex
   - DocIndex → `to_sql()` → 写入 rag_documents + rag_sections
   - 从文件名/内容提取结构化数值 → 写入 rag_facts
4. 过滤低质量文件（SPA 页面、索引页、空页面）

### Phase 2：检索工具 + Agent 集成

1. 实现 `fincat/knowledge/retriever.py` — RAGRetriever
   - 三层路由逻辑（标题匹配 / FTS5 / SQL 精确）
   - 多维度评分排序
2. 封装 `fincat/agent/tools/rag.py` — RAGSearchTool（继承 Tool 基类）
3. 在 `loop.py` 注册 rag_search 工具
4. 测试典型查询场景

### Phase 3：数据补充 + 质量优化

1. 二次爬取 PBC LPR 详情页（提取实际利率数值 → rag_facts）
2. 从 BOC PDF 批量提取产品结构化数据（收益率/期限/风险等级 → rag_facts）
3. 导入 DISC-FIN-SFT 数据集（~1000 条金融问答 → rag_sections）
4. 补充缺失的民生政策（医保/养老/个税等）

### Phase 4（可选）：叠加向量检索

1. 用户安装 Ollama + `ollama pull bge-m3:latest`
2. 新增 `rag_knowledge_vec` 向量索引表
3. 批量预计算已有知识的 embedding
4. 在检索流程中增加向量通道，与现有三通道 RRF 融合

---

## 十、关键文件引用

| 文件 | 内容 |
|------|------|
| [runner.py](fincat/agent/runner.py) | AgentRunner — 单 Agent ReAct 循环 |
| [loop.py](fincat/agent/loop.py) | AgentLoop — 消息处理 + Memory 预取 + 工具注册 |
| [subagent.py](fincat/agent/subagent.py) | SubagentManager — 后台即发即弃子 Agent |
| [memory_sqlite.py](fincat/agent/memory_sqlite.py) | SQLiteMemoryStore — FTS5 + sqlite-vec pattern + **新增 rag_* 表** |
| [memory.py](fincat/agent/memory.py) | MemoryStore — 分类 Markdown 管理 |
| [context.py](fincat/agent/context.py) | ContextBuilder — 系统提示构建 |
| [tools/registry.py](fincat/agent/tools/registry.py) | ToolRegistry — 工具注册中心 |
| [tools/web.py](fincat/agent/tools/web.py) | WebSearchTool / WebFetchTool |
| [tools/rag.py](fincat/agent/tools/rag.py) | **RAGSearchTool — 三层混合检索工具** |
| [base.py](fincat/agent/tools/base.py) | Tool 基类 — 工具参数装饰器 |
| [parsers/pdf_parser.py](fincat/knowledge/parsers/pdf_parser.py) | DocSection/DocIndex + parse_pdf + parse_text + to_sql |
| [parsers/html_parser.py](fincat/knowledge/parsers/html_parser.py) | **HTMLContentParser — HTML 去噪 → DocIndex** |
| [ingest.py](fincat/knowledge/ingest.py) | **统一入库管道 — PDF/HTML → rag_* 表** |
| [retriever.py](fincat/knowledge/retriever.py) | **RAGRetriever — 三层路由检索逻辑** |
| DISC-FIN-SFT 数据集 | https://huggingface.co/datasets/1234KAI123123123123/DISC-FIN-SFT |
