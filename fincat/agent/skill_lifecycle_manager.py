"""Skill lifecycle management: staleness, merge detection, cleanup."""

import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class MergeCandidate:
    """Represents a pair of skills that could be merged."""
    skill_a: str
    skill_b: str
    similarity_score: float
    shared_tools: list[str]
    shared_triggers: list[str]
    suggested_name: str | None = None


@dataclass
class LifecycleReport:
    """Report of skill lifecycle status."""
    stale_skills: list[dict[str, Any]]
    merge_candidates: list[MergeCandidate]
    invalid_skills: list[dict[str, Any]]
    stats: dict[str, int]


class SkillLifecycleManager:
    """
    Manages skill lifecycle: staleness detection, merge suggestions, cleanup.

    Storage:
      - workspace/memory/skill_usage.jsonl  (SkillUsageTracker records)
      - workspace/skills/.invalid/          (failed validation skills)
    """

    STALE_DAYS = 30
    MERGE_SIMILARITY_THRESHOLD = 0.5
    AUTO_DELETE_INVALID_DAYS = 180

    def __init__(
        self,
        workspace: Path,
        usage_tracker,  # SkillUsageTracker
        deduplicator,   # SkillDeduplicator
        staleness_days: int = 30,
        merge_threshold: float = 0.5,
        auto_delete_invalid_days: int = 180,
        embedding=None,  # EmbeddingEngine for vector similarity
    ):
        self.workspace = Path(workspace)
        self.usage_tracker = usage_tracker
        self.deduplicator = deduplicator
        self.STALE_DAYS = staleness_days
        self.MERGE_SIMILARITY_THRESHOLD = merge_threshold
        self.AUTO_DELETE_INVALID_DAYS = auto_delete_invalid_days
        self._skills_dir = workspace / "skills"
        self._invalid_dir = self._skills_dir / ".invalid"
        self._embedding = embedding

    # ------------------------------------------------------------------------
    # Staleness
    # ------------------------------------------------------------------------

    def check_staleness(self) -> list[dict[str, Any]]:
        """Find skills that haven't been used in STALE_DAYS."""
        stale = []
        for skill_name in self._get_all_skill_names():
            qs = self.usage_tracker.get_quality_score(skill_name)
            if qs.is_stale:
                days_since_used = 0
                if qs.last_used:
                    days_since_used = (
                        datetime.now() - datetime.fromisoformat(qs.last_used)
                    ).days
                stale.append({
                    "skill_name": skill_name,
                    "last_used": qs.last_used,
                    "days_since_used": days_since_used,
                    "invocation_count": qs.invocation_count,
                    "quality_score": qs.quality_score,
                })
        return stale

    def mark_stale(self, skill_name: str) -> bool:
        """Mark a skill as stale by adding stale: true to frontmatter metadata."""
        skill_path = self._find_skill_path(skill_name)
        if not skill_path:
            return False

        content = skill_path.read_text(encoding="utf-8")
        if "stale: true" in content:
            return True  # Already marked

        # Add stale: true to metadata section
        if re.search(r'^metadata:\s*$', content, re.MULTILINE):
            content = re.sub(
                r'^metadata:\s*$',
                'metadata:\n  fincat:\n    stale: true',
                content,
                flags=re.MULTILINE
            )
        elif re.search(r'^---\s*\n(.*?)(---)', content, re.DOTALL):
            content = re.sub(
                r'^(---\s*\n.*?)(---)',
                r'\1stale: true\n\2',
                content,
                flags=re.DOTALL
            )

        skill_path.write_text(content, encoding="utf-8")
        logger.info("[LifecycleManager] Marked skill '{}' as stale", skill_name)
        return True

    def unmark_stale(self, skill_name: str) -> bool:
        """Remove stale flag when skill becomes active again."""
        skill_path = self._find_skill_path(skill_name)
        if not skill_path:
            return False

        content = skill_path.read_text(encoding="utf-8")
        original = content
        content = re.sub(r'^stale:\s*true\s*$\n', '', content, flags=re.MULTILINE)
        content = re.sub(r'fincat:\s*\n\s*stale:\s*true\s*\n', 'fincat:\n', content)
        if content != original:
            skill_path.write_text(content, encoding="utf-8")
            logger.info("[LifecycleManager] Unmarked '{}' as stale", skill_name)
        return True

    # ------------------------------------------------------------------------
    # Merge Detection
    # ------------------------------------------------------------------------

    def find_merge_candidates(self) -> list[MergeCandidate]:
        """Find pairs of skills with Jaccard similarity >= MERGE_SIMILARITY_THRESHOLD."""
        candidates = []
        skill_names = self._get_all_skill_names()

        for i, name_a in enumerate(skill_names):
            for name_b in skill_names[i + 1:]:
                content_a = self._load_skill_content(name_a)
                content_b = self._load_skill_content(name_b)

                if not content_a or not content_b:
                    continue

                score = self._compute_similarity(content_a, content_b)

                if score >= self.MERGE_SIMILARITY_THRESHOLD:
                    shared_tools = self._find_shared_tools(content_a, content_b)
                    shared_triggers = self._find_shared_triggers(content_a, content_b)
                    suggested_name = self._suggest_merged_name(name_a, name_b)

                    candidates.append(MergeCandidate(
                        skill_a=name_a,
                        skill_b=name_b,
                        similarity_score=score,
                        shared_tools=shared_tools,
                        shared_triggers=shared_triggers,
                        suggested_name=suggested_name,
                    ))

        return candidates

    # ------------------------------------------------------------------------
    # Invalid Cleanup
    # ------------------------------------------------------------------------

    def check_invalid_cleanup(self) -> list[dict[str, Any]]:
        """Find invalid skills older than AUTO_DELETE_INVALID_DAYS for cleanup."""
        to_delete = []

        if not self._invalid_dir.exists():
            return []

        for skill_dir in self._invalid_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            created = datetime.fromtimestamp(skill_dir.stat().st_ctime)
            age_days = (datetime.now() - created).days

            if age_days >= self.AUTO_DELETE_INVALID_DAYS:
                errors_file = skill_dir / "errors.txt"
                errors = []
                if errors_file.exists():
                    errors = errors_file.read_text().splitlines()

                to_delete.append({
                    "skill_name": skill_dir.name,
                    "age_days": age_days,
                    "errors": errors,
                    "path": str(skill_dir),
                })

        return to_delete

    def delete_invalid_skill(self, skill_name: str) -> bool:
        """Permanently delete an invalid skill."""
        skill_dir = self._invalid_dir / skill_name
        if not skill_dir.exists():
            return False

        shutil.rmtree(skill_dir)
        logger.info("[LifecycleManager] Deleted invalid skill '{}'", skill_name)
        return True

    # ------------------------------------------------------------------------
    # Full Report
    # ------------------------------------------------------------------------

    def get_lifecycle_report(self) -> LifecycleReport:
        """Generate full lifecycle status report."""
        stale = self.check_staleness()
        merge_candidates = self.find_merge_candidates()
        invalid = self.check_invalid_cleanup()

        all_skills = self._get_all_skill_names()
        stats = {
            "total_skills": len(all_skills),
            "stale_count": len(stale),
            "merge_candidates": len(merge_candidates),
            "invalid_count": len(invalid),
            "auto_generated": sum(1 for s in all_skills if s.startswith("auto-")),
        }

        return LifecycleReport(
            stale_skills=stale,
            merge_candidates=merge_candidates,
            invalid_skills=invalid,
            stats=stats,
        )

    # ------------------------------------------------------------------------
    # Auto Cleanup
    # ------------------------------------------------------------------------

    def auto_cleanup(self) -> list[str]:
        """Auto-archive low-quality skills to .invalid/.

        Rules:
        - invocation_count == 1 and last_used > 14 days ago → archive
        - invocation_count == 0 and creation time > 7 days → archive
        - Skip: always-on skills, inject:false skills
        """
        archived: list[str] = []
        now = datetime.now()

        for skill_name in self._get_all_skill_names():
            skill_path = self._find_skill_path(skill_name)
            if not skill_path:
                continue

            # Check frontmatter for always/inject flags
            try:
                content = skill_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            if "always: true" in content:
                continue
            if "inject: false" in content:
                continue

            qs = self.usage_tracker.get_quality_score(skill_name)
            created = datetime.fromtimestamp(skill_path.parent.stat().st_ctime)
            age_days = (now - created).days

            should_archive = False
            reason = ""

            if qs.invocation_count == 0 and age_days >= 7:
                should_archive = True
                reason = f"零使用且已创建 {age_days} 天"
            elif qs.invocation_count == 1:
                if qs.last_used:
                    days_since_used = (now - datetime.fromisoformat(qs.last_used)).days
                    if days_since_used >= 14:
                        should_archive = True
                        reason = f"仅调用 1 次且 {days_since_used} 天前使用"
                elif age_days >= 14:
                    should_archive = True
                    reason = f"仅调用 1 次且已创建 {age_days} 天"

            if should_archive:
                self._archive_skill(skill_name, reason)
                archived.append(skill_name)

        if archived:
            logger.info("[LifecycleManager] Auto-archived {} skills: {}", len(archived), archived)
        return archived

    def _archive_skill(self, skill_name: str, reason: str) -> bool:
        """Move a skill directory to .invalid/."""
        src = self._skills_dir / skill_name
        if not src.exists():
            return False
        self._invalid_dir.mkdir(parents=True, exist_ok=True)
        dst = self._invalid_dir / skill_name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(src), str(dst))
        # Write reason
        (dst / "errors.txt").write_text(f"auto_cleanup: {reason}", encoding="utf-8")
        logger.info("[LifecycleManager] Archived skill '{}' to .invalid/ ({})", skill_name, reason)
        return True

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    def _get_all_skill_names(self) -> list[str]:
        """Get all skill names from workspace (excluding .invalid)."""
        if not self._skills_dir.exists():
            return []
        return [
            d.name for d in self._skills_dir.iterdir()
            if d.is_dir() and d.name != ".invalid" and (d / "SKILL.md").exists()
        ]

    def _find_skill_path(self, name: str) -> Path | None:
        """Find path to a skill's SKILL.md."""
        path = self._skills_dir / name / "SKILL.md"
        if path.exists():
            return path
        return None

    def _load_skill_content(self, name: str) -> str | None:
        """Load raw skill content."""
        path = self._find_skill_path(name)
        if path:
            return path.read_text(encoding="utf-8")
        return None

    def _compute_similarity(self, content_a: str, content_b: str) -> float:
        """Compute similarity between two skills. Uses vector cosine if embedding available, else Jaccard."""
        if self._embedding:
            # Vector: description + triggers (semantic content)
            text_a = self._extract_semantic_text(content_a)
            text_b = self._extract_semantic_text(content_b)
            vec_a = self._embedding.embed(text_a)
            vec_b = self._embedding.embed(text_b)
            return self._embedding.cosine_similarity(vec_a, vec_b)
        # Fallback: Jaccard on description + triggers + tools (exact matching)
        text_a = self._extract_key_text(content_a)
        text_b = self._extract_key_text(content_b)
        words_a = set(text_a.split())
        words_b = set(text_b.split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_a | words_b)

    @staticmethod
    def _extract_semantic_text(md: str) -> str:
        """Extract semantic text for vector matching: description + triggers."""
        desc_match = re.search(r'^description:\s*["\']?(.*?)["\']?\s*$', md, re.MULTILINE)
        description = desc_match.group(1).strip() if desc_match else ""
        md = re.sub(r'^---\s*\n.*?\n---\s*\n', '', md, flags=re.DOTALL)
        triggers = re.findall(r'["""]([^"""]+)["""]', md)
        parts = [description] + triggers
        return ' '.join(p for p in parts if p).lower()

    @staticmethod
    def _extract_key_text(md: str) -> str:
        """Extract key text for Jaccard matching: description + triggers + tools."""
        desc_match = re.search(r'^description:\s*["\']?(.*?)["\']?\s*$', md, re.MULTILINE)
        description = desc_match.group(1).strip() if desc_match else ""
        md = re.sub(r'^---\s*\n.*?\n---\s*\n', '', md, flags=re.DOTALL)
        triggers = re.findall(r'["""]([^"""]+)["""]', md)
        tools = re.findall(r'`([a-z_][a-z0-9_]*)', md)
        parts = [description] + triggers + tools
        return ' '.join(p for p in parts if p).lower()

    def _find_shared_tools(self, content_a: str, content_b: str) -> list[str]:
        """Find tools referenced in both skills."""
        tools_a = set(re.findall(r'`([a-z_][a-z0-9_]*)', content_a))
        tools_b = set(re.findall(r'`([a-z_][a-z0-9_]*)', content_b))
        return list(tools_a & tools_b)

    def _find_shared_triggers(self, content_a: str, content_b: str) -> list[str]:
        """Find trigger phrases in both skills."""
        triggers_a = set(re.findall(r'["""]([^"""]+)["""]', content_a))
        triggers_b = set(re.findall(r'["""]([^"""]+)["""]', content_b))
        return list(triggers_a & triggers_b)

    def _suggest_merged_name(self, name_a: str, name_b: str) -> str:
        """Suggest a name for merged skill."""
        if name_a.startswith("auto-") and name_b.startswith("auto-"):
            parts_a = name_a.replace("auto-", "").split("-")
            parts_b = name_b.replace("auto-", "").split("-")
            shared = []
            for p in parts_a:
                if p in parts_b:
                    shared.append(p)
            if shared:
                return f"auto-{'+'.join(shared)}"
        return f"merged-{name_a}-{name_b}"
