"""Compliance Guard — financial compliance speech detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from fincat.providers.base import LLMProvider


class ViolationLevel(Enum):
    """Compliance violation severity level."""
    BLOCK = "block"      # Direct block - response cannot be sent
    REWRITE = "rewrite"  # Trigger rewrite with compliant alternative
    WARN = "warn"        # Warning - log but allow
    LOG = "log"         # Log only - informational


@dataclass
class ComplianceViolation:
    """Represents a detected compliance violation."""
    rule: str
    level: ViolationLevel
    matched_text: str
    suggestion: str | None = None


# Industry-specific compliance rules
INDUSTRY_RULES = {
    "bank": {
        ViolationLevel.BLOCK: [
            "保本", "稳赚不赔", "刚性兑付", "存款保险保证赔付",
            "本金百分百安全", "零风险", "绝对收益",
        ],
        ViolationLevel.WARN: [
            "业绩比较基准", "历史收益不代表未来", "风险提示",
            "有可能亏损", "不保证收益",
        ],
    },
    "securities": {
        ViolationLevel.BLOCK: [
            "保证收益", "内幕消息", "坐庄", "涨停板",
            "内幕", "老鼠仓", "收益承诺",
        ],
        ViolationLevel.WARN: [
            "投资有风险", "可能亏损", "不保证本金",
            "股市有风险", "入市需谨慎",
        ],
    },
    "fund": {
        ViolationLevel.BLOCK: [
            "零风险", "刚性兑付", "收益兜底", "保本",
            "保证收益", "一定赚钱", "稳赚",
        ],
        ViolationLevel.WARN: [
            "基金有风险", "过往业绩不预示未来",
            "投资需谨慎", "不保证本金安全",
        ],
    },
}

# Generic high-severity violations (all industries)
GENERIC_BLOCK = [
    "银保监会", "证监会", "法院起诉", "媒体曝光",
    "去哪投诉", "怎么举报", "我要投诉",
]


class ComplianceGuard:
    """Financial compliance speech detector.

    Detects prohibited/warning speech patterns in financial scenarios.
    Supports industry-specific rules (bank, securities, fund).

    Usage:
        guard = ComplianceGuard(industry="bank")
        violations = guard.check("这个产品保本没问题")
        # Returns [ComplianceViolation(rule="BLOCK_KEYWORD:保本", ...)]
    """

    def __init__(
        self,
        industry: str = "bank",
        enable_semantic: bool = False,
        provider: "LLMProvider | None" = None,
    ):
        """Initialize ComplianceGuard.

        Args:
            industry: Industry type - "bank", "securities", or "fund"
            enable_semantic: Enable semantic detection (Phase 4 feature)
            provider: LLM provider for semantic detection
        """
        self.industry = industry
        self.enable_semantic = enable_semantic
        self.provider = provider

        rules = INDUSTRY_RULES.get(industry, INDUSTRY_RULES["bank"])
        self._block_keywords = set(rules.get(ViolationLevel.BLOCK, []))
        self._warn_keywords = set(rules.get(ViolationLevel.WARN, []))

    def check(self, text: str) -> list[ComplianceViolation]:
        """Check text for compliance violations using keyword rules.

        Args:
            text: Text to check

        Returns:
            List of detected violations (may be empty)
        """
        violations: list[ComplianceViolation] = []

        # Check BLOCK keywords
        for keyword in self._block_keywords:
            if keyword in text:
                violations.append(ComplianceViolation(
                    rule=f"BLOCK_KEYWORD:{keyword}",
                    level=ViolationLevel.BLOCK,
                    matched_text=keyword,
                    suggestion=self._get_suggestion(keyword),
                ))
                logger.warning(
                    "Compliance BLOCK detected: keyword='{}' in text ({} chars)",
                    keyword,
                    len(text),
                )

        # Check WARN keywords (only add if not already have a BLOCK for same keyword)
        existing_block_keywords = {v.matched_text for v in violations}
        for keyword in self._warn_keywords:
            if keyword in text and keyword not in existing_block_keywords:
                violations.append(ComplianceViolation(
                    rule=f"WARN_KEYWORD:{keyword}",
                    level=ViolationLevel.WARN,
                    matched_text=keyword,
                ))

        # Check generic high-severity patterns
        for pattern in GENERIC_BLOCK:
            if pattern in text:
                violations.append(ComplianceViolation(
                    rule=f"GENERIC_HIGH_SEVERITY:{pattern}",
                    level=ViolationLevel.WARN,
                    matched_text=pattern,
                    suggestion="Consider escalating to human agent",
                ))

        return violations

    async def check_with_model(self, text: str) -> list[ComplianceViolation]:
        """Semantic-level compliance detection using LLM.

        This is Phase 4 feature - requires fine-tuned FinBERT model.
        Currently a placeholder for future implementation.

        Args:
            text: Text to check

        Returns:
            List of violations detected by semantic model
        """
        if not self.enable_semantic or self.provider is None:
            return []

        # TODO: Implement semantic detection with FinBERT
        # This would detect patterns like:
        # - "我们这个产品基本不会亏" (risk understatement)
        # - "基本保本" (misleading)
        # - "历史收益一直很稳定" (implying future stability)
        logger.debug("Semantic compliance check not yet implemented")
        return []

    def _get_suggestion(self, keyword: str) -> str:
        """Get compliance suggestion for a blocked keyword."""
        suggestions = {
            "保本": "请勿承诺本金安全，建议改为'不保证本金'或'可能亏损'",
            "稳赚不赔": "禁止承诺收益，建议说明'投资有风险'",
            "刚性兑付": "禁止使用刚性兑付表述，建议说明产品风险",
            "保证收益": "禁止承诺收益，可改为'历史业绩不代表未来'",
            "内幕消息": "严格禁止提及内幕信息",
            "坐庄": "严格禁止使用坐庄等违法表述",
        }
        return suggestions.get(keyword, "请使用合规表述")

    def add_custom_rule(
        self,
        keyword: str,
        level: ViolationLevel,
        suggestion: str | None = None,
    ) -> None:
        """Add a custom compliance rule.

        Args:
            keyword: Keyword to detect
            level: Violation level
            suggestion: Optional suggestion for remediation
        """
        if level == ViolationLevel.BLOCK:
            self._block_keywords.add(keyword)
        elif level == ViolationLevel.WARN:
            self._warn_keywords.add(keyword)

        logger.info("Added custom compliance rule: keyword='{}', level={}", keyword, level.value)

    def get_safe_response(self, original: str, violation: ComplianceViolation) -> str:
        """Generate a safe replacement response for a blocked violation.

        Args:
            original: Original problematic response
            violation: The violation that triggered the block

        Returns:
            Safe compliance response
        """
        return (
            "抱歉，我无法提供这样的回复。投资有风险，建议您咨询专业的金融顾问 "
            "或拨打官方客服热线获取准确信息。基金投资需谨慎，请以产品说明书为准。"
        )
