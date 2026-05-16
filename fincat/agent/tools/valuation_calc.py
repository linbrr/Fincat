"""Investment valuation calculations: CAPM, WACC, DCF, CAGR, percentile rank."""

from __future__ import annotations

import json
from typing import Any

from fincat.agent.tools._finance_utils import require
from fincat.agent.tools.base import Tool, tool_parameters


@tool_parameters({
    "type": "object",
    "properties": {
        "calc_type": {
            "type": "string",
            "enum": ["capm", "wacc", "dcf", "cagr", "percentile_rank"],
            "description": "Valuation calculation type",
        },
        "risk_free_rate": {"type": "number", "description": "Risk-free rate (decimal)"},
        "beta": {"type": "number", "description": "Stock beta coefficient"},
        "market_return": {"type": "number", "description": "Expected market return (decimal)"},
        "equity": {"type": "number", "description": "Market value of equity"},
        "debt": {"type": "number", "description": "Market value of debt"},
        "cost_of_equity": {"type": "number", "description": "Cost of equity Ke (decimal)"},
        "cost_of_debt": {"type": "number", "description": "Cost of debt Kd (decimal)"},
        "tax_rate": {"type": "number", "description": "Corporate tax rate (decimal)"},
        "cash_flows": {"type": "array", "items": {"type": "number"}, "description": "List of FCFs for DCF"},
        "wacc_rate": {"type": "number", "description": "WACC rate (decimal)"},
        "terminal_growth_rate": {"type": "number", "description": "Terminal growth rate (decimal)"},
        "shares_outstanding": {"type": "number", "description": "Shares outstanding"},
        "net_debt": {"type": "number", "description": "Net debt for EV-to-equity bridge"},
        "begin_value": {"type": "number", "description": "Beginning value (for CAGR)"},
        "end_value": {"type": "number", "description": "Ending value (for CAGR)"},
        "years": {"type": "number", "description": "Number of years"},
        "values": {"type": "array", "items": {"type": "number"}, "description": "Distribution for percentile rank"},
        "value": {"type": "number", "description": "Value to rank"},
    },
    "required": ["calc_type"],
})
class ValuationCalcTool(Tool):
    """Investment valuation: CAPM, WACC, DCF, CAGR, percentile rank."""

    @property
    def name(self) -> str:
        return "valuation_calc"

    @property
    def description(self) -> str:
        return (
            "Investment valuation calculations. 5 types: "
            "capm (cost of equity via CAPM), wacc (weighted average cost of capital), "
            "dcf (discounted cash flow valuation), cagr (compound annual growth rate), "
            "percentile_rank (rank a value within a distribution). "
            "All rates are decimals (0.05 = 5%). Returns JSON."
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

    def _calc_capm(self, **kw: Any) -> dict:
        require(kw, "risk_free_rate", "beta", "market_return")
        rf = float(kw["risk_free_rate"])
        beta = float(kw["beta"])
        rm = float(kw["market_return"])
        ke = rf + beta * (rm - rf)
        return {"cost_of_equity": round(ke, 6), "risk_free_rate": rf,
                "beta": beta, "market_return": rm,
                "market_premium": round(rm - rf, 6)}

    def _calc_wacc(self, **kw: Any) -> dict:
        require(kw, "equity", "debt", "cost_of_equity", "cost_of_debt", "tax_rate")
        e = float(kw["equity"])
        d = float(kw["debt"])
        ke = float(kw["cost_of_equity"])
        kd = float(kw["cost_of_debt"])
        t = float(kw["tax_rate"])
        total = e + d
        if total == 0:
            raise ValueError("equity + debt cannot be zero")
        wacc = (e / total) * ke + (d / total) * kd * (1 - t)
        return {"wacc": round(wacc, 6), "equity_weight": round(e / total, 4),
                "debt_weight": round(d / total, 4),
                "cost_of_equity": ke, "cost_of_debt": kd,
                "after_tax_cost_of_debt": round(kd * (1 - t), 6),
                "tax_rate": t}

    def _calc_dcf(self, **kw: Any) -> dict:
        require(kw, "cash_flows", "wacc_rate", "terminal_growth_rate")
        cfs = [float(x) for x in kw["cash_flows"]]
        wacc = float(kw["wacc_rate"])
        g = float(kw["terminal_growth_rate"])
        if wacc <= g:
            raise ValueError("wacc_rate must be > terminal_growth_rate")
        if not cfs:
            raise ValueError("cash_flows cannot be empty")
        n = len(cfs)
        pv_cfs = sum(cf / (1 + wacc) ** t for t, cf in enumerate(cfs, 1))
        tv = cfs[-1] * (1 + g) / (wacc - g)
        pv_tv = tv / (1 + wacc) ** n
        ev = pv_cfs + pv_tv
        net_debt = float(kw.get("net_debt", 0))
        equity_val = ev - net_debt
        shares = float(kw.get("shares_outstanding", 0))
        per_share = equity_val / shares if shares > 0 else None
        result: dict[str, Any] = {
            "enterprise_value": round(ev, 2),
            "terminal_value": round(tv, 2),
            "pv_of_cash_flows": round(pv_cfs, 2),
            "pv_of_terminal_value": round(pv_tv, 2),
            "equity_value": round(equity_val, 2),
            "wacc": wacc, "terminal_growth_rate": g,
            "cash_flows_input": cfs,
        }
        if per_share is not None:
            result["per_share_value"] = round(per_share, 2)
        return result

    def _calc_cagr(self, **kw: Any) -> dict:
        require(kw, "begin_value", "end_value", "years")
        bv = float(kw["begin_value"])
        ev = float(kw["end_value"])
        n = float(kw["years"])
        if bv <= 0:
            raise ValueError("begin_value must be positive")
        if n <= 0:
            raise ValueError("years must be positive")
        cagr = (ev / bv) ** (1 / n) - 1
        return {"cagr": round(cagr, 6), "begin_value": bv, "end_value": ev,
                "years": n, "total_return": round((ev / bv) - 1, 6)}

    def _calc_percentile_rank(self, **kw: Any) -> dict:
        require(kw, "values", "value")
        vals = sorted(float(x) for x in kw["values"])
        v = float(kw["value"])
        if not vals:
            raise ValueError("values cannot be empty")
        count_below = sum(1 for x in vals if x < v)
        count_equal = sum(1 for x in vals if x == v)
        rank = count_below + count_equal / 2
        pct = (rank / len(vals)) * 100
        return {"percentile_rank": round(pct, 2), "value": v,
                "count": len(vals), "rank": round(rank, 2)}
