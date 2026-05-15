"""Skill usage tracking and quality scoring."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json


@dataclass
class SkillUsageEntry:
    skill_name: str
    timestamp: str  # ISO 8601
    success: bool
    quality_score: float  # 0.0-1.0 from reflection reusability_score


@dataclass
class SkillQualityScore:
    skill_name: str
    invocation_count: int = 0
    last_used: str | None = None  # ISO 8601
    success_count: int = 0
    failure_count: int = 0
    avg_quality_score: float = 0.0
    quality_score: float = 0.0  # Computed final score
    is_stale: bool = False


class SkillUsageTracker:
    """Tracks skill invocations and computes quality scores.

    Storage: workspace/memory/skill_usage.jsonl
    """

    STALE_DAYS = 30
    QUALITY_WEIGHTS = {
        "invocation": 0.25,
        "recency": 0.25,
        "success_rate": 0.30,
        "avg_quality": 0.20,
    }

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self._usage_file = self.workspace / "memory" / "skill_usage.jsonl"
        self._usage_file.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, SkillQualityScore] = {}
        self._load_cache()

    def record_invocation(
        self,
        skill_name: str,
        success: bool,
        quality_score: float = 0.5,
    ) -> None:
        """Record a skill invocation."""
        entry = SkillUsageEntry(
            skill_name=skill_name,
            timestamp=datetime.now().isoformat(),
            success=success,
            quality_score=quality_score,
        )
        with open(self._usage_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.__dict__, ensure_ascii=False) + "\n")

        # Invalidate cache for this skill
        self._cache.pop(skill_name, None)

    def get_quality_score(self, skill_name: str) -> SkillQualityScore:
        """Get computed quality score for a skill."""
        if skill_name in self._cache:
            return self._cache[skill_name]

        score = self._compute_quality(skill_name)
        self._cache[skill_name] = score
        return score

    def get_all_quality_scores(self) -> list[SkillQualityScore]:
        """Get quality scores for all tracked skills."""
        return list(self._cache.values())

    def _compute_quality(self, skill_name: str) -> SkillQualityScore:
        """Compute quality score from usage history."""
        entries = self._load_entries(skill_name)
        if not entries:
            return SkillQualityScore(skill_name=skill_name)

        invocation_count = len(entries)
        success_count = sum(1 for e in entries if e.success)
        failure_count = invocation_count - success_count
        last_used = max(e.timestamp for e in entries)
        avg_quality = sum(e.quality_score for e in entries) / invocation_count

        # Compute final score
        success_rate = success_count / invocation_count
        days_since_used = (datetime.now() - datetime.fromisoformat(last_used)).days
        recency_score = max(0.0, 1.0 - (days_since_used / 90.0))  # Decay over 90 days
        is_stale = days_since_used > self.STALE_DAYS

        quality = (
            self.QUALITY_WEIGHTS["invocation"] * min(1.0, invocation_count / 20)
            + self.QUALITY_WEIGHTS["recency"] * recency_score
            + self.QUALITY_WEIGHTS["success_rate"] * success_rate
            + self.QUALITY_WEIGHTS["avg_quality"] * avg_quality
        )

        return SkillQualityScore(
            skill_name=skill_name,
            invocation_count=invocation_count,
            last_used=last_used,
            success_count=success_count,
            failure_count=failure_count,
            avg_quality_score=avg_quality,
            quality_score=quality,
            is_stale=is_stale,
        )

    def _load_entries(self, skill_name: str) -> list[SkillUsageEntry]:
        """Load all usage entries for a skill."""
        if not self._usage_file.exists():
            return []
        entries = []
        with open(self._usage_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    if data["skill_name"] == skill_name:
                        entries.append(SkillUsageEntry(**data))
                except (json.JSONDecodeError, KeyError):
                    continue
        return entries

    def _load_cache(self) -> None:
        """Pre-load all skills' quality scores."""
        if not self._usage_file.exists():
            return
        skill_names = set()
        with open(self._usage_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                    skill_names.add(data["skill_name"])
                except (json.JSONDecodeError, KeyError):
                    continue
        for name in skill_names:
            self._cache[name] = self._compute_quality(name)
