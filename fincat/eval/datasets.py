"""Langfuse 评测数据集管理。

10 个评测维度，每个维度对应一个 Langfuse dataset。
数据集以 JSON 文件形式维护在 fincat/eval/data/ 目录下，
通过 sync_to_langfuse() 上传到 Langfuse。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class EvalCase:
    """单条评测用例。"""

    id: str
    input: dict[str, Any]
    expected_output: str | None = None
    expected_tools: list[str] | None = None
    expected_skill: str | None = None
    expected_risk_level: str | None = None
    expected_pii_types: list[str] | None = None
    expected_compliance_action: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# 10 个评测维度定义
DATASET_DEFINITIONS: dict[str, dict[str, str]] = {
    "financial_data_accuracy": {
        "description": "金融数据准确性 — Agent 是否正确使用股票工具并呈现准确数据",
        "langfuse_name": "eval-financial-data-accuracy",
    },
    "compliance_safety": {
        "description": "合规安全性 — 防御管道是否正确拦截/放行/警告",
        "langfuse_name": "eval-compliance-safety",
    },
    "pii_protection": {
        "description": "PII 保护 — PII 扫描器是否正确检测和脱敏",
        "langfuse_name": "eval-pii-protection",
    },
    "risk_assessment": {
        "description": "风险评估 — 风险评分器是否正确分类 P0-P3",
        "langfuse_name": "eval-risk-assessment",
    },
    "tool_selection": {
        "description": "工具选择准确性 — Agent 是否为查询选择了正确的工具",
        "langfuse_name": "eval-tool-selection",
    },
    "multi_step_reasoning": {
        "description": "多步推理 — Agent 是否能处理需要多个工具的复杂金融查询",
        "langfuse_name": "eval-multi-step-reasoning",
    },
    "memory_recall": {
        "description": "记忆召回 — Agent 是否正确召回相关记忆",
        "langfuse_name": "eval-memory-recall",
    },
    "skill_routing": {
        "description": "技能路由 — SkillRouter 是否选择了正确的技能",
        "langfuse_name": "eval-skill-routing",
    },
    "knowledge_base_quality": {
        "description": "知识库质量 — RAG 是否返回相关金融文档",
        "langfuse_name": "eval-knowledge-base-quality",
    },
    "response_quality": {
        "description": "响应质量 — 最终响应是否有效、准确、合规",
        "langfuse_name": "eval-response-quality",
    },
}


class DatasetManager:
    """管理 Langfuse 评测数据集的创建和同步。"""

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = data_dir or Path(__file__).parent / "data"
        self._langfuse: Any = None

    def _ensure_client(self) -> Any:
        if self._langfuse is None:
            try:
                from fincat.eval.config import LANGFUSE_ENABLED
                if LANGFUSE_ENABLED:
                    from fincat.eval.langfuse_hook import _get_langfuse_client
                    self._langfuse = _get_langfuse_client()
            except Exception:
                pass
        return self._langfuse

    def load_cases(self, dimension: str) -> list[EvalCase]:
        """从本地 JSON 文件加载评测用例。"""
        path = self._data_dir / f"{dimension}.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        return [EvalCase(**case) for case in data]

    def sync_to_langfuse(self, dimension: str) -> None:
        """将本地数据集同步到 Langfuse。"""
        client = self._ensure_client()
        if not client:
            logger.warning("Langfuse 未初始化，跳过数据集同步")
            return
        defn = DATASET_DEFINITIONS.get(dimension)
        if not defn:
            raise ValueError(f"未知评测维度: {dimension}")
        cases = self.load_cases(dimension)
        if not cases:
            logger.warning("数据集 {} 为空，跳过", dimension)
            return
        client.create_dataset(name=defn["langfuse_name"])
        for case in cases:
            client.create_dataset_item(
                dataset_name=defn["langfuse_name"],
                input=case.input,
                expected_output=case.expected_output,
                metadata={"case_id": case.id, **case.metadata},
            )
        logger.info("已同步 {} 条用例到 Langfuse 数据集 {}", len(cases), defn["langfuse_name"])

    def sync_all(self) -> None:
        """同步所有数据集到 Langfuse。"""
        for dimension in DATASET_DEFINITIONS:
            self.sync_to_langfuse(dimension)

    def list_dimensions(self) -> list[str]:
        """列出所有评测维度。"""
        return list(DATASET_DEFINITIONS.keys())

    def summary(self) -> dict[str, int]:
        """返回每个维度的用例数量。"""
        return {dim: len(self.load_cases(dim)) for dim in DATASET_DEFINITIONS}
