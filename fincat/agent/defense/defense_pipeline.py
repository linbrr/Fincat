"""Defense Pipeline — orchestration of all defense components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from loguru import logger

from fincat.agent.defense.pii_scanner import PIIScanner, PIIMatch
from fincat.agent.defense.compliance_guard import ComplianceGuard, ComplianceViolation, ViolationLevel
from fincat.agent.defense.risk_scorer import FinancialRiskScorer, RiskLevel, RiskSignal
from fincat.agent.defense.alert_manager import RiskAlertManager
from fincat.eval.observe import observe

if TYPE_CHECKING:
    from fincat.agent.runner import AgentRunSpec


@dataclass
class DefenseResult:
    """Result of defense pipeline processing."""
    messages: list[dict[str, Any]] | None = None
    response: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    redirect_message: str | None = None
    pii_matches: list[PIIMatch] | None = None
    risk_level: RiskLevel | None = None
    risk_signals: list[RiskSignal] | None = None
    compliance_violations: list[ComplianceViolation] | None = None


class DefensePipeline:
    """Orchestrates all defense components.

    Defense Pipeline执行顺序：
    1. 输入处理：PII脱敏 → 风险评分 → 必要时的拦截/转人工
    2. 输出处理：合规校验 → PII还原

    Usage:
        pipeline = DefensePipeline(
            pii_scanner=PIIScanner(),
            compliance_guard=ComplianceGuard(industry="bank"),
            risk_scorer=FinancialRiskScorer(),
            alert_manager=RiskAlertManager(),
        )

        # Input processing
        result = await pipeline.process_input(messages, spec)
        if result.blocked:
            return result.redirect_message

        # Output processing
        safe_response = await pipeline.process_output(response_text, spec)
    """

    def __init__(
        self,
        pii_scanner: PIIScanner | None = None,
        compliance_guard: ComplianceGuard | None = None,
        risk_scorer: FinancialRiskScorer | None = None,
        alert_manager: RiskAlertManager | None = None,
    ):
        self.pii_scanner = pii_scanner or PIIScanner()
        self.compliance_guard = compliance_guard or ComplianceGuard()
        self.risk_scorer = risk_scorer or FinancialRiskScorer()
        self.alert_manager = alert_manager or RiskAlertManager()

    @observe(name="defense.process_input")
    async def process_input(
        self,
        messages: list[dict[str, Any]],
        spec: "AgentRunSpec",
    ) -> DefenseResult:
        """Process input messages through defense pipeline.

        Args:
            messages: List of message dicts
            spec: AgentRunSpec with session_key and context

        Returns:
            DefenseResult with processed messages and any blocking info
        """
        session_key = spec.session_key or "default"
        result = DefenseResult(messages=messages)

        # 1. PII Scanning and Masking
        if self.pii_scanner:
            result.pii_matches = []
            for msg in messages:
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if not content or not isinstance(content, str):
                    continue

                masked_content, matches = self.pii_scanner.scan_and_mask(content, session_key)
                if matches:
                    msg["content"] = masked_content
                    result.pii_matches.extend(matches)
                    logger.debug(
                        "DefensePipeline: masked {} PII in session {}",
                        len(matches),
                        session_key,
                    )

        # 2. Risk Scoring
        if self.risk_scorer:
            # Get the latest user message for risk assessment
            user_content = ""
            for msg in reversed(messages):
                if msg.get("role") == "user" and msg.get("content"):
                    user_content = msg.get("content", "")
                    break

            if user_content:
                # Build context from spec
                context = {
                    "previous_risk_level": getattr(spec, "previous_risk_level", None),
                }

                score, risk_level, signals = self.risk_scorer.score(user_content, context)
                result.risk_level = risk_level
                result.risk_signals = signals

                # Get action based on risk level
                action = self.risk_scorer.get_action(risk_level)

                if risk_level == RiskLevel.P0_CRITICAL:
                    # P0: Immediate human transfer
                    logger.warning(
                        "DefensePipeline: P0 risk detected for session {}, triggering human transfer",
                        session_key,
                    )
                    result.blocked = True
                    result.block_reason = "P0_CRITICAL"
                    result.redirect_message = action["message"]

                    # Send alert
                    await self.alert_manager.send_alert(
                        session_key=session_key,
                        risk_level=risk_level,
                        signals=signals,
                        context={"action": action},
                    )

                    return result

                elif risk_level == RiskLevel.P1_HIGH:
                    # P1: Log warning, allow to continue but alert
                    logger.warning(
                        "DefensePipeline: P1 risk detected for session {}",
                        session_key,
                    )
                    await self.alert_manager.send_alert(
                        session_key=session_key,
                        risk_level=risk_level,
                        signals=signals,
                    )

        # 3. Compliance Check (input side - rare, but possible)
        if self.compliance_guard:
            for msg in messages:
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if not content or not isinstance(content, str):
                    continue

                violations = self.compliance_guard.check(content)
                if violations:
                    result.compliance_violations = violations
                    # Input compliance violations are usually just logged
                    # (user said something bad, we don't block them)
                    for v in violations:
                        if v.level == ViolationLevel.BLOCK:
                            logger.warning(
                                "DefensePipeline: user input BLOCK violation: {}",
                                v.rule,
                            )

        return result

    @observe(name="defense.process_output")
    async def process_output(
        self,
        response_text: str,
        spec: "AgentRunSpec",
    ) -> str:
        """Process output response through defense pipeline.

        Args:
            response_text: Original LLM response
            spec: AgentRunSpec with session_key

        Returns:
            Safe response (possibly modified or replaced)
        """
        if not response_text:
            return response_text

        session_key = spec.session_key or "default"
        safe_response = response_text

        # 1. Compliance Check on Output
        if self.compliance_guard:
            violations = self.compliance_guard.check(response_text)

            for violation in violations:
                if violation.level == ViolationLevel.BLOCK:
                    logger.warning(
                        "DefensePipeline: output BLOCK violation: {}, replacing response",
                        violation.rule,
                    )
                    safe_response = self.compliance_guard.get_safe_response(
                        response_text, violation
                    )
                    break

        # 2. PII Restoration
        if self.pii_scanner and "[ID_" in safe_response or "[CARD_" in safe_response or "[PHONE_" in safe_response:
            safe_response = self.pii_scanner.restore(safe_response, session_key)

        return safe_response

    def get_stats(self, session_key: str) -> dict[str, Any]:
        """Get defense statistics for a session.

        Args:
            session_key: Session identifier

        Returns:
            Dict with defense stats
        """
        return {
            "pii_masked_count": self.pii_scanner.get_masked_count(session_key) if self.pii_scanner else 0,
            "alert_history_count": len(self.alert_manager.get_alert_history(session_key)) if self.alert_manager else 0,
        }
