"""Financial Risk Scorer — risk scoring matrix for financial scenarios."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from loguru import logger


class RiskLevel(Enum):
    """Risk severity level."""
    P0_CRITICAL = 0.9   # Immediate human transfer required
    P1_HIGH = 0.7       # Alert within 5 minutes
    P2_MEDIUM = 0.5     # Batch quality check within 24h
    P3_LOW = 0.3        # Normal flow


class RiskCategory(Enum):
    """Category of risk signal."""
    EMOTION_NEGATIVE = "emotion_negative"       # Negative emotion detected
    INTENT_HIGH_RISK = "intent_high_risk"       # High-risk intent keyword
    ACTION_SENSITIVE = "action_sensitive"        # Sensitive action detected
    CONTENT_SENSITIVE = "content_sensitive"     # Sensitive content detected
    HISTORY_ESCALATION = "history_escalation"  # Historical escalation


@dataclass
class RiskSignal:
    """Represents a detected risk signal."""
    category: RiskCategory
    signal: str
    score: float
    trigger: str


# High-risk intent keywords with base scores
HIGH_RISK_INTENTS = {
    # Complaint escalation - P0 level
    "投诉": 0.9,
    "银保监会": 0.95,
    "证监会": 0.95,
    "起诉": 0.95,
    "举报": 0.9,
    "媒体曝光": 0.9,
    "上访": 0.9,
    "投诉到": 0.85,
    "要投诉": 0.85,
    "投诉电话": 0.85,

    # Fund security - P1 level
    "转账": 0.8,
    "汇款": 0.8,
    "冻结": 0.85,
    "被盗": 0.85,
    "被骗": 0.85,
    "损失": 0.7,
    "亏了": 0.7,
    "本金": 0.6,

    # Emotional distress - P0 level (suicide/self-harm indicators)
    "不想活": 0.95,
    "要死": 0.95,
    "活不下去": 0.95,
    "死了算了": 0.95,

    # Strong negative emotions
    "极度愤怒": 0.85,
    "非常生气": 0.7,
    "太失望": 0.65,
}

# Negative emotion patterns with scores
NEGATIVE_EMOTION_PATTERNS = {
    # Anger
    r"(你妈|混蛋|废物|垃圾|骗人|骗子|无赖)": 0.85,
    r"(什么鬼|神经病|脑子有问题)": 0.7,

    # Anxiety/worry
    r"(怎么办|急|救命|完了|完了|害怕|担心)": 0.6,

    # Disappointment
    r"(失望|再也不|太差|退钱)": 0.7,
    r"(垃圾|烂|差评|投诉)": 0.75,

    # Desperation
    r"(求求|跪求|救命|帮帮我)": 0.65,
}

# Sensitive action patterns (customer service focus)
SENSITIVE_ACTIONS = {
    "修改密码": 0.6,
    "重置密码": 0.6,
    "注销账户": 0.7,
    "关闭账户": 0.7,
    "转账": 0.8,
    "汇款": 0.8,
    "大额": 0.75,
    "境外": 0.7,
    "退费": 0.7,
    "退款": 0.65,
    "撤销": 0.6,
}


class FinancialRiskScorer:
    """Financial risk scoring matrix.

    Calculates risk scores based on multiple dimensions:
    - Intent risk (40%): High-risk intent keywords
    - Emotion risk (30%): Negative emotion detection
    - Action risk (30%): Sensitive operation detection

    Usage:
        scorer = FinancialRiskScorer()
        score, level, signals = scorer.score("我要转账到国外", context={})
        # score=0.8, level=RiskLevel.P1_HIGH, signals=[...]
    """

    # Weights for each dimension (adjusted for customer service scenario)
    INTENT_WEIGHT = 0.3
    EMOTION_WEIGHT = 0.4  # Increased: emotion is more critical in customer service
    ACTION_WEIGHT = 0.3

    # Thresholds for each risk level
    THRESHOLDS = {
        RiskLevel.P0_CRITICAL: 0.9,
        RiskLevel.P1_HIGH: 0.7,
        RiskLevel.P2_MEDIUM: 0.5,
    }

    def __init__(self):
        pass

    def score(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[float, RiskLevel, list[RiskSignal]]:
        """Calculate risk score for input text.

        Args:
            text: User input text
            context: Optional context (previous_risk_level, session_history, etc.)

        Returns:
            Tuple of (final_score, risk_level, list_of_signals)
        """
        signals: list[RiskSignal] = []
        weighted_sum = 0.0
        dimensions_active = 0

        # 1. Intent risk scoring
        intent_score, intent_signals = self._score_intent(text)
        if intent_signals:
            signals.extend(intent_signals)
            weighted_sum += intent_score * self.INTENT_WEIGHT
            dimensions_active += 1

        # 2. Emotion risk scoring
        emotion_score, emotion_signals = self._score_emotion(text)
        if emotion_signals:
            signals.extend(emotion_signals)
            weighted_sum += emotion_score * self.EMOTION_WEIGHT
            dimensions_active += 1

        # 3. Action risk scoring
        action_score, action_signals = self._score_actions(text)
        if action_signals:
            signals.extend(action_signals)
            weighted_sum += action_score * self.ACTION_WEIGHT
            dimensions_active += 1

        # 4. Historical escalation bonus
        if context and context.get("previous_risk_level") in ["P0", "P1"]:
            escalation_signal = RiskSignal(
                category=RiskCategory.HISTORY_ESCALATION,
                signal="previous_risk_history",
                score=0.1,
                trigger="Historical high-risk session",
            )
            signals.append(escalation_signal)
            weighted_sum += 0.1

        # Normalize by active dimensions to prevent inflation
        if dimensions_active > 0:
            final_score = min(1.0, weighted_sum / (weighted_sum + (1 - weighted_sum) * 0.5))
        else:
            final_score = 0.0

        # Determine risk level
        risk_level = self._classify_level(final_score)

        if signals:
            logger.debug(
                "RiskScorer: score={:.2f}, level={}, signals={}",
                final_score,
                risk_level.name,
                len(signals),
            )

        return final_score, risk_level, signals

    def _score_intent(self, text: str) -> tuple[float, list[RiskSignal]]:
        """Score based on high-risk intent keywords."""
        max_score = 0.0
        signals: list[RiskSignal] = []

        for keyword, score in HIGH_RISK_INTENTS.items():
            if keyword in text:
                signals.append(RiskSignal(
                    category=RiskCategory.INTENT_HIGH_RISK,
                    signal=keyword,
                    score=score,
                    trigger=f"High-risk intent: {keyword}",
                ))
                max_score = max(max_score, score)

        return max_score, signals

    def _score_emotion(self, text: str) -> tuple[float, list[RiskSignal]]:
        """Score based on negative emotion patterns."""
        max_score = 0.0
        signals: list[RiskSignal] = []

        for pattern, score in NEGATIVE_EMOTION_PATTERNS.items():
            if re.search(pattern, text):
                signals.append(RiskSignal(
                    category=RiskCategory.EMOTION_NEGATIVE,
                    signal=f"emotion_pattern:{pattern[:20]}",
                    score=score,
                    trigger=f"Negative emotion pattern matched",
                ))
                max_score = max(max_score, score)

        return max_score, signals

    def _score_actions(self, text: str) -> tuple[float, list[RiskSignal]]:
        """Score based on sensitive action keywords."""
        max_score = 0.0
        signals: list[RiskSignal] = []

        for action, score in SENSITIVE_ACTIONS.items():
            if action in text:
                signals.append(RiskSignal(
                    category=RiskCategory.ACTION_SENSITIVE,
                    signal=action,
                    score=score,
                    trigger=f"Sensitive action: {action}",
                ))
                max_score = max(max_score, score)

        return max_score, signals

    def _classify_level(self, score: float) -> RiskLevel:
        """Classify score into risk level."""
        if score >= self.THRESHOLDS[RiskLevel.P0_CRITICAL]:
            return RiskLevel.P0_CRITICAL
        elif score >= self.THRESHOLDS[RiskLevel.P1_HIGH]:
            return RiskLevel.P1_HIGH
        elif score >= self.THRESHOLDS[RiskLevel.P2_MEDIUM]:
            return RiskLevel.P2_MEDIUM
        else:
            return RiskLevel.P3_LOW

    def get_action(self, level: RiskLevel) -> dict[str, Any]:
        """Get recommended action for a risk level.

        Args:
            level: Risk level

        Returns:
            Action dict with type, priority, and message
        """
        actions = {
            RiskLevel.P0_CRITICAL: {
                "type": "human_transfer",
                "priority": "critical",
                "message": "您的问题已触发高风险预警，正在为您转接人工客服，请稍候...",
                "alert_channels": ["wecom", "dingtalk", "sms"],
                "alert_ttl_seconds": 0,
            },
            RiskLevel.P1_HIGH: {
                "type": "priority_queue",
                "priority": "high",
                "message": "您的问题已记录，将优先处理。如需紧急帮助，请拨打客服热线。",
                "alert_channels": ["wecom"],
                "alert_ttl_seconds": 300,
            },
            RiskLevel.P2_MEDIUM: {
                "type": "batch_review",
                "priority": "medium",
                "message": None,
                "alert_channels": [],
                "alert_ttl_seconds": 86400,
            },
            RiskLevel.P3_LOW: {
                "type": "normal",
                "priority": "low",
                "message": None,
                "alert_channels": [],
                "alert_ttl_seconds": None,
            },
        }
        return actions.get(level, actions[RiskLevel.P3_LOW])
