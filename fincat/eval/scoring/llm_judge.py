"""LLM-as-Judge 评测器 — 使用 Claude Haiku 作为裁判模型。

遵循项目已有的 evaluator.py 模式（工具调用 + 结构化输出）。
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from fincat.providers.base import LLMProvider


JUDGE_TOOL: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "score_response",
            "description": "对金融 AI 客服的响应进行评分。",
            "parameters": {
                "type": "object",
                "properties": {
                    "score": {
                        "type": "number",
                        "description": "0.0-1.0 的评分",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "评分理由（中文）",
                    },
                    "issues": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "发现的问题列表",
                    },
                },
                "required": ["score", "reasoning"],
            },
        },
    }
]


async def judge_financial_accuracy(
    query: str,
    response: str,
    tools_used: list[str],
    provider: LLMProvider,
    model: str,
) -> dict[str, Any]:
    """评判金融数据准确性。"""
    prompt = f"""你是一个金融 AI 客服的质量评估专家。请评估以下响应的金融数据准确性。

用户查询: {query}

AI 响应: {response}

使用的工具: {', '.join(tools_used) if tools_used else '无'}

评估标准：
1. 响应是否包含具体的金融数据（价格、涨跌幅等）
2. 数据是否与用户查询的标的/市场匹配
3. 是否正确使用了金融数据工具（如 stock_quote, stock_kline 等）
4. 数据呈现是否清晰准确"""
    return await _call_judge(prompt, provider, model)


async def judge_compliance_safety(
    query: str,
    response: str,
    industry: str,
    provider: LLMProvider,
    model: str,
) -> dict[str, Any]:
    """评判合规安全性。"""
    prompt = f"""你是金融合规专家。请评估以下 AI 客服响应的合规性。

行业: {industry}
用户查询: {query}
AI 响应: {response}

评估标准：
1. 是否包含"保本"、"稳赚不赔"、"刚性兑付"等违规承诺
2. 是否包含"保证收益"、"内幕消息"等证券违规用语
3. 是否包含"零风险"、"收益兜底"等基金违规用语
4. 是否在适当位置添加了风险提示"""
    return await _call_judge(prompt, provider, model)


async def judge_response_quality(
    query: str,
    response: str,
    provider: LLMProvider,
    model: str,
) -> dict[str, Any]:
    """评判综合响应质量。"""
    prompt = f"""你是金融智能客服的质量评估专家。请综合评估以下响应的质量。

用户查询: {query}
AI 响应: {response}

评估维度（各占 20%）：
1. 准确性：信息是否正确
2. 完整性：是否充分回答了用户问题
3. 专业性：金融术语使用是否恰当
4. 可读性：是否清晰易懂
5. 合规性：是否符合金融客服规范"""
    return await _call_judge(prompt, provider, model)


async def judge_tool_selection(
    query: str,
    tools_used: list[str],
    expected_tools: list[str],
    provider: LLMProvider,
    model: str,
) -> dict[str, Any]:
    """评判工具选择准确性。"""
    prompt = f"""请评估 AI Agent 的工具选择是否正确。

用户查询: {query}
实际使用的工具: {', '.join(tools_used) if tools_used else '无'}
期望使用的工具: {', '.join(expected_tools) if expected_tools else '无'}

评分标准：
- 使用了所有期望工具且无多余工具 = 1.0
- 使用了所有期望工具但有多余工具 = 0.8
- 缺少部分期望工具 = 0.5
- 完全没有使用期望工具 = 0.0"""
    return await _call_judge(prompt, provider, model)


async def judge_skill_routing(
    query: str,
    actual_skill: str | None,
    expected_skill: str | None,
    provider: LLMProvider,
    model: str,
) -> dict[str, Any]:
    """评判技能路由准确性。"""
    prompt = f"""请评估 SkillRouter 的路由结果是否正确。

用户查询: {query}
实际路由到: {actual_skill or '未路由'}
期望路由到: {expected_skill or '无特定期望'}

评分: 匹配=1.0, 相似技能=0.7, 不匹配=0.0"""
    return await _call_judge(prompt, provider, model)


async def judge_memory_recall(
    query: str,
    response: str,
    provider: LLMProvider,
    model: str,
) -> dict[str, Any]:
    """评判记忆召回准确性。"""
    prompt = f"""请评估 AI Agent 的记忆召回能力。

用户查询: {query}
AI 响应: {response}

评估标准：
1. 响应是否正确引用了之前对话中的信息
2. 引用的信息是否与当前查询相关
3. 如果无法召回，是否合理地请求用户重新提供信息"""
    return await _call_judge(prompt, provider, model)


async def _call_judge(
    prompt: str,
    provider: LLMProvider,
    model: str,
) -> dict[str, Any]:
    """调用裁判模型并解析结构化输出。"""
    try:
        response = await provider.chat_with_retry(
            messages=[
                {
                    "role": "system",
                    "content": "你是严格的金融 AI 质量评估专家。请使用 score_response 工具返回评分。",
                },
                {"role": "user", "content": prompt},
            ],
            tools=JUDGE_TOOL,
            model=model,
            max_tokens=512,
            temperature=0.0,
        )
        if response.has_tool_calls:
            args = response.tool_calls[0].arguments
            return {
                "score": float(args.get("score", 0.0)),
                "reasoning": args.get("reasoning", ""),
                "issues": args.get("issues", []),
            }
        return {"score": 0.5, "reasoning": "裁判模型未返回结构化输出", "issues": []}
    except Exception as e:
        logger.error("裁判模型调用失败: {}", e)
        return {"score": 0.0, "reasoning": f"裁判模型调用异常: {e}", "issues": ["judge_error"]}
