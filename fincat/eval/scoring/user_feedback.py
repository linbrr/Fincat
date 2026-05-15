"""用户反馈收集 — 前端 thumbs up/down 评分。"""

from __future__ import annotations

from typing import Any

from loguru import logger


class FeedbackCollector:
    """收集用户反馈并上报到 Langfuse。

    工作流：
    1. Agent 响应后，在 metadata 中嵌入 feedback_token
    2. 前端显示 thumbs up/down 按钮
    3. 用户点击后，前端调用 API 发送反馈
    4. FeedbackCollector 将评分上报到 Langfuse trace
    """

    def __init__(self) -> None:
        self._langfuse: Any = None
        self._pending: dict[str, str] = {}  # feedback_token -> trace_id

    def _ensure_client(self) -> Any:
        if self._langfuse is None:
            try:
                from fincat.eval.langfuse_hook import _get_langfuse_client
                self._langfuse = _get_langfuse_client()
            except Exception:
                pass
        return self._langfuse

    def register_trace(self, trace_id: str, feedback_token: str) -> None:
        """注册一个待反馈的 trace。"""
        self._pending[feedback_token] = trace_id

    def submit_feedback(
        self,
        feedback_token: str,
        score: float,
        comment: str | None = None,
    ) -> bool:
        """提交用户反馈到 Langfuse。

        Args:
            feedback_token: 反馈令牌
            score: 1.0 = thumbs up, 0.0 = thumbs down
            comment: 可选评论
        """
        client = self._ensure_client()
        trace_id = self._pending.pop(feedback_token, None)
        if not trace_id or not client:
            return False
        try:
            client.score(
                trace_id=trace_id,
                name="user_feedback",
                value=score,
                comment=comment,
            )
            logger.info("用户反馈已上报: trace={}, score={}", trace_id, score)
            return True
        except Exception as e:
            logger.error("反馈上报失败: {}", e)
            return False

    @property
    def pending_count(self) -> int:
        return len(self._pending)
