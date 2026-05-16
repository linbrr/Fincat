"""Capital budgeting calculations: IRR and NPV."""

from __future__ import annotations

import json
from typing import Any

from fincat.agent.tools._finance_utils import newton_raphson, require
from fincat.agent.tools.base import Tool, tool_parameters


@tool_parameters({
    "type": "object",
    "properties": {
        "calc_type": {
            "type": "string",
            "enum": ["irr", "npv"],
            "description": "Budgeting calculation type",
        },
        "cash_flows": {"type": "array", "items": {"type": "number"}, "description": "Cash flows (CF0 typically negative)"},
        "rate": {"type": "number", "description": "Discount rate for NPV (decimal)"},
        "guess": {"type": "number", "description": "Initial guess for IRR solver", "default": 0.1},
        "max_iterations": {"type": "integer", "description": "Max solver iterations", "default": 1000},
        "tolerance": {"type": "number", "description": "Solver convergence tolerance", "default": 1e-10},
    },
    "required": ["calc_type"],
})
class BudgetingCalcTool(Tool):
    """Capital budgeting: internal rate of return (IRR) and net present value (NPV)."""

    @property
    def name(self) -> str:
        return "budgeting_calc"

    @property
    def description(self) -> str:
        return (
            "Capital budgeting calculations. 2 types: "
            "irr (internal rate of return via Newton-Raphson), "
            "npv (net present value of cash flows). "
            "Cash flows: CF0 is typically negative (investment). Returns JSON."
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

    def _calc_irr(self, **kw: Any) -> dict:
        require(kw, "cash_flows")
        cfs = [float(x) for x in kw["cash_flows"]]
        if len(cfs) < 2:
            raise ValueError("cash_flows must have at least 2 entries")
        signs = [1 if x >= 0 else -1 for x in cfs if x != 0]
        if len(set(signs)) < 2:
            raise ValueError("cash_flows must have at least one sign change")
        guess = float(kw.get("guess", 0.1))
        max_iter = int(kw.get("max_iterations", 1000))
        tol = float(kw.get("tolerance", 1e-10))

        def f(r: float) -> float:
            return sum(cf / (1 + r) ** t for t, cf in enumerate(cfs))

        def f_prime(r: float) -> float:
            return sum(-t * cf / (1 + r) ** (t + 1) for t, cf in enumerate(cfs) if t > 0)

        irr, iters, converged = newton_raphson(f, f_prime, guess, max_iter, tol)
        return {"irr": round(irr, 6), "iterations": iters,
                "converged": converged, "cash_flows": cfs}

    def _calc_npv(self, **kw: Any) -> dict:
        require(kw, "rate", "cash_flows")
        rate = float(kw["rate"])
        cfs = [float(x) for x in kw["cash_flows"]]
        npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cfs))
        return {"npv": round(npv, 2), "rate": rate, "cash_flows": cfs}
