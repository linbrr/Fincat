"""Time value of money: compound interest, annuity future/present value."""

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
            "enum": ["compound_interest", "annuity_fv", "annuity_pv"],
            "description": "TVM calculation type",
        },
        "pv": {"type": "number", "description": "Present value (principal)"},
        "pmt": {"type": "number", "description": "Periodic payment"},
        "rate": {"type": "number", "description": "Interest rate per period (decimal, e.g. 0.05 for 5%)"},
        "periods": {"type": "integer", "description": "Number of periods", "minimum": 1},
    },
    "required": ["calc_type"],
})
class TvmCalcTool(Tool):
    """Time value of money: compound interest, annuity FV/PV."""

    @property
    def name(self) -> str:
        return "tvm_calc"

    @property
    def description(self) -> str:
        return (
            "Time value of money calculations. 3 types: "
            "compound_interest (future value with optional periodic additions), "
            "annuity_fv (future value of regular payments), "
            "annuity_pv (present value of regular payments). "
            "Rate is per-period. Returns JSON."
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

    def _calc_compound_interest(self, **kw: Any) -> dict:
        require(kw, "pv", "rate", "periods")
        pv, rate, n = float(kw["pv"]), float(kw["rate"]), int(kw["periods"])
        pmt = float(kw.get("pmt", 0))
        if rate == 0:
            fv = pv + pmt * n
        else:
            fv = pv * (1 + rate) ** n + pmt * ((1 + rate) ** n - 1) / rate
        total_contributed = pv + pmt * n
        return {"fv": round(fv, 2), "pv": pv, "rate": rate, "periods": n,
                "pmt": pmt, "total_interest": round(fv - total_contributed, 2)}

    def _calc_annuity_fv(self, **kw: Any) -> dict:
        require(kw, "pmt", "rate", "periods")
        pmt, rate, n = float(kw["pmt"]), float(kw["rate"]), int(kw["periods"])
        if rate == 0:
            fv = pmt * n
        else:
            fv = pmt * ((1 + rate) ** n - 1) / rate
        total = pmt * n
        return {"fv": round(fv, 2), "pmt": pmt, "rate": rate, "periods": n,
                "total_contributions": round(total, 2),
                "total_interest": round(fv - total, 2)}

    def _calc_annuity_pv(self, **kw: Any) -> dict:
        require(kw, "pmt", "rate", "periods")
        pmt, rate, n = float(kw["pmt"]), float(kw["rate"]), int(kw["periods"])
        if rate == 0:
            pv = pmt * n
        else:
            pv = pmt * (1 - (1 + rate) ** (-n)) / rate
        return {"pv": round(pv, 2), "pmt": pmt, "rate": rate, "periods": n,
                "total_contributions": round(pmt * n, 2)}
