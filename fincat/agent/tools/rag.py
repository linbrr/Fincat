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
        "source": {
            "type": "string",
            "enum": ["all", "private", "public"],
            "description": "搜索范围。all=全部（默认），private=仅用户上传的文档，public=仅公共知识库",
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

    def __init__(self, hybrid_retriever=None, store=None, private_retriever=None):
        self._retriever = hybrid_retriever
        self._store = store
        self._private_retriever = private_retriever

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        return (
            "搜索知识库（RAG）。包含两类：1）公共知识库（银行产品、证券规则、政策文件等）；"
            "2）用户上传的私有文档（PDF年报、研报等）。"
            "支持关键词搜索和精确数据查询。"
            "当用户提问涉及金融知识或询问已上传文档内容时，使用此工具而非 glob/read_file。"
            "用 source='private' 可只搜用户文档。"
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
            from fincat.config.paths import get_vector_dir
            vector_dir = get_vector_dir()
            vector_store = KnowledgeVectorStore(
                db_path=Path(store.db_path),
                vector_dir=vector_dir,
                embedding=embedding,
            )
            self._retriever = HybridRetriever(store, vector_store, embedding)
            return self._retriever
        except Exception:
            return None

    def _search_private(self, query: str, top_k: int = 3) -> list:
        """Search the private knowledge base if a private retriever is available."""
        if self._private_retriever is None:
            # Lazy init: try to build retriever if private DB exists now
            self._try_init_private_retriever()
        if self._private_retriever is None:
            return []
        try:
            return self._private_retriever.search(query, top_k=top_k)
        except Exception:
            return []

    def _try_init_private_retriever(self) -> None:
        """Lazily initialize the private retriever when the DB appears."""
        try:
            from fincat.config.paths import get_knowledge_dir
            from fincat.knowledge.hybrid_retriever import HybridRetriever
            from fincat.knowledge.store import RAGKnowledgeStore
            from fincat.knowledge.vector_store import KnowledgeVectorStore

            kb_dir = get_knowledge_dir()
            private_db = kb_dir / "knowledge.db"
            if not private_db.exists():
                return

            # Need embedding for vector store — try to get from public retriever
            embedding = getattr(self._retriever, "_embedding", None) if self._retriever else None
            if embedding is None:
                return

            private_store = RAGKnowledgeStore(private_db)
            private_vs = KnowledgeVectorStore(
                db_path=private_db,
                vector_dir=kb_dir / "vectors",
                embedding=embedding,
            )
            self._private_retriever = HybridRetriever(private_store, private_vs, embedding)
        except Exception:
            pass  # will retry next call

    @staticmethod
    def _merge_hybrid_results(public: list, private: list, top_k: int) -> list:
        """Merge public and private hybrid results by RRF score."""
        # Mark private results
        for r in private:
            if not r.source:
                r.source = "private_knowledge"
            elif "private" not in r.source:
                r.source = f"{r.source},private_knowledge"

        combined = public + private
        combined.sort(key=lambda r: r.rrf_score, reverse=True)
        return combined[:top_k]

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

        # Check if knowledge base is empty (public + private)
        stats = store.get_stats()
        has_public = stats.get("sections", 0) > 0 or stats.get("facts", 0) > 0
        has_private = self._private_retriever is not None
        if not has_public and not has_private:
            return (
                "知识库为空。请先运行数据入库：\n"
                "python -m fincat.knowledge.ingest\n"
                "或使用 /kb add 导入私有文档"
            )

        # Entity traversal mode
        if mode == "entity":
            return await self._execute_entity_traverse(store, query, top_k)

        # Try hybrid retriever first (vector + FTS + title + facts with RRF)
        retriever = self._get_retriever()
        if retriever is not None:
            try:
                results = retriever.search(query, category=category, top_k=top_k)
                # Also search private knowledge base
                private_results = self._search_private(query, top_k=3)
                if private_results:
                    results = self._merge_hybrid_results(results, private_results, top_k)
                if results:
                    return self._format_hybrid_results(results, stats)
            except Exception:
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
