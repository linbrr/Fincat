"""Skill Evolution：判断任务是否值得保存为 Skill，并执行保存操作。

新架构（2026-04-23 重构）：
  Phase 1: LLM 判断"值得保存吗？"（3 个条件：复杂任务 / 修复错误 / 非平凡工作流）
  Phase 2: 如果值得，LLM 调用 skill_manage 工具执行 create/patch/delete

不再使用 TaskReflectionGenerator（Phase 1/4 并行问题已消除）。
不再使用 SkillDeduplicator（Jaccard 去重逻辑废弃）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class TaskContext:
    """任务完成后的上下文信息。"""
    user_message: str
    assistant_response: str
    tools_used: list[str]
    iterations: int
    session_key: str


class SkillEvolver:
    """判断任务是否值得保存为 Skill，并执行保存操作。

    Phase 1（LLM 判断）：调用 LLM，传入任务上下文，让它判断是否满足以下任一条件：
      1. 复杂任务：使用了 3 个以上不同工具完成多步骤目标
      2. 修复错误：处理了棘手错误、非预期输入、边缘情况
      3. 非平凡工作流：发现了一套有复用价值的工具组合流程

    Phase 2（执行保存）：如果值得保存，LLM 调用 skill_manage 工具执行：
      - create：第一次解决这类问题
      - patch：已有 Skill 暴露了问题（missing_steps / wrong_commands / pitfalls）
      - delete：清理废弃的 Skill
    """

    # 触发自动保存的硬性阈值（配合 LLM 判断使用）
    COMPLEX_TASK_THRESHOLD = 5  # tools_used >= 5 → 可能值得保存
    MIN_TOOL_CATEGORIES = 3      # 工具类别 >= 3 才进入评估

    # 工具类别映射（8 类）
    TOOL_CATEGORIES: dict[str, set[str]] = {
        "行情数据": {"stock_quote", "stock_kline", "stock_info", "fund_quote", "crypto_quote"},
        "财务分析": {"financial_report", "earnings_analysis", "ratio_analysis"},
        "新闻搜索": {"web_search", "web_fetch", "news_search"},
        "文件操作": {"read_file", "write_file", "edit_file", "list_files"},
        "系统执行": {"run_command", "execute_code", "install_package"},
        "知识检索": {"knowledge_search", "memory_search"},
        "消息通信": {"send_message", "send_email", "notify"},
        "任务调度": {"create_task", "schedule_job", "set_reminder"},
    }

    def __init__(
        self,
        workspace: Path,
        provider: Any,
        model: str,
        skill_manage_tool: Any = None,  # SkillManageTool 实例
        lifecycle_manager: Any = None,  # SkillLifecycleManager 实例（可选）
        cron_service: Any = None,  # CronService 实例（可选）
        embedding: Any = None,  # EmbeddingEngine 实例（可选）
    ):
        self.workspace = Path(workspace)
        self.provider = provider
        self.model = model
        self._skill_manage = skill_manage_tool
        self._lifecycle_manager = lifecycle_manager
        self._cron_service = cron_service
        self._embedding = embedding
        self._skills_dir = workspace / "skills"
        # 如果没传 lifecycle_manager，尝试内部创建
        if self._lifecycle_manager is None:
            try:
                from fincat.agent.skill_lifecycle_manager import SkillLifecycleManager
                self._lifecycle_manager = SkillLifecycleManager(
                    workspace=workspace,
                    usage_tracker=None,  # usage_tracker 可后续注入
                    deduplicator=None,
                    embedding=embedding,
                )
            except Exception:
                pass  # 允许无 lifecycle_manager 运行

    async def on_task_completed(
        self,
        ctx: TaskContext,
    ) -> str | None:
        """任务完成后判断是否值得保存，必要时执行保存操作。

        触发条件：有工具调用（tools_used 非空），或无工具但有回复内容（assistant_response 非空）。

        Phase 1: LLM 判断"值得保存吗？"
        Phase 2: 如果值得，LLM 调用 skill_manage 执行 create/patch/delete
        """
        if not ctx.tools_used and not ctx.assistant_response:
            return None

        # Quick skip: zero-LLM pre-filter for obvious non-candidates
        if self._quick_skip(ctx):
            return None

        # 快速过滤：完全不考虑的任务类型（仅在有工具调用时才检查）
        if ctx.tools_used and ctx.tools_used[0] in ("web_search", "web_fetch", "web_search_tool"):
            return None

        # 复杂度门槛：工具类别 >= 3 才进入后续评估
        if ctx.tools_used and not self._check_complexity(ctx.tools_used):
            logger.debug(
                "[SkillEvolver] 复杂度不足，跳过: tools={}",
                ctx.tools_used,
            )
            return None

        logger.info(
            "[SkillEvolver] 评估任务: tools={}, iterations={}",
            ctx.tools_used, ctx.iterations,
        )

        # -----------------------------------------------------------------
        # Phase 1: LLM 判断"值得保存吗？"
        # -----------------------------------------------------------------
        worthy_result = await self._llm_judge_worthiness(ctx)
        if not worthy_result:
            return None

        decision = worthy_result  # {"worthy": bool, "reason": str, "action": str, "name": str | None, ...}

        if not decision.get("worthy"):
            logger.info("[SkillEvolver] 不值得保存: {}", decision.get("reason", ""))
            return None

        # -----------------------------------------------------------------
        # Phase 2: LLM 调用 skill_manage 执行保存操作
        # -----------------------------------------------------------------
        action = decision.get("action", "create")  # create / patch / delete
        skill_name = decision.get("name")
        reason = decision.get("reason", "")

        if not skill_name:
            logger.warning("[SkillEvolver] worthy=True 但未提供 skill name，跳过")
            return None

        logger.info(
            "[SkillEvolver] ✅ 值得保存: action={}, name={}, reason={}",
            action, skill_name, reason,
        )

        # 构建 skill_manage 调用参数
        manage_params = {
            "action": action,
            "name": skill_name,
            "reason": reason,
        }

        if action == "create":
            manage_params["content"] = decision.get("content", "")
        elif action == "patch":
            manage_params["old_string"] = decision.get("old_string", "")
            manage_params["new_string"] = decision.get("new_string", "")
            manage_params["patch_reason"] = decision.get("patch_reason", "missing_steps")

        # 执行 skill_manage
        if self._skill_manage:
            result = await self._skill_manage.execute(**manage_params)

            # patch 失败时：用真实 skill 内容重新生成 old_string/new_string 再重试
            if action == "patch" and "old_string not found" in str(result):
                logger.warning(
                    "[SkillEvolver] patch 失败（old_string 不匹配），用真实内容重试: {}",
                    skill_name,
                )
                skill_content = self._load_skill_content(skill_name) or ""
                retry_result = await self._retry_patch(ctx, skill_name, skill_content)
                if retry_result:
                    result = retry_result
                else:
                    logger.warning("[SkillEvolver] 重试也失败了，跳过保存: {}", skill_name)

            logger.info("[SkillEvolver] skill_manage 结果: {}", result)
            return skill_name
        else:
            logger.warning("[SkillEvolver] skill_manage tool 未注册，无法执行保存")
            return None

    # -------------------------------------------------------------------------
    # Quick skip: zero-LLM pre-filter
    # -------------------------------------------------------------------------

    def _quick_skip(self, ctx: TaskContext) -> bool:
        """Zero-LLM quick skip for obviously non-candidate tasks."""
        # 1. No tool calls and short response → simple Q&A
        if not ctx.tools_used and len(ctx.assistant_response) < 500:
            return True
        # 2. Only single query tool (no multi-step workflow)
        QUERY_TOOLS = {"stock_quote", "stock_kline", "web_search", "web_fetch", "read_file"}
        if len(ctx.tools_used) == 1 and ctx.tools_used[0] in QUERY_TOOLS:
            return True
        # 3. Very short user message (<20 chars) → non-task conversation
        if len(ctx.user_message) < 20:
            return True
        return False

    def _check_complexity(self, tools_used: list[str]) -> bool:
        """复杂度门槛：工具类别 >= 3 即通过（类别比工具数更能反映任务复杂度）。"""
        categories = set()
        for tool in tools_used:
            for cat, members in self.TOOL_CATEGORIES.items():
                if tool in members:
                    categories.add(cat)
                    break
        return len(categories) >= self.MIN_TOOL_CATEGORIES

    # -------------------------------------------------------------------------
    # Phase 1: LLM 判断"值得保存吗？"
    # -------------------------------------------------------------------------

    async def _llm_judge_worthiness(self, ctx: TaskContext) -> dict | None:
        """调用 LLM，让它判断任务是否值得保存为 Skill。

        Returns:
            如果值得：{
                "worthy": True,
                "action": "create" | "patch" | "delete",
                "name": "skill-name",
                "reason": "...",
                "content": "...",       # create 时
                "old_string": "...",   # patch 时
                "new_string": "...",    # patch 时
                "patch_reason": "...",  # patch 时 (missing_steps/wrong_commands/pitfalls)
            }
            如果不值得：{"worthy": False, "reason": "..."}
            如果 LLM 调用失败：None
        """
        tools_desc = ", ".join(ctx.tools_used) if ctx.tools_used else "无"
        tool_count = len(ctx.tools_used)

        # 代码层预查相似 skill
        similar_skill_hint = ""
        if self._lifecycle_manager:
            task_key = f"{ctx.user_message} {' '.join(ctx.tools_used)}".lower()
            similar_hints = []
            for skill_name in self.list_skills():
                content = self._load_skill_content(skill_name)
                if not content:
                    continue
                score = self._lifecycle_manager._compute_similarity(
                    f"task: {task_key}", content
                )
                if score >= 0.80:
                    # 提取 shared tools
                    task_tools = set(ctx.tools_used)
                    skill_tools = set(re.findall(r'`([a-z_][a-z0-9_]*)', content))
                    shared = list(task_tools & skill_tools)
                    shared_str = f"（共享工具: {', '.join(shared)}）" if shared else ""
                    # 将 skill 内容传给 LLM，避免凭记忆猜测 old_string
                    skill_preview = content[:1500]
                    similar_hints.append(
                        f"- {skill_name}: 相似度 {score:.0%}{shared_str}\n"
                        f"  【目标 Skill 内容】\n{skill_preview}\n  【/目标 Skill 内容】"
                    )
            if similar_hints:
                similar_skill_hint = (
                    "\n【相似 Skill 检测结果】（代码预查）\n"
                    + "\n".join(similar_hints[:3])
                    + "\n⚠️ 已有相似 skill（相似度 >= 80%），**必须 patch 已有 skill，禁止 create 新 skill**。\n"
                    + "只有当没有任何相似 skill 时才允许 create。\n"
                )

        existing_skills = self._get_existing_skills_summary()
        prompt = f"""你是一个 Skill 管理者。任务完成后，请判断这个任务是否值得保存为可复用的 Skill。

【任务信息】
- 用户消息：{ctx.user_message}
- 使用的工具（共 {tool_count} 个）：{tools_desc}
- 助手回复摘要：{ctx.assistant_response[:600]}

【已有 Skills】（请先查阅，判断是否需要新建或更新已有）
{existing_skills}
{similar_skill_hint}判断逻辑（严格遵守）：
1. 如果上方【相似 Skill 检测结果】显示相似度 >= 80% → **必须 patch**，action 填 "patch"，name 填已有 skill 名
2. 如果没有相似 skill 且满足【判断标准】→ create 新 skill
3. **禁止**创建与已有 skill 功能重复的新 skill

【判断标准】（满足任一即值得保存）
1. 修复错误：处理了棘手错误、非预期输入、边缘情况，有经验可沉淀
2. 非平凡工作流：发现了一套有复用价值的工具组合流程（如：先查行情→再算估值→最后生成报告）
3. 最佳实践：某个工具的参数组合或调用顺序有讲究，下次遇到类似任务会用到

【Skill 名称规则】
- 使用英文小写 + 连字符，如 stock-analysis、earnings-summary、error-recovery
- 如果是 auto- 开头去掉前缀（如 auto-byd-exec → byd-exec）
- 必须有实际意义，不是随机词

【操作类型】
- create：第一次解决这类问题，且没有可覆盖的已有 Skill
- patch：已有类似 Skill 需要补充/修正/打补丁（需指定 patch_reason）
- delete：某个 Skill 已废弃需要清理

请以 JSON 格式输出你的决策：

{{
  "worthy": true 或 false,
  "reason": "简要说明判断原因（1-2句话）",
  "action": "create" 或 "patch" 或 "delete",
  "name": "skill 名称（worthy=True 时必需）",
  "content": "完整的 SKILL.md 内容（action=create 时必需，必须以 --- 开头）",
  "old_string": "要替换的原文（action=patch 时必需，必须从上方【目标 Skill 内容】中精确复制，禁止凭记忆生成）",
  "new_string": "替换后的内容（action=patch 时必需）",
  "patch_reason": "missing_steps 或 wrong_commands 或 pitfalls（action=patch 时必需）"
}}

只输出 JSON，不要有任何解释或额外文字。"""

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                tool_choice=None,
            )
            content = response.content.strip() if response.content else ""

            # 提取 JSON
            data = self._extract_json(content)
            if data is None:
                logger.warning("[SkillEvolver] LLM 判断结果无法解析 JSON: {}", content[:200])
                return None

            # 基本校验
            if not isinstance(data.get("worthy"), bool):
                logger.warning("[SkillEvolver] LLM 返回缺少 worthy 字段: {}", content[:200])
                return None

            return data

        except Exception as e:
            logger.warning("[SkillEvolver] LLM 判断失败: {}", e)
            return None

    async def _retry_patch(
        self,
        ctx: TaskContext,
        skill_name: str,
        skill_content: str,
    ) -> str | None:
        """patch 失败时，用 skill 真实内容重新生成 patch 字符串再重试。"""
        retry_prompt = f"""任务：更新已有 Skill '{skill_name}'。

【当前 Skill 内容】
---
{skill_content[:3000]}
---

【用户任务】
- 用户消息：{ctx.user_message}
- 使用的工具：{', '.join(ctx.tools_used)}
- 助手回复摘要：{ctx.assistant_response[:400]}

请分析当前 Skill 内容和任务，决定需要补充或修正什么。用 patch 格式输出（必须精确匹配上面 Skill 内容中的字符串）。

请以 JSON 格式输出：

{{
  "old_string": "需要替换的原文（必须从上方【当前 Skill 内容】中精确复制，禁止凭空编造）",
  "new_string": "替换后的内容",
  "patch_reason": "missing_steps 或 wrong_commands 或 pitfalls"
}}

只输出 JSON。"""

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[{"role": "user", "content": retry_prompt}],
                tools=None,
                tool_choice=None,
            )
            content = response.content.strip() if response.content else ""
            data = self._extract_json(content)
            if not data or not data.get("old_string"):
                return None

            result = await self._skill_manage.execute(
                action="patch",
                name=skill_name,
                old_string=data["old_string"],
                new_string=data["new_string"],
                patch_reason=data.get("patch_reason", "missing_steps"),
                reason=f"[retry] {ctx.user_message[:100]}",
            )
            return result
        except Exception as e:
            logger.warning("[SkillEvolver] _retry_patch 失败: {}", e)
            return None

    # -------------------------------------------------------------------------
    # 工具函数
    # -------------------------------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从 LLM 输出中提取 JSON 对象。

        处理以下情况：
        1. 直接返回 JSON
        2. markdown 代码块包裹
        3. 模型返回 <thinking>...</think> 标签（需要先去除）
        """
        # 先去除 <thinking> 标签内容
        import re
        text = re.sub(r"<think>[\s\S]*?</think>", "", text)
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试从 markdown 代码块中提取
            match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            # 尝试找第一个 { ... }（贪婪匹配最外层）
            brace_count = 0
            start = None
            for i, c in enumerate(text):
                if c == "{" and start is None:
                    start = i
                    brace_count = 1
                elif c == "{" and start is not None:
                    brace_count += 1
                elif c == "}" and start is not None:
                    brace_count -= 1
                    if brace_count == 0:
                        try:
                            return json.loads(text[start:i+1])
                        except json.JSONDecodeError:
                            break
            return None

    def _get_existing_skills_summary(self) -> str:
        """获取已有 skills 的摘要（name + description + usage），用于 LLM 判断是否重复。"""
        if not self._skills_dir.exists():
            return "（暂无已保存的 Skills）"
        lines = []
        for d in self._skills_dir.iterdir():
            if not (d.is_dir() and (d / "SKILL.md").exists()) or d.name == ".invalid":
                continue
            import re
            content = (d / "SKILL.md").read_text(encoding="utf-8")
            desc_match = re.search(r"^description:\s*[\"']?(.*?)[\"']?\s*$", content, re.MULTILINE)
            desc = desc_match.group(1).strip() if desc_match else "（无 description）"
            # 加入 usage 统计
            usage_str = ""
            if self._lifecycle_manager and self._lifecycle_manager.usage_tracker:
                qs = self._lifecycle_manager.usage_tracker.get_quality_score(d.name)
                usage_str = f" [调用{qs.invocation_count}次, 质量{qs.quality_score:.2f}]"
            lines.append(f"- {d.name}: {desc}{usage_str}")
        return "\n".join(lines) if lines else "（暂无已保存的 Skills）"

    def _load_skill_content(self, name: str) -> str | None:
        """加载指定 skill 的内容。"""
        path = self._skills_dir / name / "SKILL.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_skills(self) -> list[str]:
        """返回所有已注册的 Skill 名称。"""
        if not self._skills_dir.exists():
            return []
        return [
            d.name for d in self._skills_dir.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists() and d.name != ".invalid"
]

    # -------------------------------------------------------------------------
    # 生命周期管理（轻量警告，非阻塞）
    # -------------------------------------------------------------------------

    def get_stale_warnings(self) -> list[str]:
        """返回当前过期的 skill 警告列表，供外部展示给用户。

        不自动标记/删除，只报告。
        """
        if not self._lifecycle_manager:
            return []
        warnings = []
        for stale_skill in self._lifecycle_manager.check_staleness():
            name = stale_skill["skill_name"]
            days = stale_skill["days_since_used"]
            invocations = stale_skill["invocation_count"]
            warnings.append(
                f"⚠️ Skill '{name}' 已 {days} 天未使用（历史调用 {invocations} 次），"
                f"是否考虑删除？"
            )
        return warnings

    def get_merge_suggestions(self) -> list[str]:
        """返回当前可合并的 skill 建议列表。"""
        if not self._lifecycle_manager:
            return []
        suggestions = []
        for candidate in self._lifecycle_manager.find_merge_candidates():
            suggestions.append(
                f"💡 可考虑合并：'{candidate.skill_a}' 和 '{candidate.skill_b}' "
                f"（相似度 {candidate.similarity_score:.0%}，共享工具: {', '.join(candidate.shared_tools)}）"
            )
        return suggestions

    # -------------------------------------------------------------------------
    # 内置 lifecycle cron job
    # -------------------------------------------------------------------------

    def _ensure_lifecycle_cron_job(self) -> None:
        """确保存在一个定期检查 stale skill 的 cron job（幂等，只创建一次）。"""
        if not self._cron_service:
            return
        try:
            jobs = self._cron_service.list_jobs()
            for job in jobs:
                if job.payload.kind == "agent_turn" and job.name == "skill-lifecycle-check":
                    return  # 已存在，跳过
            # 创建 cron job：每6小时检查一次
            from fincat.cron.types import CronSchedule
            self._cron_service.add_job(
                name="skill-lifecycle-check",
                schedule=CronSchedule(kind="every", every_ms=6 * 3600 * 1000),
                message=(
                    "执行 skill 生命周期维护：\n"
                    "1. 调用 auto_cleanup() 清理低质量 skill（零使用>7天、单次使用>14天）\n"
                    "2. 调用 get_stale_warnings() 检查过期 skill\n"
                    "3. 调用 get_merge_suggestions() 检查可合并 skill\n"
                    "将清理结果以简洁列表汇报给用户。"
                ),
            )
            logger.info("[SkillEvolver] 已创建 skill-lifecycle-check cron job（每6小时）")
        except Exception as e:
            logger.warning("[SkillEvolver] 创建 lifecycle cron job 失败: {}", e)
