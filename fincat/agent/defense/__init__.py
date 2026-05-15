"""Financial compliance defense layer for fincat agent.

Modules:
- pii_scanner: PII detection and masking
- compliance_guard: Financial compliance speech detection
- risk_scorer: Risk scoring matrix
- alert_manager: Risk alert management
- defense_pipeline: Defense pipeline orchestration
"""

from fincat.agent.defense.pii_scanner import PIIScanner, PIIMatch
from fincat.agent.defense.compliance_guard import ComplianceGuard, ComplianceViolation, ViolationLevel
from fincat.agent.defense.risk_scorer import FinancialRiskScorer, RiskLevel, RiskSignal, RiskCategory
from fincat.agent.defense.alert_manager import RiskAlertManager
from fincat.agent.defense.defense_pipeline import DefensePipeline

__all__ = [
    "PIIScanner",
    "PIIMatch",
    "ComplianceGuard",
    "ComplianceViolation",
    "ViolationLevel",
    "FinancialRiskScorer",
    "RiskLevel",
    "RiskSignal",
    "RiskCategory",
    "RiskAlertManager",
    "DefensePipeline",
]
