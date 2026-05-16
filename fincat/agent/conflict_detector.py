"""Conflict detector — 语义矛盾检测 + 优先级仲裁。

当新记忆与已有记忆存在语义矛盾时，根据优先级决定覆盖或保留双版本。

优先级规则（从高到低）：
1. 时间优先：最新记忆 > 旧记忆
2. 来源可靠性：用户明确表述 > 系统推导 > 第三方
3. 置信度：高(0.8+) > 中(0.5-0.8) > 低(<0.5)
4. 使用频率：高频(≥3) > 低频
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol

from loguru import logger


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class ConflictAction(str, Enum):
    """冲突解决动作"""
    MERGE = "merge"           # 不矛盾，合并（原有逻辑）
    OVERWRITE = "overwrite"   # 矛盾，高优先级覆盖低优先级
    KEEP_BOTH = "keep_both"   # 矛盾，优先级相近，保留双版本


@dataclass
class ConflictResult:
    """冲突检测结果"""
    is_conflict: bool
    reason: str
    action: ConflictAction = ConflictAction.MERGE
    winner_id: str | None = None
    loser_id: str | None = None
    priority_diff: float = 0.0  # 优先级差值


# ---------------------------------------------------------------------------
# Protocol for LLM client
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """LLM 客户端协议（兼容 OpenAI / Anthropic / 自定义）"""
    async def generate(self, prompt: str, **kwargs) -> str: ...


# ---------------------------------------------------------------------------
# Conflict Detector
# ---------------------------------------------------------------------------


# 来源可靠性分数
SOURCE_SCORES: dict[str, float] = {
    "user": 1.0,           # 用户明确表述
    "system": 0.6,         # 系统自动推导
    "third_party": 0.3,    # 第三方来源
}

# 优先级相近阈值
PRIORITY_CLOSE_THRESHOLD = 0.15


class ConflictDetector:
    """检测并解决记忆冲突"""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        close_threshold: float = PRIORITY_CLOSE_THRESHOLD,
    ):
        self._llm = llm_client
        self._close_threshold = close_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def detect_and_resolve(
        self,
        new_content: str,
        new_timestamp: datetime,
        new_source_type: str = "user",
        new_confidence: float = 0.5,
        new_frequency: int = 1,
        *,
        existing_content: str,
        existing_id: str,
        existing_timestamp: datetime,
        existing_source_type: str = "user",
        existing_confidence: float = 0.5,
        existing_frequency: int = 1,
    ) -> ConflictResult:
        """检测冲突并决定解决策略。

        Args:
            new_*: 新记忆的属性
            existing_*: 已有记忆的属性

        Returns:
            ConflictResult 包含是否冲突、解决动作、胜者 ID
        """
        # Step 1: LLM 判断是否矛盾
        is_conflict = await self._llm_judge_conflict(new_content, existing_content)

        if not is_conflict:
            logger.debug("记忆无冲突: 语义一致")
            return ConflictResult(is_conflict=False, reason="语义一致")

        logger.info("检测到记忆冲突: new vs existing={}", existing_id)

        # Step 2: 计算优先级
        new_priority = self._calc_priority(
            timestamp=new_timestamp,
            source_type=new_source_type,
            confidence=new_confidence,
            frequency=new_frequency,
        )
        old_priority = self._calc_priority(
            timestamp=existing_timestamp,
            source_type=existing_source_type,
            confidence=existing_confidence,
            frequency=existing_frequency,
        )

        priority_diff = abs(new_priority - old_priority)
        logger.debug(
            "优先级计算: new={:.3f} old={:.3f} diff={:.3f}",
            new_priority, old_priority, priority_diff,
        )

        # Step 3: 判断是否"优先级相近"
        if priority_diff < self._close_threshold:
            logger.info("优先级相近 (diff={:.3f})，保留双版本", priority_diff)
            return ConflictResult(
                is_conflict=True,
                reason=f"优先级相近 (差值={priority_diff:.3f})，保留双版本",
                action=ConflictAction.KEEP_BOTH,
                priority_diff=priority_diff,
            )

        # Step 4: 高优先级覆盖低优先级
        if new_priority >= old_priority:
            logger.info("新记忆优先级更高，覆盖旧记忆 {}", existing_id)
            return ConflictResult(
                is_conflict=True,
                reason=f"新记忆优先级更高 (new={new_priority:.3f} > old={old_priority:.3f})",
                action=ConflictAction.OVERWRITE,
                winner_id="new",
                loser_id=existing_id,
                priority_diff=priority_diff,
            )
        else:
            logger.info("旧记忆优先级更高，保留旧记忆 {}", existing_id)
            return ConflictResult(
                is_conflict=True,
                reason=f"旧记忆优先级更高 (old={old_priority:.3f} > new={new_priority:.3f})",
                action=ConflictAction.OVERWRITE,
                winner_id=existing_id,
                loser_id="new",
                priority_diff=priority_diff,
            )

    async def scan_existing_conflicts(
        self,
        items: list[Any],  # list[MemoryItem]
        search_fn: Any,  # MemoryStoreV2.search_similar_batch
        *,
        max_pairs: int = 20,
        similarity_range: tuple[float, float] = (0.85, 0.95),
    ) -> list[dict]:
        """扫描已有记忆间的潜在冲突。

        对每条近期记忆，用向量搜索找相似但低于插入阈值的对，
        调用 detect_and_resolve() 判断是否矛盾。

        Args:
            items: 近期 MemoryItem 列表
            search_fn: MemoryStoreV2.search_similar_batch 方法
            max_pairs: 最多检查的对数（控制 LLM 调用成本）
            similarity_range: 向量相似度范围 (下限, 上限)

        Returns:
            解决的冲突列表，每项包含 item_id, matched_id, action
        """
        resolved: list[dict] = []
        checked = 0
        searched = 0
        seen_pairs: set[tuple[str, str]] = set()

        for item in items:
            if checked >= max_pairs or searched >= max_pairs:
                break
            searched += 1
            try:
                similar = search_fn(
                    summary=item.content[:200],
                    threshold=similarity_range[0],
                    limit=5,
                )
            except Exception:
                logger.debug("scan_existing_conflicts: search failed for {}", item.item_id)
                continue

            for match in similar:
                if checked >= max_pairs:
                    break
                score = match.get("_score", 0)
                match_id = match.get("item_id", "")
                if score >= similarity_range[1]:
                    continue  # 插入时已检查过
                if match_id == item.item_id:
                    continue
                # 去重：A-B 和 B-A 只检查一次
                pair = tuple(sorted([item.item_id, match_id]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                # 解析匹配项的时间戳
                match_ts_str = match.get("updated_at") or match.get("created_at") or ""
                try:
                    if match_ts_str:
                        match_ts = datetime.fromisoformat(match_ts_str.replace("Z", "+00:00"))
                    else:
                        match_ts = datetime.now(timezone.utc)
                except (ValueError, TypeError):
                    match_ts = datetime.now(timezone.utc)

                item_ts = item.last_accessed or item.timestamp
                try:
                    result = await self.detect_and_resolve(
                        new_content=item.content,
                        new_timestamp=item_ts if item_ts.tzinfo else item_ts.replace(tzinfo=timezone.utc),
                        new_source_type=item.source_type,
                        new_confidence=item.confidence,
                        new_frequency=item.frequency,
                        existing_content=match.get("content", ""),
                        existing_id=match_id,
                        existing_timestamp=match_ts,
                        existing_source_type=match.get("source_type", "system"),
                        existing_confidence=float(match.get("confidence", 0.5)),
                        existing_frequency=int(match.get("frequency", 1)),
                    )
                except Exception:
                    logger.debug("scan_existing_conflicts: detect_and_resolve failed for {} vs {}", item.item_id, match_id)
                    checked += 1
                    continue

                if result.is_conflict:
                    resolved.append({
                        "item_id": item.item_id,
                        "matched_id": match_id,
                        "action": result.action.value,
                        "reason": result.reason,
                        "score": round(score, 3),
                    })
                    logger.info(
                        "scan_existing_conflicts: conflict found {} vs {} action={}",
                        item.item_id, match_id, result.action.value,
                    )
                checked += 1

        if resolved:
            logger.info("scan_existing_conflicts: resolved {} conflict(s) in {} checks", len(resolved), checked)
        return resolved

    # ------------------------------------------------------------------
    # Priority calculation
    # ------------------------------------------------------------------

    def _calc_priority(
        self,
        *,
        timestamp: datetime,
        source_type: str,
        confidence: float,
        frequency: int,
    ) -> float:
        """计算记忆优先级分数。

        权重分配：
        - 时间优先: 0.4
        - 来源可靠性: 0.3
        - 置信度: 0.2
        - 使用频率: 0.1
        """
        score = 0.0

        # 1. 时间优先 (0.4) — 使用相对时间戳
        # 将时间戳归一化到 [0, 1] 范围（以 2020-01-01 为基准，10 年为跨度）
        base_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        span_days = 365 * 10  # 10 年
        ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        days_since_base = (ts - base_time).total_seconds() / 86400
        time_score = min(max(days_since_base / span_days, 0), 1.0)
        score += time_score * 0.4

        # 2. 来源可靠性 (0.3)
        source_score = SOURCE_SCORES.get(source_type, 0.5)
        score += source_score * 0.3

        # 3. 置信度 (0.2) — 已经是 [0, 1] 范围
        score += min(max(confidence, 0), 1.0) * 0.2

        # 4. 使用频率 (0.1) — 归一化到 [0, 1]，10 次为满分
        freq_score = min(frequency / 10, 1.0)
        score += freq_score * 0.1

        return score

    # ------------------------------------------------------------------
    # LLM conflict judgment
    # ------------------------------------------------------------------

    async def _llm_judge_conflict(self, text_a: str, text_b: str) -> bool:
        """LLM 判断两条记忆是否矛盾。

        矛盾定义：两条记忆在语义上互斥（一个说 A，另一个说非 A)。
        非矛盾：两条记忆是补充关系、重复关系、或无关关系。
        """
        prompt = f"""判断以下两条记忆是否存在语义矛盾。

矛盾定义：两条记忆在核心事实上互斥。例如：
- "持仓1000股" vs "持仓2000股" → 矛盾
- "喜欢Python" vs "不喜欢Python" → 矛盾
- "住在北京" vs "住在上海" → 矛盾

非矛盾示例：
- "喜欢Python" vs "喜欢编程" → 补充关系
- "喜欢Python" vs "喜欢Python" → 重复
- "喜欢Python" vs "今天天气好" → 无关

记忆1: {text_a}
记忆2: {text_b}

只回答 JSON，不要其他内容: {{"conflict": true 或 false}}"""

        try:
            response = await self._llm.generate(prompt)
            # 解析 JSON 响应
            response = response.strip()
            # 提取 JSON 部分（处理 LLM 可能添加的额外文本)
            if "{" in response:
                json_str = response[response.index("{"):response.rindex("}") + 1]
                result = json.loads(json_str)
                return bool(result.get("conflict", False))
            return False
        except Exception as e:
            logger.warning("LLM 冲突判断失败，默认无冲突: {}", e)
            return False
