"""RAG knowledge base search tool for the financial agent.

Provides a single `rag_search` tool with RRF-based hybrid retrieval:
1. Vector — FAISS semantic similarity on chunk embeddings
2. FTS5 — BM25 full-text search on chunks
3. Title — structural title/path matching
4. Facts — precise lookup on structured rate/fee data

Usage:
    Registered in loop.py as a standard Tool.
"""

from __future__ import annotations

from typing import Any

from fincat.agent.tools.base import Tool, tool_parameters


@tool_parameters({
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "搜索查询。可以是：\n"
                "- 关键词搜索（如 '公积金贷款利率'）\n"
                "- 章节查询（如 '第五章 风险管理'）\n"
                "- 精确数据查询（如 'LPR利率是多少'）\n"
                "- 产品查询（如 '中银平稳智荟收益率'）\n"
                "- 实体查询（如 '中国银行'、'贵州茅台'，需配合 mode=entity）"
            ),
            "minLength": 2,
        },
        "mode": {
            "type": "string",
            "enum": ["search", "entity"],
            "description": "查询模式。search=关键词搜索（默认），entity=实体图谱遍历（从实体出发沿关系链查询关联信息）",
        },
        "category": {
            "type": "string",
            "enum": ["product", "regulation", "business_rules", "livelihood", "facts"],
            "description": "限定搜索类别（可选）。product=产品说明, regulation=监管规定, business_rules=证券业务, livelihood=民生政策",
        },
        "top_k": {
            "type": "integer",
            "description": "返回结果数量（默认5）",
            "minimum": 1,
            "maximum": 20,
        },
    },
    "required": ["query"],
})
class RAGSearchTool(Tool):
    """Search the RAG knowledge base for financial documents and facts.

    RRF-based hybrid retrieval across four channels:
    - Vector: FAISS semantic similarity on chunk embeddings
    - FTS5: BM25 full-text search on chunks
    - Title: structural title/path matching
    - Facts: precise lookup on structured rate/fee data

    Results are fused via Reciprocal Rank Fusion (RRF) for optimal ranking.
    """

    def __init__(self, hybrid_retriever=None, store=None):
        self._retriever = hybrid_retriever
        self._store = store

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        return (
            "搜索金融知识库（RAG）。包含银行产品说明书、证券业务规则、"
            "民生政策文件等。支持关键词搜索、章节定位和精确数据查询（利率/费率/税率）。"
            "当你需要回答用户关于金融产品、政策法规、费率标准等问题时使用此工具。"
        )

    @property
    def read_only(self) -> bool:
        return True

    def _get_store(self):
        if self._store is None:
            try:
                from fincat.knowledge.store import get_knowledge_store
            except ImportError:
                return None
            self._store = get_knowledge_store()
        return self._store

    def _get_retriever(self):
        if self._retriever is not None:
            return self._retriever
        # Fallback: try to build a retriever from store
        store = self._get_store()
        if store is None:
            return None
        try:
            from fincat.agent.embedding import EmbeddingEngine
            from fincat.knowledge.vector_store import KnowledgeVectorStore
            from fincat.knowledge.hybrid_retriever import HybridRetriever
            from pathlib import Path

            embedding = EmbeddingEngine()
            vector_dir = Path(store.db_path).parent / "vectors"
            vector_store = KnowledgeVectorStore(
                db_path=Path(store.db_path),
                vector_dir=vector_dir,
                embedding=embedding,
            )
            self._retriever = HybridRetriever(store, vector_store, embedding)
            return self._retriever
        except Exception:
            return None

    async def execute(
        self,
        query: str,
        mode: str = "search",
        category: str | None = None,
        top_k: int = 5,
        **kwargs: Any,
    ) -> str:
        store = self._get_store()
        if store is None:
            return "知识库模块未安装（fincat.knowledge）。请先安装知识库模块后重试。"

        # Check if knowledge base is empty
        stats = store.get_stats()
        if stats.get("sections", 0) == 0 and stats.get("facts", 0) == 0:
            return (
                "知识库为空。请先运行数据入库：\n"
                "python -m fincat.knowledge.ingest"
            )

        # Entity traversal mode
        if mode == "entity":
            return await self._execute_entity_traverse(store, query, top_k)

        # Try hybrid retriever first (vector + FTS + title + facts with RRF)
        retriever = self._get_retriever()
        if retriever is not None:
            try:
                results = retriever.search(query, category=category, top_k=top_k)
                if results:
                    return self._format_hybrid_results(results, stats)
            except Exception as e:
                pass  # fall through to legacy search

        # Fallback: legacy three-channel search
        results = store.search(query, category=category, top_k=top_k)

        if not results:
            return f"未找到与 '{query}' 相关的知识库内容。"

        # Format results
        output_parts: list[str] = []
        for i, r in enumerate(results, 1):
            channel_name = {1: "结构匹配", 2: "全文搜索", 3: "精确数据"}[r.source_channel]
            header = f"[{i}] [{channel_name}] {r.title}"
            if r.category:
                header += f" ({r.category})"
            output_parts.append(header)

            # Truncate content for display
            content = r.content
            if len(content) > 500:
                content = content[:500] + "..."
            output_parts.append(content)

            if r.source:
                output_parts.append(f"来源: {r.source}")
            output_parts.append("")

        # Add stats footer
        output_parts.append(
            f"--- 知识库: {stats.get('documents', 0)}文档, "
            f"{stats.get('sections', 0)}切片, {stats.get('facts', 0)}事实 ---"
        )

        return "\n".join(output_parts)

    def _format_hybrid_results(self, results, stats: dict) -> str:
        """Format hybrid retriever results for agent consumption."""
        output_parts: list[str] = []
        for i, r in enumerate(results, 1):
            channels = "+".join(r.channels_matched) if r.channels_matched else "hybrid"
            header = f"[{i}] [{channels}] {r.title}"
            if r.category:
                header += f" ({r.category})"
            output_parts.append(header)

            content = r.content
            if len(content) > 500:
                content = content[:500] + "..."
            output_parts.append(content)

            if r.source:
                output_parts.append(f"来源: {r.source}")
            output_parts.append("")

        output_parts.append(
            f"--- 知识库: {stats.get('documents', 0)}文档, "
            f"{stats.get('sections', 0)}切片, {stats.get('facts', 0)}事实 ---"
        )

        return "\n".join(output_parts)

    async def _execute_entity_traverse(self, store, entity_name: str, top_k: int) -> str:
        """Execute entity traversal: resolve → walk relations → retrieve."""
        entity_id = store.resolve_entity(entity_name)
        if not entity_id:
            # Try standard search as fallback
            results = store.search(entity_name, top_k=top_k)
            if not results:
                return f"未找到实体 '{entity_name}'，也未找到相关内容。"
            # Fall through to standard format
            output_parts = [f"未找到精确实体 '{entity_name}'，以下是关键词搜索结果：\n"]
            for i, r in enumerate(results, 1):
                channel_name = {1: "结构匹配", 2: "全文搜索", 3: "精确数据"}[r.source_channel]
                output_parts.append(f"[{i}] [{channel_name}] {r.title}")
                content = r.content[:500] + "..." if len(r.content) > 500 else r.content
                output_parts.append(content)
                if r.source:
                    output_parts.append(f"来源: {r.source}")
                output_parts.append("")
            return "\n".join(output_parts)

        entity = store.get_entity(entity_id)
        neighbors = store.get_entity_neighbors(entity_id)
        results = store.search_by_entity(entity_name, top_k=top_k)

        output_parts: list[str] = []

        # Entity info
        if entity:
            aliases_str = ", ".join(entity.get("aliases", []))
            output_parts.append(
                f"实体: {entity['name']} (ID: {entity_id}, 类型: {entity['entity_type']})"
            )
            if aliases_str:
                output_parts.append(f"别名: {aliases_str}")

        # Related entities
        if neighbors:
            output_parts.append(f"\n关联实体 ({len(neighbors)}):")
            for n in neighbors[:10]:
                output_parts.append(
                    f"  - {n['name']} ({n['entity_type']}) "
                    f"[{n.get('relation_type', '?')}] "
                    f"({n.get('direction', '?')})"
                )

        # Retrieved facts and sections
        if results:
            output_parts.append(f"\n相关内容 ({len(results)}):")
            for i, r in enumerate(results, 1):
                channel_name = {1: "结构匹配", 2: "全文搜索", 3: "精确数据"}[r.source_channel]
                content = r.content[:500] + "..." if len(r.content) > 500 else r.content
                output_parts.append(f"[{i}] [{channel_name}] {r.title}")
                output_parts.append(content)
                if r.source:
                    output_parts.append(f"来源: {r.source}")
                output_parts.append("")
        else:
            output_parts.append("\n未找到与该实体直接关联的知识库内容。")

        return "\n".join(output_parts)
