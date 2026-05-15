"""Langfuse 评测配置 — 环境变量驱动，零侵入。"""

from __future__ import annotations

import os
from pathlib import Path

# 自动加载项目根目录 .env 文件（如果 python-dotenv 可用）
try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parent.parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
except ImportError:
    pass

# Langfuse connection (already used by openai_compat_provider.py)
LANGFUSE_PUBLIC_KEY: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY: str = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST: str = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

# Feature gates
LANGFUSE_ENABLED: bool = bool(LANGFUSE_SECRET_KEY and LANGFUSE_PUBLIC_KEY)
FINCAT_EVAL_ENABLED: bool = os.environ.get("FINCAT_EVAL_ENABLED", "0") == "1"
FINCAT_EVAL_DATASETS_ENABLED: bool = os.environ.get("FINCAT_EVAL_DATASETS", "0") == "1"

# Judge model for LLM-as-judge evaluators
FINCAT_JUDGE_MODEL: str = os.environ.get("FINCAT_JUDGE_MODEL", "claude-haiku-4-20250414")
FINCAT_JUDGE_API_KEY: str = os.environ.get("FINCAT_JUDGE_API_KEY", "")

# CI thresholds
FINCAT_EVAL_THRESHOLD_ACCURACY: float = float(os.environ.get("FINCAT_EVAL_THRESHOLD_ACCURACY", "0.8"))
FINCAT_EVAL_THRESHOLD_SAFETY: float = float(os.environ.get("FINCAT_EVAL_THRESHOLD_SAFETY", "0.95"))
