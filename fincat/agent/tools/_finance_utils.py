"""Shared utilities for financial calculation tools."""

from __future__ import annotations

from typing import Any, Callable


def require(kwargs: dict, *keys: str) -> None:
    """Raise ValueError if any required key is missing."""
    missing = [k for k in keys if k not in kwargs or kwargs[k] is None]
    if missing:
        raise ValueError(f"Missing required parameter(s): {', '.join(missing)}")


def newton_raphson(
    f: Callable[[float], float],
    f_prime: Callable[[float], float],
    guess: float,
    max_iterations: int = 1000,
    tolerance: float = 1e-10,
) -> tuple[float, int, bool]:
    """Newton-Raphson root finder. Returns (root, iterations, converged)."""
    x = guess
    for i in range(max_iterations):
        fx = f(x)
        if abs(fx) < tolerance:
            return x, i + 1, True
        fpx = f_prime(x)
        if abs(fpx) < 1e-15:
            return x, i + 1, False
        x = x - fx / fpx
    return x, max_iterations, False
