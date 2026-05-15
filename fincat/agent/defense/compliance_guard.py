"""Compliance Guard — financial compliance speech detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from fincat.providers.base import LLMProvider


class ViolationLevel(Enum):
    """Compliance violation severity level."""
    BLOCK = "block"      # Direct block - response cannot be sent
    REWRITE = "rewrite"  # Trigger rewrite with compliant alternative
    WARN = "warn"        # Warning - log but allow
    LOG = "log"         # Log only - informational


@dataclass
class ComplianceViolation:
    """Represents a detected compliance violation."""
    rule: str
    level: ViolationLevel
    matched_text: str
    suggestion: str | None = None


# --- Layer 1: Offline rules (regex-based, Cost ≈ 0) ---

# BLOCK patterns: absolute promises of returns/safety
# Use negative lookbehind (?<!不) to avoid false positives in negation context
# e.g. "不保本" won't match, "保本" will match
BLOCK_PATTERNS: list[tuple[str, str, str]] = [
    # (pattern, description, suggestion)
    (r"(?<!不)保本", "承诺保本", "请勿承诺本金安全，建议改为'不保证本金'或'可能亏损'"),
    (r"稳赚不赔", "承诺稳赚", "禁止承诺收益，建议说明'投资有风险'"),
    (r"刚性兑付", "刚性兑付表述", "禁止使用刚性兑付表述，建议说明产品风险"),
    (r"零风险|零风险", "承诺零风险", "禁止承诺零风险，建议说明风险等级"),
    (r"保证收益|保证回报|收益保证", "承诺收益", "禁止承诺收益，可改为'历史业绩不代表未来'"),
    (r"(?:一定|肯定|必然)赚钱", "承诺赚钱", "禁止承诺收益"),
    (r"收益兜底", "收益兜底", "禁止承诺收益兜底"),
    (r"内幕消息|内幕信息", "提及内幕", "严格禁止提及内幕信息"),
    (r"坐庄|老鼠仓", "违法操盘表述", "严格禁止使用违法操盘表述"),
    (r"涨停板?打板", "诱导短线操作", "禁止诱导短线操作"),
]

# WARN patterns: suspicious but not necessarily violations
# These are patterns that deserve logging, not blocking
WARN_PATTERNS: list[tuple[str, str]] = [
    (r"基本不会[亏赔]", "暗示低风险"),
    (r"收益很?稳定", "暗示收益确定性"),
    (r"基本保本", "变相承诺保本"),
    (r"闭眼[买入]", "诱导投资"),
    (r"赶紧买|错过没机会", "诱导投资"),
    (r"梭哈|all\s*in", "诱导高风险操作"),
]

# Patterns that are NOT violations (compliant speech) — used for context filtering
COMPLIANT_PATTERNS: list[str] = [
    r"投资有风险",
    r"基金有风险",
    r"股市有风险",
    r"入市需谨慎",
    r"不保[证本]",
    r"可能亏损",
    r"过往业绩不[预代]表?未来",
    r"风险提示",
]


@dataclass
class _CompiledPattern:
    """Compiled regex pattern with metadata."""
    regex: re.Pattern
    description: str
    level: ViolationLevel
    suggestion: str | None = None


class ComplianceGuard:
    """Financial compliance speech detector.

    Three-layer detection:
    - Layer 1: Offline regex rules (this class, Cost ≈ 0)
    - Layer 2: Embedding vector matching (check_with_embedding)
    - Layer 3: LLM semantic detection (check_with_model)

    Usage:
        guard = ComplianceGuard()
        violations = guard.check("这个产品保本没问题")
        # Returns [ComplianceViolation(rule="BLOCK:承诺保本", ...)]
    """

    def __init__(
        self,
        enable_semantic: bool = False,
        provider: "LLMProvider | None" = None,
    ):
        self.enable_semantic = enable_semantic
        self.provider = provider

        # Compile patterns once
        self._block_patterns: list[_CompiledPattern] = []
        for pattern, desc, suggestion in BLOCK_PATTERNS:
            self._block_patterns.append(_CompiledPattern(
                regex=re.compile(pattern),
                description=desc,
                level=ViolationLevel.BLOCK,
                suggestion=suggestion,
            ))

        self._warn_patterns: list[_CompiledPattern] = []
        for pattern, desc in WARN_PATTERNS:
            self._warn_patterns.append(_CompiledPattern(
                regex=re.compile(pattern),
                description=desc,
                level=ViolationLevel.WARN,
            ))

        self._compliant_regexes = [re.compile(p) for p in COMPLIANT_PATTERNS]

        # Embedding layer (lazy init)
        self._embedding_engine = None
        self._violation_vectors: list[tuple[str, list[float]]] | None = None

    def check(self, text: str) -> list[ComplianceViolation]:
        """Layer 1: Check text for compliance violations using regex rules.

        Args:
            text: Text to check

        Returns:
            List of detected violations (may be empty)
        """
        violations: list[ComplianceViolation] = []

        # Check if text contains compliant speech — if so, skip matching
        # that overlaps with compliant patterns
        compliant_spans: set[int] = set()
        for regex in self._compliant_regexes:
            for m in regex.finditer(text):
                compliant_spans.update(range(m.start(), m.end()))

        # Check BLOCK patterns
        for compiled in self._block_patterns:
            for m in compiled.regex.finditer(text):
                # Skip if this match is within a compliant span
                if m.start() in compliant_spans:
                    continue
                violations.append(ComplianceViolation(
                    rule=f"BLOCK:{compiled.description}",
                    level=ViolationLevel.BLOCK,
                    matched_text=m.group(),
                    suggestion=compiled.suggestion,
                ))
                logger.warning(
                    "Compliance BLOCK: '{}' in text ({} chars)",
                    compiled.description,
                    len(text),
                )

        # Check WARN patterns
        for compiled in self._warn_patterns:
            for m in compiled.regex.finditer(text):
                if m.start() in compliant_spans:
                    continue
                violations.append(ComplianceViolation(
                    rule=f"WARN:{compiled.description}",
                    level=ViolationLevel.WARN,
                    matched_text=m.group(),
                ))

        return violations

    def check_with_embedding(
        self,
        text: str,
        threshold: float = 0.95,
    ) -> list[ComplianceViolation]:
        """Layer 2: Check text against violation corpus using embedding similarity.

        Args:
            text: Text to check
            threshold: Cosine similarity threshold (default 0.95)

        Returns:
            List of violations detected by embedding matching
        """
        if self._embedding_engine is None:
            try:
                from fincat.agent.embedding import EmbeddingEngine
                self._embedding_engine = EmbeddingEngine()
            except Exception:
                logger.debug("EmbeddingEngine not available, skipping Layer 2")
                return []

        if self._violation_vectors is None:
            self._violation_vectors = self._load_violation_corpus()

        if not self._violation_vectors:
            return []

        try:
            import numpy as np
            text_vec = np.array(self._embedding_engine.embed(text), dtype=np.float32)
            text_norm = np.linalg.norm(text_vec)
            if text_norm == 0:
                return []

            violations: list[ComplianceViolation] = []
            for label, vec in self._violation_vectors:
                vec_arr = np.array(vec, dtype=np.float32)
                similarity = float(np.dot(text_vec, vec_arr) / (text_norm * np.linalg.norm(vec_arr)))
                if similarity >= threshold:
                    violations.append(ComplianceViolation(
                        rule=f"EMBEDDING:similarity={similarity:.3f}",
                        level=ViolationLevel.BLOCK,
                        matched_text=label,
                        suggestion="语义匹配到违规语料库",
                    ))
                    logger.warning(
                        "Compliance EMBEDDING: '{}' matched '{}' (sim={:.3f})",
                        text[:50], label, similarity,
                    )
                    break  # One match is enough

            return violations
        except Exception as e:
            logger.warning("Embedding compliance check failed: {}", e)
            return []

    async def check_with_model(self, text: str) -> list[ComplianceViolation]:
        """Layer 3: Semantic-level compliance detection using LLM.

        Only activates for financial core actions (investment advice,
        account opening, fund transfer) when Layers 1&2 found nothing.

        Args:
            text: Text to check

        Returns:
            List of violations detected by semantic model
        """
        if not self.enable_semantic or self.provider is None:
            return []

        # Only trigger for financial core actions
        financial_triggers = [
            "投资", "理财", "买入", "卖出", "申购", "赎回",
            "开户", "转账", "汇款", "基金", "股票", "债券",
            "收益", "回报", "保本", "风险",
        ]
        if not any(keyword in text for keyword in financial_triggers):
            return []

        try:
            prompt = f"""你是金融合规审核员。判断以下文本是否违反金融合规规定。

违规类型：
1. 承诺收益/保本（如"稳赚"、"不会亏"、"保本保收益"、"基本不会亏"）
2. 误导性表述（如"基本保本"、"收益很稳定"暗示确定性）
3. 诱导投资（如"赶紧买"、"错过就没机会了"、"闭眼买"）

合规表述（不算违规）：
- "投资有风险"、"基金有风险" → 正确的风险提示
- "不保本"、"可能亏损" → 正确的风险告知
- "过往业绩不代表未来" → 正确的免责

文本：{text}

输出JSON数组，每项包含 violation_type 和 matched_text。无违规输出 []"""

            response = await self.provider.chat(
                model=None,  # Use default model
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )

            if not response or not response.content:
                return []

            # Parse JSON response
            import json
            content = response.content.strip()
            # Extract JSON array from response
            match = re.search(r'\[.*\]', content, re.DOTALL)
            if not match:
                return []

            items = json.loads(match.group())
            violations: list[ComplianceViolation] = []
            for item in items:
                if isinstance(item, dict) and "violation_type" in item:
                    violations.append(ComplianceViolation(
                        rule=f"LLM:{item['violation_type']}",
                        level=ViolationLevel.BLOCK,
                        matched_text=item.get("matched_text", ""),
                        suggestion="LLM 语义检测到违规",
                    ))
            return violations

        except Exception as e:
            logger.warning("LLM compliance check failed: {}", e)
            return []

    def _load_violation_corpus(self) -> list[tuple[str, list[float]]]:
        """Load and embed violation corpus for Layer 2 matching."""
        import json
        from pathlib import Path

        corpus_path = Path(__file__).parent / "compliance_violation_corpus.json"
        if not corpus_path.exists():
            logger.debug("Violation corpus not found at {}", corpus_path)
            return []

        try:
            with open(corpus_path, encoding="utf-8") as f:
                entries = json.load(f)

            if not self._embedding_engine:
                return []

            texts = [e["text"] for e in entries]
            vectors = self._embedding_engine.embed_batch(texts)
            return list(zip(texts, vectors))
        except Exception as e:
            logger.warning("Failed to load violation corpus: {}", e)
            return []

    def add_custom_rule(
        self,
        keyword: str,
        level: ViolationLevel,
        suggestion: str | None = None,
    ) -> None:
        """Add a custom compliance rule."""
        pattern = _CompiledPattern(
            regex=re.compile(re.escape(keyword)),
            description=f"CUSTOM:{keyword}",
            level=level,
            suggestion=suggestion,
        )
        if level == ViolationLevel.BLOCK:
            self._block_patterns.append(pattern)
        elif level == ViolationLevel.WARN:
            self._warn_patterns.append(pattern)
        logger.info("Added custom compliance rule: keyword='{}', level={}", keyword, level.value)

    def get_safe_response(self, original: str, violation: ComplianceViolation) -> str:
        """Generate a safe replacement response for a blocked violation."""
        return (
            "抱歉，我无法提供这样的回复。投资有风险，建议您咨询专业的金融顾问 "
            "或拨打官方客服热线获取准确信息。基金投资需谨慎，请以产品说明书为准。"
        )
