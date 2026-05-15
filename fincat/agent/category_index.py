"""CategoryIndex — in-memory index of Category metadata from YAML frontmatter."""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---", re.DOTALL)
_FIELD_RE = re.compile(r"^(\w[\w_]*):\s*(.+)$", re.MULTILINE)


class CategoryIndex:
    """Category metadata index parsed from YAML frontmatter in .md files."""

    def __init__(self, memory_dir: Path):
        self._memory_dir = memory_dir
        self._index: dict[str, dict] = {}  # category_id → metadata

    def scan(self) -> None:
        """Walk memory_dir, parse all .md files with frontmatter, rebuild index."""
        self._index.clear()
        if not self._memory_dir.exists():
            return
        for md_path in self._memory_dir.rglob("*.md"):
            if md_path.name == "memory.md":
                continue
            meta = self._parse_frontmatter(md_path)
            if meta and "category_id" in meta:
                meta["_path"] = str(md_path)
                self._index[meta["category_id"]] = meta
        logger.debug("CategoryIndex: scanned {} categories", len(self._index))

    def get(self, category_id: str) -> dict | None:
        return self._index.get(category_id)

    def get_by_name(self, name: str) -> dict | None:
        for meta in self._index.values():
            if meta.get("name") == name:
                return meta
        return None

    def get_by_path(self, path: str) -> dict | None:
        for meta in self._index.values():
            if meta.get("_path") == path:
                return meta
        return None

    def list_all(self) -> list[dict]:
        return list(self._index.values())

    def list_by_type(self, type: str) -> list[dict]:
        return [m for m in self._index.values() if m.get("type") == type]

    def list_active(self) -> list[dict]:
        def _is_active(meta: dict) -> bool:
            val = meta.get("is_active", True)
            if isinstance(val, bool):
                return val
            return str(val).lower() not in ("false", "0", "no")
        return [m for m in self._index.values() if _is_active(m)]

    def upsert(self, category_id: str, metadata: dict) -> None:
        self._index[category_id] = metadata

    def remove(self, category_id: str) -> None:
        self._index.pop(category_id, None)

    def find_by_tag(self, tag: str) -> list[dict]:
        return [m for m in self._index.values() if tag in m.get("tags", [])]

    def _parse_frontmatter(self, md_path: Path) -> dict | None:
        try:
            content = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        match = _FRONTMATTER_RE.match(content)
        if not match:
            return None
        raw = match.group(1)
        meta: dict = {}
        for m in _FIELD_RE.finditer(raw):
            key, val = m.group(1), m.group(2).strip()
            if val.startswith("[") and val.endswith("]"):
                # Simple list parse: ["a", "b"]
                val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]
            elif val.lower() in ("true", "false"):
                val = val.lower() == "true"
            meta[key] = val
        return meta
