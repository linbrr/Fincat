"""Financial ratio analysis: liquidity and leverage ratios."""

from __future__ import annotations

import json
from typing import Any

from fincat.agent.tools.base import Tool, tool_parameters
from fincat.agent.tools._finance_utils import require


@tool_parameters({
    "type": "object",
    "properties": {
        "calc_type": {
            "type": "string",
            "enum": ["ratio_analysis"],
            "description": "Financial ratio calculation type",
        },
        "current_assets": {"type": "number", "description": "Total current assets"},
        "current_liabilities": {"type": "number", "description": "Total current liabilities"},
        "inventory": {"type": "number", "description": "Inventory value"},
        "total_debt": {"type": "number", "description": "Total debt"},
        "total_equity": {"type": "number", "description": "Total equity"},
        "cash": {"type": "number", "description": "Cash and equivalents"},
    },
    "required": ["calc_type"],
})
class RatioCalcTool(Tool):
    """Financial ratio analysis: current ratio, quick ratio, debt-to-equity, cash ratio."""

    @property
    def name(self) -> str:
        return "ratio_calc"

    @property
    def description(self) -> str:
        return (
            "Financial health diagnostics. 1 type: "
            "ratio_analysis (current/quick ratio, debt-to-equity, cash ratio with auto-judgment). "
            "Returns JSON with health assessment."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, calc_type: str, **kwargs: Any) -> str:
        handler = getattr(self, f"_calc_{calc_type}", None)
        if handler is None:
            return json.dumps({"error": f"Unknown calc_type: {calc_type}"})
        try:
            return json.dumps(handler(**kwargs), ensure_ascii=False, indent=2)
        except (ValueError, TypeError, ZeroDivisionError) as e:
            return json.dumps({"error": str(e), "calc_type": calc_type})

    def _calc_ratio_analysis(self, **kw: Any) -> dict:
        require(kw, "current_assets", "current_liabilities")
        ca = float(kw["current_assets"])
        cl = float(kw["current_liabilities"])
        inv = float(kw.get("inventory", 0))
        td = float(kw.get("total_debt", 0))
        te = float(kw.get("total_equity", 0))
        cash = float(kw.get("cash", 0))
        current_ratio = ca / cl if cl != 0 else None
        quick_ratio = (ca - inv) / cl if cl != 0 else None
        de_ratio = td / te if te != 0 else None
        cash_ratio = cash / cl if cl != 0 else None

        def _judge_cr(v: float | None) -> str | None:
            if v is None:
                return None
            if v >= 2:
                return "良好"
            if v >= 1:
                return "一般"
            return "危险"

        def _round(v: float | None) -> float | None:
            return round(v, 4) if v is not None else None

        return {"current_ratio": _round(current_ratio),
                "quick_ratio": _round(quick_ratio),
                "debt_to_equity": _round(de_ratio),
                "cash_ratio": _round(cash_ratio),
                "current_ratio_judgment": _judge_cr(current_ratio)}
