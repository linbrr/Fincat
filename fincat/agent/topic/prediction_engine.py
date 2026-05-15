"""PredictionEngine — zero-LLM real-time prediction via rules + vector similarity."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from fincat.agent.embedding import EmbeddingEngine

# Patterns for extracting the primary entity from a user message.
# Matches: stock codes (600519), or 2-4 char Chinese names near action verbs.
_STOCK_CODE_RE = re.compile(r"\b(\d{6})(?:\.(?:SH|SZ|sh|sz))?\b")
_ACTION_VERBS = re.compile(
    r"(?:分析|查看|查询|买入|卖出|建仓|加仓|定投|关注|追踪|研究|看下|看看|怎么样|走势|行情|涨停|跌停)"
)
_CN_ENTITY_RE = re.compile(r"([一-鿿]{2,4}(?:股份|集团|科技|银行|保险|证券)?)")


def _extract_entity(text: str) -> str:
    """Extract the primary entity (stock name/code) from a user message.

    Returns the first plausible entity string, or '' if nothing found.
    """
    # 1. Stock code (highest specificity)
    m = _STOCK_CODE_RE.search(text)
    if m:
        return m.group(1)

    # 2. Chinese noun near an action verb
    for vm in _ACTION_VERBS.finditer(text):
        after = text[vm.end() : vm.end() + 10]
        em = _CN_ENTITY_RE.match(after)
        if em:
            return em.group(1)
        before = text[max(0, vm.start() - 10) : vm.start()]
        em = _CN_ENTITY_RE.search(before)
        if em:
            return em.group(1)

    # 3. Fallback: first 2-4 char Chinese word in the message
    em = _CN_ENTITY_RE.search(text)
    if em:
        return em.group(1)

    return ""


class PredictionEngine:
    """Real-time lightweight prediction: rule matching + vector similarity (zero LLM)."""

    def __init__(self, rules_path: Path, embedding: EmbeddingEngine | None = None):
        self._rules = self._load_rules(rules_path)
        self._rules_path = rules_path
        self._embedding = embedding
        # Dynamic rules from PatternMiner (not persisted to rules.json)
        self._dynamic_rules: list[dict] = []
        # Compiled patterns cache
        self._compiled: dict[str, re.Pattern] = {}
        self._compiled_dynamic: dict[str, re.Pattern] = {}
        for rule in self._rules:
            pattern = rule.get("trigger", {}).get("pattern", "")
            if pattern:
                self._compiled[rule["rule_id"]] = re.compile(pattern)

    def add_dynamic_rule(self, rule: dict) -> None:
        """Add a PatternMiner-generated dynamic predict rule."""
        compiled = {
            "rule_id": rule.get("rule_id", f"dyn_{uuid.uuid4().hex[:8]}"),
            "trigger": rule.get("trigger", {}),
            "action": rule.get("action", {}),
            "confidence": rule.get("confidence", 0.5),
            "source_name": "模式预测",
            "dynamic": True,
        }
        pattern = rule.get("trigger", {}).get("pattern", "")
        if pattern:
            try:
                self._compiled_dynamic[compiled["rule_id"]] = re.compile(pattern)
            except re.error:
                logger.warning("PredictionEngine: invalid dynamic rule pattern: {}", pattern)
                return
        self._dynamic_rules.append(compiled)
        logger.debug("PredictionEngine: added dynamic rule {}", compiled["rule_id"])

    def match_rules(self, user_message: str) -> list[dict]:
        """Match user message against rule patterns (static + dynamic). Returns list of matched actions."""
        matched = []
        # Static rules
        for rule in self._rules:
            if rule.get("confidence", 0) < 0.3:
                continue
            pattern = self._compiled.get(rule["rule_id"])
            if pattern and pattern.search(user_message):
                action = rule["action"].copy()
                action["rule_id"] = rule["rule_id"]
                action["confidence"] = rule.get("confidence", 0.5)
                action["source_name"] = rule.get("source_name", "实时预测")
                matched.append(action)
        # Dynamic rules
        for rule in self._dynamic_rules:
            if rule.get("confidence", 0) < 0.3:
                continue
            pattern = self._compiled_dynamic.get(rule["rule_id"])
            if pattern and pattern.search(user_message):
                action = rule["action"].copy()
                action["rule_id"] = rule["rule_id"]
                action["confidence"] = rule.get("confidence", 0.5)
                action["source_name"] = rule.get("source_name", "模式预测")
                matched.append(action)
        return matched

    def predict_from_context(self, current_message: str, session_history: list[dict] | None = None) -> list[dict]:
        """Context-based prediction: combine rule matches with session context."""
        predictions = self.match_rules(current_message)
        if session_history and len(session_history) >= 2:
            # Check if recent messages suggest a pattern
            recent_text = " ".join(m.get("content", "") for m in session_history[-3:])
            context_matches = self.match_rules(recent_text)
            for m in context_matches:
                if not any(p["rule_id"] == m["rule_id"] for p in predictions):
                    predictions.append(m)
        return predictions

    def predict(self, user_message: str, session_history: list[dict] | None = None) -> list[dict]:
        """Main entry: merge rule matches → create Topic objects → return Top N."""
        predictions = self.predict_from_context(user_message, session_history)
        if not predictions:
            return []

        entity = _extract_entity(user_message)

        topics = []
        for pred in predictions:
            title = pred.get("topic_template", "推荐")
            content = pred.get("content_template", "")
            if entity:
                title = title.replace("{entity}", entity)
                content = content.replace("{entity}", entity)
            topic = {
                "topic_id": f"topic_{uuid.uuid4().hex[:8]}",
                "source": "prediction_engine",
                "source_name": pred.get("source_name", "实时预测"),
                "category": pred.get("category", "insight"),
                "title": title,
                "content": content,
                "priority": pred.get("priority", 1),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "confidence": pred.get("confidence", 0.5),
                "expires_at": self._next_expiry(),
            }
            topics.append(topic)

        # Deduplicate by title
        seen = set()
        unique = []
        for t in topics:
            if t["title"] not in seen:
                seen.add(t["title"])
                unique.append(t)

        return unique

    def update_confidence(self, rule_id: str, feedback: str) -> None:
        """Adjust rule confidence based on user feedback. Write back to rules.json."""
        adjustments = {"click": 0.05, "ignore": -0.02, "reject": -0.10, "engage": 0.10}
        delta = adjustments.get(feedback, 0.0)
        for rule in self._rules:
            if rule["rule_id"] == rule_id:
                old = rule.get("confidence", 0.5)
                rule["confidence"] = max(0.1, min(1.0, old + delta))
                break
        self._save_rules()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save_rules(self) -> None:
        data = {"version": 1, "rules": self._rules}
        self._rules_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _load_rules(path: Path) -> list[dict]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("rules", [])
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _next_expiry() -> str:
        from datetime import timedelta
        return (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
