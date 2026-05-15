"""批量评测运行器 — 在 Langfuse 数据集上运行评测。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from fincat.providers.base import LLMProvider


class EvalRunner:
    """批量评测运行器。

    使用方式::

        runner = EvalRunner(provider=provider)
        results = await runner.run_dimension("financial_data_accuracy")
        results = await runner.run_all()
    """

    def __init__(
        self,
        provider: LLMProvider,
        judge_model: str | None = None,
    ) -> None:
        self._provider = provider
        from fincat.eval.config import FINCAT_JUDGE_MODEL
        self._judge_model = judge_model or FINCAT_JUDGE_MODEL

    async def run_dimension(self, dimension: str) -> dict[str, Any]:
        """运行单个维度的评测。"""
        from fincat.eval.datasets import DatasetManager

        dm = DatasetManager()
        cases = dm.load_cases(dimension)
        if not cases:
            return {"dimension": dimension, "cases": 0, "average_score": 0.0, "scores": []}

        scores: list[dict[str, Any]] = []
        for case in cases:
            try:
                score = await self._evaluate_case(dimension, case)
                scores.append({"id": case.id, **score})
            except Exception as e:
                logger.error("评测用例 {} 失败: {}", case.id, e)
                scores.append({"id": case.id, "score": 0.0, "error": str(e)})

        avg_score = sum(s.get("score", 0.0) for s in scores) / len(scores) if scores else 0.0
        return {
            "dimension": dimension,
            "cases": len(cases),
            "average_score": round(avg_score, 4),
            "scores": scores,
        }

    async def run_all(self) -> dict[str, Any]:
        """运行所有维度的评测。"""
        from fincat.eval.datasets import DATASET_DEFINITIONS

        results: dict[str, Any] = {}
        for dimension in DATASET_DEFINITIONS:
            logger.info("开始评测维度: {}", dimension)
            results[dimension] = await self.run_dimension(dimension)
            logger.info(
                "维度 {} 完成: {}/{}",
                dimension,
                results[dimension]["average_score"],
                results[dimension]["cases"],
            )
        return results

    async def _evaluate_case(self, dimension: str, case: Any) -> dict[str, Any]:
        """评测单条用例。"""
        from fincat.eval.scoring import llm_judge, rule_based

        input_data = case.input
        message = input_data.get("message", "")

        # 根据维度选择评分器
        if dimension == "financial_data_accuracy":
            result = await llm_judge.judge_financial_accuracy(
                message, "", [], self._provider, self._judge_model,
            )
        elif dimension == "compliance_safety":
            result = await llm_judge.judge_compliance_safety(
                message, "", "bank", self._provider, self._judge_model,
            )
        elif dimension == "pii_protection":
            result = rule_based.score_pii_detection(
                message, f"eval:{case.id}", case.expected_pii_types or [],
            )
        elif dimension == "risk_assessment":
            result = rule_based.score_risk_level(
                message, case.expected_risk_level or "P3_LOW",
            )
        elif dimension == "tool_selection":
            result = await llm_judge.judge_tool_selection(
                message, [], case.expected_tools or [], self._provider, self._judge_model,
            )
        elif dimension == "skill_routing":
            result = await llm_judge.judge_skill_routing(
                message, None, case.expected_skill, self._provider, self._judge_model,
            )
        elif dimension == "response_quality":
            result = await llm_judge.judge_response_quality(
                message, "", self._provider, self._judge_model,
            )
        elif dimension == "memory_recall":
            result = await llm_judge.judge_memory_recall(
                message, "", self._provider, self._judge_model,
            )
        else:
            result = await llm_judge.judge_response_quality(
                message, "", self._provider, self._judge_model,
            )

        # 上报到 Langfuse dataset run
        self._report_to_langfuse(dimension, case, result)
        return result

    def _report_to_langfuse(self, dimension: str, case: Any, result: dict[str, Any]) -> None:
        """将评测结果上报到 Langfuse dataset run。"""
        try:
            from fincat.eval.langfuse_hook import _get_langfuse_client
            from fincat.eval.datasets import DATASET_DEFINITIONS

            langfuse = _get_langfuse_client()
            if not langfuse:
                return
            dataset_name = DATASET_DEFINITIONS[dimension]["langfuse_name"]
            langfuse.create_dataset_run_item(
                dataset_name=dataset_name,
                run_name=f"fincat-eval-{dimension}",
                item_id=case.id,
                run_metadata=result,
            )
        except Exception as e:
            logger.debug("Langfuse dataset run 上报失败: {}", e)

    @staticmethod
    def save_results(results: dict[str, Any], output_path: str = "eval_results.json") -> Path:
        """保存评测结果到文件。"""
        path = Path(output_path)
        path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("评测结果已写入 {}", path)
        return path

    @staticmethod
    def summary(results: dict[str, Any]) -> str:
        """生成评测结果摘要。"""
        lines = [
            f"fincat 评测报告 — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            "=" * 60,
        ]
        for dim, data in results.items():
            if isinstance(data, dict) and "average_score" in data:
                lines.append(f"  {dim}: {data['average_score']:.2%} ({data['cases']} 用例)")
        lines.append("=" * 60)
        return "\n".join(lines)
