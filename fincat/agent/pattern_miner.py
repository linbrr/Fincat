"""PatternMiner — time-periodicity mining from interaction logs.

Semantic association and entity association are handled by Dream Phase 2 (LLM-based).
Only time-periodicity patterns are unique to PatternMiner (timestamp-based analysis).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fincat.agent.resource_store import ResourceStore

# Confidence thresholds
_TIME_PERIODICITY_THRESHOLD = 0.8


class PatternMiner:
    """Time-periodicity mining from interaction logs."""

    def __init__(self, resource_store: ResourceStore):
        self._resource_store = resource_store

    async def mine_time_periodicity(self, days: int = 30) -> list[dict]:
        """Mine time-based periodic patterns from interaction logs.

        Analyzes:
        - Day-of-week patterns (e.g., "every Friday afternoon")
        - Hour-of-day patterns (e.g., "every morning at 9am")
        """
        logs = self._resource_store.read_recent_logs(hours=days * 24)
        if not logs:
            return []

        # Group by (weekday, hour) → actions
        time_actions: dict[tuple[int, int, str], int] = defaultdict(int)
        action_totals: Counter[str] = Counter()

        for log in logs:
            ts = log.get("timestamp", "")
            action = log.get("action", "")
            if not ts or not action:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            key = (dt.weekday(), dt.hour, action)
            time_actions[key] += 1
            action_totals[action] += 1

        patterns = []
        for (weekday, hour, action), count in time_actions.items():
            total = action_totals[action]
            if total < 5:
                continue
            confidence = count / total
            if count < 3 or confidence < _TIME_PERIODICITY_THRESHOLD:
                day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
                patterns.append({
                    "type": "time_periodicity",
                    "action": action,
                    "weekday": weekday,
                    "hour": hour,
                    "day_name": day_names[weekday],
                    "confidence": round(confidence, 2),
                    "count": count,
                    "total": total,
                })

        return patterns

    async def run_daily(self, existing_rules: list[dict] | None = None) -> list[dict]:
        """Daily entry point: mine time-periodicity patterns → generate schedule rules.

        Args:
            existing_rules: Current rules in DynamicRuleStore, used for deduplication.

        Output: list of rule dicts with type=schedule.
        """
        time_patterns = await self.mine_time_periodicity()

        # Build set of existing schedule exprs for dedup
        existing_keys: set[str] = set()
        if existing_rules:
            for r in existing_rules:
                if r.get("type") == "schedule":
                    existing_keys.add(r.get("schedule", {}).get("expr", ""))

        rules = []
        now = datetime.now(timezone.utc)
        day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

        for p in time_patterns:
            weekday = p.get("weekday", 0)
            hour = p.get("hour", 0)
            action = p.get("action", "")
            confidence = p.get("confidence", 0)
            cron_expr = f"{hour} * * {weekday + 1} *"

            if cron_expr in existing_keys:
                continue
            existing_keys.add(cron_expr)

            rules.append({
                "rule_id": f"dyn_{uuid.uuid4().hex[:8]}",
                "type": "schedule",
                "source": "pattern_miner",
                "schedule": {"kind": "cron", "expr": cron_expr},
                "action": {"task": action, "preload": True},
                "confidence": confidence,
                "expires_at": (now + timedelta(days=30)).isoformat(),
                "created_at": now.isoformat(),
                "description": f"{day_names[weekday]} {hour}点 {action}",
            })

        rules.sort(key=lambda r: (-r.get("confidence", 0), r.get("rule_id", "")))
        return rules[:10]
