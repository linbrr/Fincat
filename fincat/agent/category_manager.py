"""CategoryManager — directory-based Category management with memory.md generation."""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from loguru import logger

from fincat.agent.category_index import CategoryIndex
from fincat.agent.embedding import EmbeddingEngine

_EMOJI_MAP = {
    "preference": "✨",
    "fact": "📌",
    "knowledge": "📚",
    "event": "📅",
    "goal": "🎯",
    "behavior": "📊",
}

_BUILTIN_FOLDERS = ["profile", "knowledge", "preferences", "behavioral_insights", "compliance"]


class CategoryManager:
    """Directory-based Category management with frontmatter, archive, and memory.md."""

    def __init__(self, memory_dir: Path, embedding: EmbeddingEngine | None = None):
        self._memory_dir = memory_dir
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._embedding = embedding
        self._index = CategoryIndex(memory_dir)
        self._ensure_builtin_folders()
        self._index.scan()
        self._on_item_change: Callable | None = None

    # ------------------------------------------------------------------
    # Item change callback
    # ------------------------------------------------------------------

    def set_on_item_change(self, callback: Callable) -> None:
        """Register a callback for item change events.

        Signature: callback(event_type: str, payload: dict) -> None
        event_type: "item_added", "item_removed", "item_moved", "item_updated"
        """
        self._on_item_change = callback

    def _fire_item_change(self, event_type: str, payload: dict) -> None:
        if self._on_item_change is None:
            return
        try:
            self._on_item_change(event_type, payload)
        except Exception:
            logger.exception(
                "CategoryManager: on_item_change callback failed for {}", event_type,
            )

    # ------------------------------------------------------------------
    # Category lifecycle
    # ------------------------------------------------------------------

    def get_or_create_category(
        self,
        name: str,
        type: str = "custom",
        parent_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        existing = self._index.get_by_name(name)
        if existing:
            return existing["category_id"]

        category_id = f"cate_{uuid.uuid4().hex[:8]}"
        folder = self._resolve_folder(type, name)
        md_path = self._memory_dir / folder / f"{self._sanitize_filename(name)}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc).isoformat()
        metadata = {
            "category_id": category_id,
            "name": name,
            "type": type,
            "created_at": now,
            "updated_at": now,
            "last_accessed_at": now,
            "activity_score": "0.5",
            "is_active": "true",
            "tags": tags or [],
            "parent_id": parent_id or "",
            "auto_generated": "true",
            "_path": str(md_path),
        }
        self._write_category_md(md_path, metadata, f"# {name}\n\n> 摘要：（待生成）\n\n## 记忆条目\n")
        self._index.upsert(category_id, metadata)
        logger.info("CategoryManager: created category {} ({})", name, category_id)
        return category_id

    def archive_category(self, category_id: str) -> None:
        meta = self._index.get(category_id)
        if not meta:
            return
        now = datetime.now(timezone.utc)
        archive_dir = self._memory_dir / "archive" / str(now.year) / f"{now.month:02d}"
        archive_dir.mkdir(parents=True, exist_ok=True)

        old_path = Path(meta["_path"])
        if old_path.exists():
            new_path = archive_dir / old_path.name
            shutil.move(str(old_path), str(new_path))
            meta["_path"] = str(new_path)

        meta["is_active"] = "false"
        meta["updated_at"] = now.isoformat()
        self._index.upsert(category_id, meta)
        logger.info("CategoryManager: archived category {}", category_id)
        self.regenerate_memory_md()

    def restore_category(self, category_id: str) -> None:
        meta = self._index.get(category_id)
        if not meta:
            return
        # Move back to original folder
        old_path = Path(meta["_path"])
        name = meta.get("name", "unknown")
        folder = self._resolve_folder(meta.get("type", "custom"), name)
        new_path = self._memory_dir / folder / old_path.name
        new_path.parent.mkdir(parents=True, exist_ok=True)
        if old_path.exists():
            shutil.move(str(old_path), str(new_path))
            meta["_path"] = str(new_path)

        meta["is_active"] = "true"
        meta["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._index.upsert(category_id, meta)
        logger.info("CategoryManager: restored category {}", category_id)
        self.regenerate_memory_md()

    # ------------------------------------------------------------------
    # Item management
    # ------------------------------------------------------------------

    def add_item_to_category(self, category_id: str, item_id: str, item_data: dict) -> None:
        meta = self._index.get(category_id)
        if not meta:
            return
        md_path = Path(meta["_path"])
        if not md_path.exists():
            return

        memory_type = item_data.get("memory_type", "fact")
        emoji = _EMOJI_MAP.get(memory_type, "📌")
        summary = item_data.get("summary", "")
        date_str = datetime.now().strftime("%Y-%m-%d")
        entry = f"- [{date_str}] {emoji}【{memory_type}】{summary} | item_id: {item_id}\n"

        content = md_path.read_text(encoding="utf-8")
        # Append before end or at end
        if "## 记忆条目" in content:
            content = content.rstrip() + "\n" + entry
        else:
            content += f"\n## 记忆条目\n\n{entry}"
        md_path.write_text(content, encoding="utf-8")

        self._update_activity_score(category_id)
        self.regenerate_memory_md()
        logger.debug("CategoryManager: added item {} to category {}", item_id, category_id)
        self._fire_item_change("item_added", {
            "item_id": item_id, "category_id": category_id, "item_data": item_data,
        })

    def _remove_item_line(self, category_id: str, item_id: str) -> bool:
        """Remove an item line from .md file without firing events."""
        meta = self._index.get(category_id)
        if not meta:
            return False
        md_path = Path(meta["_path"])
        if not md_path.exists():
            return False
        content = md_path.read_text(encoding="utf-8")
        pattern = re.compile(
            rf"^- \[.*?\] .*?\| item_id: {re.escape(item_id)}\s*$", re.MULTILINE,
        )
        new_content = pattern.sub("", content)
        if new_content == content:
            return False
        md_path.write_text(new_content, encoding="utf-8")
        return True

    def remove_item_from_category(self, category_id: str, item_id: str) -> None:
        if not self._remove_item_line(category_id, item_id):
            return
        self.regenerate_memory_md()
        self._fire_item_change("item_removed", {
            "item_id": item_id, "category_id": category_id,
        })

    def move_item(
        self,
        item_id: str,
        from_category_id: str,
        to_category_id: str,
        item_data: dict,
    ) -> None:
        """Move an item between categories (content unchanged)."""
        self._remove_item_line(from_category_id, item_id)

        meta_to = self._index.get(to_category_id)
        if meta_to:
            md_path = Path(meta_to["_path"])
            if md_path.exists():
                memory_type = item_data.get("memory_type", "fact")
                emoji = _EMOJI_MAP.get(memory_type, "📌")
                summary = item_data.get("summary", "")
                date_str = datetime.now().strftime("%Y-%m-%d")
                entry = f"- [{date_str}] {emoji}【{memory_type}】{summary} | item_id: {item_id}\n"
                raw = md_path.read_text(encoding="utf-8")
                if "## 记忆条目" in raw:
                    raw = raw.rstrip() + "\n" + entry
                else:
                    raw += f"\n## 记忆条目\n\n{entry}"
                md_path.write_text(raw, encoding="utf-8")
                self._update_activity_score(to_category_id)

        self.regenerate_memory_md()
        self._fire_item_change("item_moved", {
            "item_id": item_id,
            "from_category_id": from_category_id,
            "to_category_id": to_category_id,
            "item_data": item_data,
        })

    def update_item_content(
        self,
        category_id: str,
        item_id: str,
        new_summary: str,
        item_data: dict,
    ) -> None:
        """Update an item's summary text in the category .md file."""
        meta = self._index.get(category_id)
        if not meta:
            return
        md_path = Path(meta["_path"])
        if not md_path.exists():
            return

        content = md_path.read_text(encoding="utf-8")
        memory_type = item_data.get("memory_type", "fact")
        emoji = _EMOJI_MAP.get(memory_type, "📌")
        date_str = datetime.now().strftime("%Y-%m-%d")
        new_entry = f"- [{date_str}] {emoji}【{memory_type}】{new_summary} | item_id: {item_id}"
        pattern = re.compile(
            rf"^- \[.*?\] .*?\| item_id: {re.escape(item_id)}\s*$", re.MULTILINE,
        )
        content = pattern.sub(new_entry, content)
        md_path.write_text(content, encoding="utf-8")

        self._update_activity_score(category_id)
        self.regenerate_memory_md()
        self._fire_item_change("item_updated", {
            "item_id": item_id,
            "category_id": category_id,
            "new_summary": new_summary,
            "item_data": item_data,
        })

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read_category_md(self, category_id: str) -> str:
        meta = self._index.get(category_id)
        if not meta:
            return ""
        md_path = Path(meta["_path"])
        if not md_path.exists():
            return ""
        return md_path.read_text(encoding="utf-8")

    def read_memory_md(self) -> str:
        path = self._memory_dir / "memory.md"
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def find_best_category(self, summary: str, memory_type: str) -> str | None:
        """Vector match: summary embedding vs category summary embeddings."""
        if not self._embedding:
            return self._fallback_category(memory_type)

        active = self._index.list_active()
        if not active:
            return None

        summary_vec = self._embedding.embed(summary)
        best_id = None
        best_score = 0.0
        for meta in active:
            cat_summary = meta.get("summary", meta.get("name", ""))
            if not cat_summary:
                continue
            cat_vec = self._embedding.embed(cat_summary)
            score = self._embedding.cosine_similarity(summary_vec, cat_vec)
            if score > best_score:
                best_score = score
                best_id = meta["category_id"]

        if best_score > 0.5:
            return best_id
        return self._fallback_category(memory_type)

    # ------------------------------------------------------------------
    # memory.md maintenance
    # ------------------------------------------------------------------

    def regenerate_memory_md(self) -> None:
        sections: list[str] = []
        active = self._index.list_active()

        # Group by type
        by_type: dict[str, list[dict]] = {}
        for meta in active:
            t = meta.get("type", "custom")
            by_type.setdefault(t, []).append(meta)

        section_map = {
            "profile": "画像",
            "knowledge": "知识",
            "preference": "偏好",
            "behavioral_insights": "行为洞察",
            "compliance": "合规",
        }

        for type_key, title in section_map.items():
            cats = by_type.get(type_key, [])
            if not cats:
                continue
            lines = [f"## {title}"]
            for cat in cats:
                summary = self._extract_summary(cat)
                if summary:
                    lines.append(f"- {summary}")
            if len(lines) > 1:
                sections.append("\n".join(lines))

        # Custom categories
        custom = by_type.get("custom", [])
        if custom:
            lines = ["## 近期事件"]
            for cat in custom:
                summary = self._extract_summary(cat)
                if summary:
                    lines.append(f"- {summary}")
            if len(lines) > 1:
                sections.append("\n".join(lines))

        content = "# 用户记忆摘要\n\n" + "\n\n".join(sections) + "\n" if sections else ""
        path = self._memory_dir / "memory.md"
        path.write_text(content, encoding="utf-8")
        logger.debug("CategoryManager: regenerated memory.md ({} sections)", len(sections))

    # ------------------------------------------------------------------
    # Auto management
    # ------------------------------------------------------------------

    def check_archive_candidates(self) -> list[str]:
        candidates = []
        for meta in self._index.list_active():
            score = float(meta.get("activity_score", 0.5))
            if score < 0.2:
                candidates.append(meta["category_id"])
        return candidates

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_builtin_folders(self) -> None:
        """Create builtin category folders and seed .md files if missing."""
        # Builtin category definitions: (folder, display_name, type, summary)
        _BUILTIN_CATEGORIES = [
            ("profile", "用户画像", "profile", "用户基本信息、身份特征"),
            ("knowledge", "产品知识", "knowledge", "产品、市场、交易相关知识"),
            ("preferences", "用户偏好", "preference", "用户偏好、习惯、喜好"),
            ("behavioral_insights", "行为洞察", "behavioral_insights", "用户行为模式、交易节奏分析"),
            ("compliance", "合规规则", "compliance", "合规要求、风控规则"),
        ]

        for folder, name, cat_type, summary in _BUILTIN_CATEGORIES:
            folder_path = self._memory_dir / folder
            folder_path.mkdir(parents=True, exist_ok=True)

            # Check if any .md with frontmatter already exists in this folder
            has_category = any(
                self._index.get(m["category_id"])
                for m in self._index.list_by_type(cat_type)
            ) if self._index.list_all() else False

            # Also check by scanning the folder directly
            if not has_category:
                for md_file in folder_path.glob("*.md"):
                    meta = self._index._parse_frontmatter(md_file)
                    if meta and "category_id" in meta:
                        has_category = True
                        break

            if not has_category:
                # Seed a default category .md with frontmatter
                self.get_or_create_category(name, type=cat_type)

        # Ensure custom and archive directories exist
        (self._memory_dir / "custom").mkdir(parents=True, exist_ok=True)
        (self._memory_dir / "archive").mkdir(parents=True, exist_ok=True)

    def _fallback_category(self, memory_type: str) -> str | None:
        """Type-based fallback: find a category whose type matches memory_type."""
        type_to_folder = {
            "preference": "preferences",
            "fact": "profile",
            "knowledge": "knowledge",
            "event": "custom",
            "goal": "custom",
            "behavior": "behavioral_insights",
        }
        folder = type_to_folder.get(memory_type, "custom")
        for meta in self._index.list_active():
            if meta.get("type") == folder or meta.get("type") == memory_type:
                return meta["category_id"]
        # Return first active category in the matching folder
        for meta in self._index.list_active():
            path = Path(meta.get("_path", ""))
            if path.parent.name == folder:
                return meta["category_id"]
        return None

    def _resolve_folder(self, type: str, name: str) -> str:
        if type in _BUILTIN_FOLDERS:
            return type
        if type == "system":
            return "profile"
        return "custom"

    def _update_activity_score(self, category_id: str) -> None:
        meta = self._index.get(category_id)
        if not meta:
            return
        now = datetime.now(timezone.utc).isoformat()
        meta["last_accessed_at"] = now
        meta["updated_at"] = now
        # Simple scoring: bump score on access
        score = min(1.0, float(meta.get("activity_score", 0.5)) + 0.05)
        meta["activity_score"] = str(score)
        self._index.upsert(category_id, meta)

    def _extract_summary(self, meta: dict) -> str:
        """Extract summary from category md (first blockquote line)."""
        md_path = Path(meta.get("_path", ""))
        if not md_path.exists():
            return meta.get("name", "")
        try:
            content = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return meta.get("name", "")
        for line in content.splitlines():
            if line.startswith("> 摘要：") or line.startswith("> Summary:"):
                return line.lstrip("> ").replace("摘要：", "").strip()
        return meta.get("name", "")

    def _write_category_md(self, path: Path, metadata: dict, body: str) -> None:
        lines = ["---"]
        for key, val in metadata.items():
            if key.startswith("_"):
                continue
            if isinstance(val, list):
                val_str = "[" + ", ".join(f'"{v}"' for v in val) + "]"
            elif isinstance(val, bool):
                val_str = "true" if val else "false"
            else:
                val_str = str(val)
            lines.append(f"{key}: {val_str}")
        lines.append("---")
        lines.append("")
        lines.append(body)
        path.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        # Remove or replace characters not allowed in filenames
        return re.sub(r'[<>:"/\\|?*]', "_", name).strip()[:64]
