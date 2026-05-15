"""PII Scanner — financial personal information detection and masking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from loguru import logger


@dataclass
class PIIMatch:
    """Represents a detected PII match."""
    pii_type: Literal["id_card", "bank_card", "phone", "email"]
    original: str
    placeholder: str
    start: int
    end: int


class PIIScanner:
    """Financial PII scanner with session-level masking.

    Scans and masks personal identifiable information before LLM processing,
    with session-level mapping for later restoration.
    """

    # Patterns: (regex_pattern, placeholder_template)
    PATTERNS = {
        "id_card": (
            r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
            "[ID_{idx}]",
        ),
        "bank_card": (
            r"(?<![0-9])(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|6(?:011|5[0-9]{2})[0-9]{12}|62[0-9]{14,17})(?![0-9])",
            "[CARD_{idx}]",
        ),
        "phone": (
            r"(?:86)?1[3-9]\d{9}",
            "[PHONE_{idx}]",
        ),
        "email": (
            r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
            "[EMAIL_{idx}]",
        ),
    }

    def __init__(self):
        # session_key -> {placeholder -> original_value}
        self._mappings: dict[str, dict[str, str]] = {}

    def scan_and_mask(self, text: str, session_key: str) -> tuple[str, list[PIIMatch]]:
        """Scan text for PII and replace with placeholders.

        Args:
            text: Input text to scan
            session_key: Session identifier for mapping isolation

        Returns:
            Tuple of (masked_text, list_of_matches)
        """
        if session_key not in self._mappings:
            self._mappings[session_key] = {}

        matches: list[PIIMatch] = []
        masked = text
        offset = 0

        # Track replacements for this scan to avoid double-counting
        replacements_done: set[tuple[int, int]] = set()

        for pii_type, (pattern, placeholder) in self.PATTERNS.items():
            # Count existing placeholders of this type for this session
            idx_counter = sum(
                1 for k in self._mappings[session_key]
                if k.startswith(placeholder.rsplit("_", 1)[0] + "_")
            )

            for m in re.finditer(pattern, text):
                # Skip if this position was already replaced
                if (m.start(), m.end()) in replacements_done:
                    continue

                idx_counter += 1
                placeholder_text = placeholder.format(idx=idx_counter)

                # Store mapping
                self._mappings[session_key][placeholder_text] = m.group()

                # Record match
                matches.append(PIIMatch(
                    pii_type=pii_type,
                    original=m.group(),
                    placeholder=placeholder_text,
                    start=m.start(),
                    end=m.end(),
                ))

                # Apply replacement with offset tracking
                old_start = m.start() + offset
                old_end = m.end() + offset
                masked = masked[:old_start] + placeholder_text + masked[old_end:]
                offset += len(placeholder_text) - (m.end() - m.start())
                replacements_done.add((m.start(), m.end()))

        if matches:
            logger.debug(
                "PIIScanner: masked {} PII items for session {}",
                len(matches),
                session_key,
            )

        return masked, matches

    def restore(self, text: str, session_key: str) -> str:
        """Restore placeholders back to original values.

        Args:
            text: Text with placeholders
            session_key: Session identifier

        Returns:
            Text with original values restored
        """
        if session_key not in self._mappings:
            return text

        restored = text
        for placeholder, original in self._mappings[session_key].items():
            restored = restored.replace(placeholder, original)

        return restored

    def clear_session(self, session_key: str) -> None:
        """Clear mappings for a session (call when session ends).

        Args:
            session_key: Session identifier
        """
        if session_key in self._mappings:
            del self._mappings[session_key]
            logger.debug("PIIScanner: cleared mappings for session {}", session_key)

    def get_masked_count(self, session_key: str) -> int:
        """Get number of masked items for a session.

        Args:
            session_key: Session identifier

        Returns:
            Number of masked PII items
        """
        return len(self._mappings.get(session_key, {}))
