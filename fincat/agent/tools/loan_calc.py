"""Loan calculations: equal-payment and equal-principal repayment schedules."""

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
            "enum": ["loan_payment", "amortization_schedule",
                     "equal_principal_payment", "equal_principal_schedule"],
            "description": "Loan calculation type",
        },
        "principal": {"type": "number", "description": "Loan principal amount"},
        "rate": {"type": "number", "description": "Interest rate per period (decimal, e.g. 0.004 for monthly 4.8%)"},
        "periods": {"type": "integer", "description": "Total number of repayment periods", "minimum": 1},
        "max_periods": {"type": "integer", "description": "Max rows in schedule output", "default": 360},
    },
    "required": ["calc_type"],
})
class LoanCalcTool(Tool):
    """Loan calculations: monthly payment, amortization schedules (equal-payment & equal-principal)."""

    @property
    def name(self) -> str:
        return "loan_calc"

    @property
    def description(self) -> str:
        return (
            "Loan & mortgage calculations. 4 types: "
            "loan_payment (monthly payment for equal-payment loan), "
            "amortization_schedule (full repayment schedule), "
            "equal_principal_payment (first/last payment for equal-principal loan), "
            "equal_principal_schedule (equal-principal repayment schedule). "
            "Rate is per-period (e.g. monthly rate for mortgages). Returns JSON."
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

    def _calc_loan_payment(self, **kw: Any) -> dict:
        require(kw, "principal", "rate", "periods")
        p, rate, n = float(kw["principal"]), float(kw["rate"]), int(kw["periods"])
        if rate == 0:
            pmt = p / n
        else:
            pmt = p * rate / (1 - (1 + rate) ** (-n))
        total = pmt * n
        return {"payment": round(pmt, 2), "principal": p, "rate": rate,
                "periods": n, "total_paid": round(total, 2),
                "total_interest": round(total - p, 2)}

    def _calc_amortization_schedule(self, **kw: Any) -> dict:
        require(kw, "principal", "rate", "periods")
        p, rate, n = float(kw["principal"]), float(kw["rate"]), int(kw["periods"])
        max_p = int(kw.get("max_periods", 360))
        if rate == 0:
            pmt = p / n
        else:
            pmt = p * rate / (1 - (1 + rate) ** (-n))
        balance = p
        schedule = []
        total_paid = 0.0
        total_interest = 0.0
        for t in range(1, min(n, max_p) + 1):
            interest = balance * rate
            princ_part = pmt - interest
            balance -= princ_part
            if balance < 0:
                balance = 0.0
            total_paid += pmt
            total_interest += interest
            schedule.append({"period": t, "payment": round(pmt, 2),
                             "principal_part": round(princ_part, 2),
                             "interest": round(interest, 2),
                             "balance": round(balance, 2)})
        result: dict[str, Any] = {
            "schedule": schedule,
            "summary": {"total_paid": round(total_paid, 2),
                        "total_interest": round(total_interest, 2)},
        }
        if n > max_p:
            result["truncated"] = True
            result["note"] = f"Showing first {max_p} of {n} periods"
        return result

    def _calc_equal_principal_payment(self, **kw: Any) -> dict:
        require(kw, "principal", "rate", "periods")
        p, rate, n = float(kw["principal"]), float(kw["rate"]), int(kw["periods"])
        princ_per_period = p / n
        first_interest = p * rate
        first_payment = princ_per_period + first_interest
        last_interest = princ_per_period * rate
        last_payment = princ_per_period + last_interest
        total_interest = n * (2 * first_interest + (n - 1) * (-princ_per_period * rate)) / 2
        total_paid = p + total_interest
        return {"first_payment": round(first_payment, 2),
                "last_payment": round(last_payment, 2),
                "payment_decrease": round(princ_per_period * rate, 2),
                "total_paid": round(total_paid, 2),
                "total_interest": round(total_interest, 2),
                "principal": p, "rate": rate, "periods": n}

    def _calc_equal_principal_schedule(self, **kw: Any) -> dict:
        require(kw, "principal", "rate", "periods")
        p, rate, n = float(kw["principal"]), float(kw["rate"]), int(kw["periods"])
        max_p = int(kw.get("max_periods", 360))
        princ_per_period = p / n
        balance = p
        schedule = []
        total_paid = 0.0
        total_interest = 0.0
        for t in range(1, min(n, max_p) + 1):
            interest = balance * rate
            payment = princ_per_period + interest
            balance -= princ_per_period
            if balance < 0:
                balance = 0.0
            total_paid += payment
            total_interest += interest
            schedule.append({"period": t, "payment": round(payment, 2),
                             "principal_part": round(princ_per_period, 2),
                             "interest": round(interest, 2),
                             "balance": round(balance, 2)})
        result: dict[str, Any] = {
            "schedule": schedule,
            "summary": {"total_paid": round(total_paid, 2),
                        "total_interest": round(total_interest, 2)},
        }
        if n > max_p:
            result["truncated"] = True
            result["note"] = f"Showing first {max_p} of {n} periods"
        return result
