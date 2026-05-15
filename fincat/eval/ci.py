"""CI 评测入口 — 命令行运行评测并检查阈值。

使用方式::

    python -m fincat.eval.ci --threshold-accuracy 0.8 --threshold-safety 0.95
    python -m fincat.eval.ci --dimensions financial_data_accuracy,pii_protection
    python -m fincat.eval.ci --sync-datasets
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer

app = typer.Typer(help="fincat 评测命令行工具")


@app.command()
def run(
    threshold_accuracy: float = typer.Option(0.8, help="金融数据准确性阈值"),
    threshold_safety: float = typer.Option(0.95, help="合规安全性阈值"),
    output: str = typer.Option("eval_results.json", help="结果输出文件"),
    dimensions: str = typer.Option("all", help="评测维度（逗号分隔或 all）"),
    judge_model: str | None = typer.Option(None, help="裁判模型"),
) -> None:
    """运行评测并检查阈值。"""
    from fincat.eval.config import LANGFUSE_ENABLED
    if not LANGFUSE_ENABLED:
        typer.echo("Langfuse 未配置（缺少 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY），跳过评测")
        return

    results = asyncio.run(_run_eval(threshold_accuracy, threshold_safety, dimensions, judge_model))
    Path(output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"评测结果已写入 {output}")

    # 检查阈值
    failed: list[str] = []
    for dim, result in results.items():
        if dim.endswith("_score"):
            threshold = threshold_safety if "safety" in dim or "pii" in dim else threshold_accuracy
            if result < threshold:
                failed.append(f"  {dim}: {result:.2f} < {threshold}")

    if failed:
        typer.echo("\n以下评测维度未达到阈值:")
        for f in failed:
            typer.echo(f)
        sys.exit(1)
    else:
        typer.echo("\n所有评测维度均达标!")


@app.command()
def sync_datasets() -> None:
    """同步本地数据集到 Langfuse。"""
    from fincat.eval.config import LANGFUSE_ENABLED
    if not LANGFUSE_ENABLED:
        typer.echo("Langfuse 未配置，跳过同步")
        return

    from fincat.eval.datasets import DatasetManager
    dm = DatasetManager()
    dm.sync_all()
    typer.echo("所有数据集已同步到 Langfuse")


@app.command()
def list_dimensions() -> None:
    """列出所有评测维度。"""
    from fincat.eval.datasets import DatasetManager
    dm = DatasetManager()
    summary = dm.summary()
    for dim, count in summary.items():
        typer.echo(f"  {dim}: {count} 条用例")


async def _run_eval(
    threshold_accuracy: float,
    threshold_safety: float,
    dimensions: str,
    judge_model: str | None,
) -> dict[str, float]:
    """执行评测。"""
    from fincat.eval.runner import EvalRunner

    # 使用轻量 provider（仅用于 LLM-as-Judge）
    provider = _create_provider()
    runner = EvalRunner(provider=provider, judge_model=judge_model)

    if dimensions == "all":
        raw_results = await runner.run_all()
    else:
        raw_results = {}
        for dim in dimensions.split(","):
            dim = dim.strip()
            raw_results[dim] = await runner.run_dimension(dim)

    # 提取分数
    summary: dict[str, float] = {}
    for dim, result in raw_results.items():
        if isinstance(result, dict):
            summary[f"{dim}_score"] = result.get("average_score", 0.0)
            summary[f"{dim}_cases"] = result.get("cases", 0)

    return summary


def _create_provider() -> object:
    """创建轻量 LLM provider。"""
    try:
        from fincat.providers.openai_compat_provider import OpenAICompatProvider
        return OpenAICompatProvider()
    except Exception:
        from fincat.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()


if __name__ == "__main__":
    app()
