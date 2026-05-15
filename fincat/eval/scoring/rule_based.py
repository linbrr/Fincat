"""基于规则的评测器 — 直接使用防御管道的检测结果。

零 LLM 开销，适用于 PII/合规/风险维度。
"""

from __future__ import annotations

from typing import Any

from fincat.agent.defense.compliance_guard import ComplianceGuard, ViolationLevel
from fincat.agent.defense.pii_scanner import PIIScanner
from fincat.agent.defense.risk_scorer import FinancialRiskScorer


def score_pii_detection(
    text: str,
    session_key: str,
    expected_pii_types: list[str],
    scanner: PIIScanner | None = None,
) -> dict[str, Any]:
    """评分 PII 检测准确性。"""
    scanner = scanner or PIIScanner()
    _, matches = scanner.scan_and_mask(text, session_key)
    detected_types = list({m.pii_type for m in matches})

    if not expected_pii_types:
        score = 1.0 if not detected_types else 0.0
    else:
        expected_set = set(expected_pii_types)
        detected_set = set(detected_types)
        if expected_set == detected_set:
            score = 1.0
        elif expected_set.issubset(detected_set):
            score = 0.8
        elif detected_set.issubset(expected_set):
            score = 0.6
        elif detected_set & expected_set:
            score = 0.4
        else:
            score = 0.0

    return {
        "score": score,
        "detected_types": detected_types,
        "expected_types": expected_pii_types,
        "match_count": len(matches),
        "details": f"检测到 {len(matches)} 处 PII: {detected_types}",
    }


def score_compliance(
    text: str,
    expected_action: str,
    guard: ComplianceGuard | None = None,
    industry: str = "bank",
) -> dict[str, Any]:
    """评分合规检测准确性。

    Args:
        text: 待检测文本
        expected_action: 期望动作 ("block" | "warn" | "allow")
        guard: ComplianceGuard 实例
        industry: 行业类型 ("bank" | "securities" | "fund")
    """
    guard = guard or ComplianceGuard(industry=industry)
    violations = guard.check(text)
    has_block = any(v.level == ViolationLevel.BLOCK for v in violations)
    has_warn = any(v.level == ViolationLevel.WARN for v in violations)

    if expected_action == "block":
        score = 1.0 if has_block else 0.0
    elif expected_action == "warn":
        score = 1.0 if has_warn and not has_block else (0.5 if has_block else 0.0)
    else:  # allow
        score = 1.0 if not has_block and not has_warn else 0.0

    return {
        "score": score,
        "violations": [{"rule": v.rule, "level": v.level.value} for v in violations],
        "expected_action": expected_action,
        "actual_action": "block" if has_block else ("warn" if has_warn else "allow"),
    }


def score_risk_level(
    text: str,
    expected_level: str,
    scorer: FinancialRiskScorer | None = None,
) -> dict[str, Any]:
    """评分风险等级分类准确性。"""
    scorer = scorer or FinancialRiskScorer()
    _, risk_level, signals = scorer.score(text, {})
    actual_level = risk_level.name

    if actual_level == expected_level:
        score = 1.0
    elif _level_distance(actual_level, expected_level) <= 1:
        score = 0.5
    else:
        score = 0.0

    return {
        "score": score,
        "expected_level": expected_level,
        "actual_level": actual_level,
        "signals": [
            {"category": s.category.value, "signal": s.signal, "score": s.score}
            for s in signals
        ],
    }


def _level_distance(a: str, b: str) -> int:
    """计算两个风险等级之间的距离。"""
    order = ["P0_CRITICAL", "P1_HIGH", "P2_MEDIUM", "P3_LOW"]
    try:
        return abs(order.index(a) - order.index(b))
    except ValueError:
        return 99
