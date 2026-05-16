"""Bond calculations: pricing and yield-to-maturity."""

from __future__ import annotations

import json
from typing import Any

from fincat.agent.tools.base import Tool, tool_parameters
from fincat.agent.tools._finance_utils import require, newton_raphson


@tool_parameters({
    "type": "object",
    "properties": {
        "calc_type": {
            "type": "string",
            "enum": ["bond_price", "bond_ytm"],
            "description": "Bond calculation type",
        },
        "face_value": {"type": "number", "description": "Bond face/par value"},
        "coupon_rate": {"type": "number", "description": "Bond coupon rate (decimal)"},
        "periods": {"type": "integer", "description": "Remaining periods to maturity"},
        "yield_rate": {"type": "number", "description": "Market yield/discount rate (decimal)"},
        "frequency": {"type": "integer", "description": "Coupon payments per year", "default": 1},
        "price": {"type": "number", "description": "Bond market price (for YTM calculation)"},
        "guess": {"type": "number", "description": "Initial guess for YTM solver", "default": 0.05},
    },
    "required": ["calc_type"],
})
class BondCalcTool(Tool):
    """Bond calculations: price from yield, yield from price (YTM)."""

    @property
    def name(self) -> str:
        return "bond_calc"

    @property
    def description(self) -> str:
        return (
            "Bond/fixed-income calculations. 2 types: "
            "bond_price (calculate price from yield), "
            "bond_ytm (calculate yield-to-maturity from price, uses Newton-Raphson solver). "
            "Rates are decimals. Returns JSON."
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

    def _calc_bond_price(self, **kw: Any) -> dict:
        require(kw, "face_value", "coupon_rate", "periods", "yield_rate")
        fv = float(kw["face_value"])
        cr = float(kw["coupon_rate"])
        n = int(kw["periods"])
        y = float(kw["yield_rate"])
        freq = int(kw.get("frequency", 1))
        c = fv * cr / freq
        yf = y / freq
        total_periods = n * freq
        if yf == 0:
            price = fv + c * total_periods
        else:
            price = sum(c / (1 + yf) ** t for t in range(1, total_periods + 1))
            price += fv / (1 + yf) ** total_periods
        current_yield = (c * freq) / price if price != 0 else 0
        return {"price": round(price, 4), "face_value": fv,
                "coupon_rate": cr, "yield_rate": y, "periods": n,
                "frequency": freq, "coupon_payment": round(c, 2),
                "current_yield": round(current_yield, 6)}

    def _calc_bond_ytm(self, **kw: Any) -> dict:
        require(kw, "price", "face_value", "coupon_rate", "periods")
        p = float(kw["price"])
        fv = float(kw["face_value"])
        cr = float(kw["coupon_rate"])
        n = int(kw["periods"])
        freq = int(kw.get("frequency", 1))
        guess = float(kw.get("guess", 0.05))
        c = fv * cr / freq
        total_periods = n * freq

        def f(y: float) -> float:
            yf = y / freq
            if yf == -1:
                return float("inf")
            pv = sum(c / (1 + yf) ** t for t in range(1, total_periods + 1))
            pv += fv / (1 + yf) ** total_periods
            return pv - p

        def f_prime(y: float) -> float:
            yf = y / freq
            if yf == -1:
                return float("inf")
            deriv = sum(-t * c / (freq * (1 + yf) ** (t + 1)) for t in range(1, total_periods + 1))
            deriv += -total_periods * fv / (freq * (1 + yf) ** (total_periods + 1))
            return deriv

        ytm, iters, converged = newton_raphson(f, f_prime, guess)
        return {"ytm": round(ytm, 6), "price": p, "face_value": fv,
                "coupon_rate": cr, "periods": n, "frequency": freq,
                "iterations": iters, "converged": converged}
