"""DynamicRuleStore — persist and manage PatternMiner-generated meta-rules.

Three rule types:
- schedule: time-based triggers → registered as CronService jobs
- predict: semantic triggers → injected into PredictionEngine
- associate: entity co-occurrence → enhances memory context
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


class DynamicRuleStore:
    """Persist PatternMiner-generated dynamic rules (JSONL)."""

    def __init__(self, path: Path):
        self._path = path
        self._rules: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            for line in self._path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rule = json.loads(line)
                    rid = rule.get("rule_id", "")
                    if rid:
                        self._rules[rid] = rule
                except json.JSONDecodeError:
                    continue
        except OSError:
            logger.warning("DynamicRuleStore: failed to load {}", self._path)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(r, ensure_ascii=False) for r in self._rules.values()]
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
        tmp_path.replace(self._path)  # Atomic rename

    def add_rule(self, rule: dict) -> str:
        """Add a rule (dedup by rule_id). Returns rule_id."""
        rule_id = rule.get("rule_id") or f"dyn_{uuid.uuid4().hex[:8]}"
        rule["rule_id"] = rule_id
        if "created_at" not in rule:
            rule["created_at"] = datetime.now(timezone.utc).isoformat()
        if "source" not in rule:
            rule["source"] = "pattern_miner"
        self._rules[rule_id] = rule
        self._save()
        logger.debug("DynamicRuleStore: added rule {} (type={})", rule_id, rule.get("type"))
        return rule_id

    def remove_rule(self, rule_id: str) -> None:
        if rule_id in self._rules:
            del self._rules[rule_id]
            self._save()
            logger.debug("DynamicRuleStore: removed rule {}", rule_id)

    def get_rules_by_type(self, rule_type: str) -> list[dict]:
        return [r for r in self._rules.values() if r.get("type") == rule_type]

    def get_all(self) -> list[dict]:
        return list(self._rules.values())

    def cleanup_expired(self) -> int:
        """Remove rules past expires_at. Returns count removed."""
        now = datetime.now(timezone.utc)
        expired = []
        for rid, r in self._rules.items():
            exp = r.get("expires_at")
            if exp:
                try:
                    # Normalize Z suffix to +00:00 for Python <3.10 compatibility
                    if datetime.fromisoformat(exp.replace("Z", "+00:00")) < now:
                        expired.append(rid)
                except ValueError:
                    continue  # Skip malformed timestamps
        for rid in expired:
            del self._rules[rid]
        if expired:
            self._save()
            logger.info("DynamicRuleStore: cleaned up {} expired rule(s)", len(expired))
        return len(expired)
