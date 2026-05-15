"""skill_manage 工具：管理 Skill 的增删改操作。

LLM 通过调用此工具来创建、更新或删除 Skill。
工具返回执行结果（成功/错误信息），如果出错 LLM 可以重新推理参数后重试。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from fincat.agent.tools.base import Tool, tool_parameters


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------

def _atomic_write_text(file_path: Path, content: str, encoding: str = "utf-8") -> None:
    """原子写入：先写临时文件，再 rename，防止崩溃损坏原文件。"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f".{file_path.name}.tmp.",
        suffix="",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(temp_path, file_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

class _EnumSchema:
    """简易 enum string schema，用于 tool_parameters。"""

    def __init__(self, description: str, enum: tuple[str, ...], default: str | None = None):
        self._description = description
        self._enum = enum
        self._default = default

    def to_json_schema(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "string",
            "description": self._description,
            "enum": list(self._enum),
        }
        if self._default is not None:
            result["default"] = self._default
        return result


class _OptSchema:
    """可选 string schema。"""

    def __init__(self, description: str, min_length: int | None = None):
        self._description = description
        self._min_length = min_length

    def to_json_schema(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "string",
            "description": self._description,
        }
        if self._min_length is not None:
            result["minLength"] = self._min_length
        return result


# ---------------------------------------------------------------------------
# SkillManager — 纯执行层，不涉及 LLM 调用
# ---------------------------------------------------------------------------

class SkillManager:
    """Skill 的增删改执行层，供 SkillManageTool 调用。

    不做决策，只负责：
    - create：验证格式，写入文件
    - patch：在 SKILL.md 中替换 old_string → new_string
    - delete：删除整个 skill 目录
    """

    def __init__(self, workspace: Path, validator: Any = None, tracker: Any = None,
                 memory_manager: Any = None):
        self.workspace = Path(workspace)
        self._skills_dir = workspace / "skills"
        self._validator = validator  # SkillValidator
        self._tracker = tracker      # SkillUsageTracker
        self._memory_manager = memory_manager

    # ---------------------------------------------------------------------------
    # create
    # ---------------------------------------------------------------------------

    def create(self, name: str, content: str, reason: str = "") -> tuple[bool, str]:
        """创建新 Skill。

        Returns:
            (success, message)
            - 成功：message = Skill 目录路径
            - 失败：message = 错误原因
        """
        # 防御：name 禁止路径遍历字符
        if not name or any(c in name for c in "/\\.。"):
            return False, f"Invalid skill name: '{name}'"

        skill_dir = self._skills_dir / name
        if skill_dir.exists():
            return False, f"Skill '{name}' already exists. Use patch to update it."

        # 自动补全 frontmatter 缺失字段
        content = self._ensure_frontmatter(content, name)

        # 验证格式（如果 validator 存在）
        if self._validator:
            is_valid, errors = self._validator.validate(content)
            if not is_valid:
                return False, f"Validation failed: {'; '.join(errors)}"

        # 写入文件
        try:
            skill_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(skill_dir / "SKILL.md", content)
            # 初始化 feedback 文件
            _atomic_write_text(skill_dir / "feedback.jsonl", "", encoding="utf-8")
            # 记录指标
            self._record_metric("skill_created", name, {"reason": reason})
            self._notify_memory_write("create", name)
            logger.info("[SkillManager] Created skill '{}'", name)
            return True, f"Skill '{name}' created at {skill_dir}"
        except Exception as e:
            return False, f"Failed to create skill: {e}"

    # ---------------------------------------------------------------------------
    # edit
    # ---------------------------------------------------------------------------

    def edit(self, name: str, content: str, reason: str = "") -> tuple[bool, str]:
        """完整重写已有 Skill 的 SKILL.md（大幅修改）。

        与 patch 的区别：
        - patch：局部替换（old_string → new_string）
        - edit：完整重写整个 SKILL.md

        适用于：workflow 彻底重构、新的执行路径、大幅改写。

        Returns:
            (success, message)
        """
        skill_dir = self._skills_dir / name
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            return False, f"Skill '{name}' does not exist. Use create first."

        # 自动补全 frontmatter
        content = self._ensure_frontmatter(content, name)

        # 验证格式
        if self._validator:
            is_valid, errors = self._validator.validate(content)
            if not is_valid:
                return False, f"Validation failed: {'; '.join(errors)}"

        # 备份原内容
        original_content = skill_file.read_text(encoding="utf-8")

        try:
            _atomic_write_text(skill_file, content)
            self._record_metric("skill_edited", name, {"reason": reason})
            self._notify_memory_write("edit", name)
            logger.info("[SkillManager] Edited skill '{}'", name)
            return True, f"Skill '{name}' edited successfully."
        except Exception as e:
            # 回滚
            try:
                _atomic_write_text(skill_file, original_content)
            except Exception:
                pass
            return False, f"Failed to edit skill: {e}"

    # ---------------------------------------------------------------------------
    # 工具函数
    # ---------------------------------------------------------------------------

    def _validate_frontmatter_yaml(self, content: str) -> tuple[bool, str | None]:
        """用 yaml.safe_load 解析 frontmatter，校验格式正确性。

        Returns:
            (is_valid, error_message)
        """
        if not content.startswith("---"):
            return False, "Frontmatter must start with '---'"

        end_match = content.find("\n---", 3)
        if end_match < 0:
            return False, "Frontmatter is not closed (missing second '---')"

        yaml_content = content[3:end_match]
        try:
            parsed = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            return False, f"YAML parse error: {e}"

        if parsed is None:
            return False, "Frontmatter is empty"
        if not isinstance(parsed, dict):
            return False, "Frontmatter must be a YAML mapping (key: value)"

        if "name" not in parsed:
            return False, "Frontmatter missing 'name' field"
        if "description" not in parsed:
            return False, "Frontmatter missing 'description' field"

        body = content[end_match + 4:].strip()
        if not body:
            return False, "SKILL.md must have content after frontmatter"

        return True, None

    def _ensure_frontmatter(self, content: str, name: str) -> str:
        """补全 frontmatter 中缺失的字段，避免 LLM 输出不完整导致校验失败。"""
        if not content.startswith("---"):
            # 没有 frontmatter，直接包一层
            return f"---\nname: {name}\ndescription: \"{name} skill\"\n---\n\n{content}"

        # 提取 frontmatter 区域
        end = content.find("\n---", 3)
        if end < 0:
            # frontmatter 未闭合，补全闭合并补 description
            fm_lines = content[3:].split("\n")
            if not any(re.match(r"^description:", line) for line in fm_lines):
                content = content.rstrip() + f"\ndescription: \"{name} skill\"\n"
            return content + "\n---\n"

        fm = content[: end + 4]
        rest = content[end + 4:]

        # 补 description
        if not re.search(r"^description:", fm, re.MULTILINE):
            fm = fm.replace("---\n", f"---\ndescription: \"{name} skill\"\n", 1)

        return fm + rest

    # ---------------------------------------------------------------------------
    # patch
    # ---------------------------------------------------------------------------

    def patch(
        self,
        name: str,
        old_string: str,
        new_string: str,
        patch_reason: str,  # missing_steps | wrong_commands | pitfalls
    ) -> tuple[bool, str]:
        """更新已有 Skill 的内容。

        Args:
            name: Skill 名称
            old_string: 要替换的原文（必须精确匹配）
            new_string: 替换后的内容
            patch_reason: 触发 patch 的原因（missing_steps / wrong_commands / pitfalls）

        Returns:
            (success, message)
        """
        valid_reasons = ("missing_steps", "wrong_commands", "pitfalls")
        if patch_reason not in valid_reasons:
            return False, f"patch_reason must be one of {valid_reasons}, got '{patch_reason}'"

        skill_dir = self._skills_dir / name
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            return False, f"Skill '{name}' does not exist. Use create first."

        original_content = skill_file.read_text(encoding="utf-8")

        try:
            if old_string not in original_content:
                return False, (
                    f"old_string not found in skill '{name}'. "
                    "Read the skill first, then provide the exact string to replace."
                )

            new_content = original_content.replace(old_string, new_string, 1)

            # 验证修改后的内容（如果 validator 存在）
            if self._validator:
                is_valid, errors = self._validator.validate(new_content)
                if not is_valid:
                    return False, f"Validation of patched content failed: {'; '.join(errors)}"

            # 追加 improvement_reason 到 frontmatter
            new_content = self._append_improvement_reason(new_content, patch_reason, old_string)

            _atomic_write_text(skill_file, new_content)
            self._record_metric("skill_patched", name, {
                "patch_reason": patch_reason,
                "old_string": old_string[:50],
            })
            self._notify_memory_write("patch", name)
            logger.info("[SkillManager] Patched skill '{}' (reason: {})", name, patch_reason)
            return True, f"Skill '{name}' patched successfully."
        except Exception as e:
            # 回滚
            try:
                _atomic_write_text(skill_file, original_content)
            except Exception:
                pass
            return False, f"Failed to patch skill: {e}"

    def _append_improvement_reason(self, content: str, reason: str, old_string: str) -> str:
        """在 frontmatter 中追加 improvement_reason 字段。"""
        today = datetime.now().strftime("%Y-%m-%d")
        improvement = f"improved_at: '{today}'\nimprovement_reason: '{reason}'"

        if re.search(r'^improvement_reason:', content, re.MULTILINE):
            # 已有字段，更新
            content = re.sub(
                r"^improvement_reason:.*\n",
                f"improvement_reason: '{reason}' (updated {today})\n",
                content,
                flags=re.MULTILINE,
            )
        elif re.search(r'^version:', content, re.MULTILINE):
            # 在 version 后面插入
            content = re.sub(
                r"^(version:.*\n)",
                rf"\1{improvement}\n",
                content,
                flags=re.MULTILINE,
            )
        else:
            # 加到 frontmatter 末尾
            if content.startswith("---"):
                end = content.find("\n---", 3)
                if end >= 0:
                    frontmatter = content[:end]
                    rest = content[end:]
                    content = f"{frontmatter}\n{improvement}\n{rest}"
        return content

    # ---------------------------------------------------------------------------
    # delete
    # ---------------------------------------------------------------------------

    def delete(self, name: str) -> tuple[bool, str]:
        """删除 Skill 目录。

        Returns:
            (success, message)
        """
        import shutil

        skill_dir = self._skills_dir / name
        if not skill_dir.exists():
            return False, f"Skill '{name}' does not exist."

        try:
            shutil.rmtree(skill_dir)
            self._record_metric("skill_deleted", name)
            self._notify_memory_write("delete", name)
            logger.info("[SkillManager] Deleted skill '{}'", name)
            return True, f"Skill '{name}' deleted."
        except Exception as e:
            return False, f"Failed to delete skill: {e}"

    # ---------------------------------------------------------------------------
    # metrics
    # ---------------------------------------------------------------------------

    def _record_metric(self, event_type: str, skill_name: str, metadata: dict | None = None) -> None:
        """记录到 skill_evolution.jsonl。"""
        try:
            metrics_dir = self.workspace / "memory"
            metrics_dir.mkdir(parents=True, exist_ok=True)
            metrics_file = metrics_dir / "skill_evolution.jsonl"
            entry = {
                "timestamp": datetime.now().isoformat(),
                "event_type": event_type,
                "skill_name": skill_name,
                "metadata": metadata or {},
            }
            with open(metrics_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("[SkillManager] Failed to record metric: {}", e)

    # ---------------------------------------------------------------------------
    # memory write notification
    # ---------------------------------------------------------------------------

    def _notify_memory_write(self, action: str, skill_name: str) -> None:
        """通知外部 memory provider（如果有）：skill 被创建/更新/删除。"""
        if not self._memory_manager:
            return
        try:
            self._memory_manager.on_memory_write(
                action=action,
                target="skill",
                content=skill_name,
            )
        except Exception as e:
            logger.debug("[SkillManager] memory_manager.on_memory_write failed: {}", e)

# ---------------------------------------------------------------------------
# skill_manage 工具
# ---------------------------------------------------------------------------

ACTION_ENUM = ("create", "edit", "patch", "delete")
PATCH_REASON_ENUM = ("missing_steps", "wrong_commands", "pitfalls")


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "description": "操作类型：create（新建）/ edit（完整重写）/ patch（局部更新）/ delete（删除）",
            "enum": list(ACTION_ENUM),
        },
        "name": {
            "type": "string",
            "description": "Skill 名称（不含路径，如 'stock-analysis'）",
            "minLength": 1,
        },
        "content": {
            "type": "string",
            "description": "SKILL.md 完整内容（create 时必需，必须以 --- 开头）",
        },
        "old_string": {
            "type": "string",
            "description": "要替换的原文（patch 时必需，必须精确匹配 Skill 中的内容）",
        },
        "new_string": {
            "type": "string",
            "description": "替换后的内容（patch 时必需）",
        },
        "patch_reason": {
            "type": "string",
            "description": "触发 patch 的原因（三选一，patch 时必需）：\n"
                          "- missing_steps：skill 缺少完成这类任务所必需的步骤\n"
                          "- wrong_commands：skill 中的命令已过期、错误或有更好替代\n"
                          "- pitfalls：使用这个 skill 时发现了新的陷阱/坑，需要添加警告",
            "enum": list(PATCH_REASON_ENUM),
        },
        "reason": {
            "type": "string",
            "description": "操作原因说明（create/delete 时记录到 improvement_reason）",
        },
    },
    "required": ["action", "name"],
})
class SkillManageTool(Tool):
    """管理 Skill 的增删改操作。

    LLM 在任务完成后判断"值得保存"后，通过调用此工具来：
    - create：第一次解决这类问题时创建新 Skill
    - patch：已有 Skill 暴露了问题（missing_steps / wrong_commands / pitfalls）时更新
    - delete：清理废弃的 Skill

    如果操作失败，工具会返回具体错误，LLM 可以根据错误重新推理参数后重试。
    """

    def __init__(self, workspace: Path, validator: Any = None, tracker: Any = None,
                 memory_manager: Any = None):
        self._manager = SkillManager(
            workspace=workspace,
            validator=validator,
            tracker=tracker,
            memory_manager=memory_manager,
        )
        self.on_skill_change: Any = None  # callback(skill_name, action) for SkillRouter invalidation

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return (
            "管理 Skill 的增删改操作。\n"
            "action=create：新建 Skill（name 已存在时报错，换用 edit）\n"
            "action=edit：完整重写已有 Skill 的 SKILL.md（大幅修改）\n"
            "action=patch：局部更新已有 Skill 的内容（old_string → new_string）\n"
            "action=delete：删除 Skill 目录\n"
            "执行失败时返回具体错误，LLM 可根据错误重试。"
        )

    async def execute(
        self,
        action: str | None = None,
        name: str | None = None,
        content: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        patch_reason: str | None = None,
        reason: str | None = None,
        **kwargs: Any,
    ) -> str:
        # 基础校验
        if action is None:
            return "Error: 'action' is required (create / edit / patch / delete)."
        if name is None:
            return "Error: 'name' is required."

        # create
        if action == "create":
            if not content:
                return "Error: 'content' is required for create."
            ok, msg = self._manager.create(name, content, reason or "")
            if ok:
                self._notify_skill_change(name, "create")
            return msg if ok else f"Error: {msg}"

        # edit
        if action == "edit":
            if not content:
                return "Error: 'content' is required for edit."
            ok, msg = self._manager.edit(name, content, reason or "")
            if ok:
                self._notify_skill_change(name, "patch")
            return msg if ok else f"Error: {msg}"

        # patch
        if action == "patch":
            if not old_string:
                return "Error: 'old_string' is required for patch."
            if not new_string:
                return "Error: 'new_string' is required for patch."
            if not patch_reason:
                return "Error: 'patch_reason' is required for patch (missing_steps / wrong_commands / pitfalls)."
            ok, msg = self._manager.patch(name, old_string, new_string, patch_reason)
            if ok:
                self._notify_skill_change(name, "patch")
            return msg if ok else f"Error: {msg}"

        # delete
        if action == "delete":
            ok, msg = self._manager.delete(name)
            if ok:
                self._notify_skill_change(name, "delete")
            return msg if ok else f"Error: {msg}"

        return f"Error: Unknown action '{action}'. Must be one of {ACTION_ENUM}."

    def _notify_skill_change(self, skill_name: str, action: str) -> None:
        """Notify SkillRouter to invalidate/update its vector index."""
        if self.on_skill_change:
            try:
                self.on_skill_change(skill_name=skill_name, action=action)
            except Exception as e:
                logger.debug("[SkillManageTool] on_skill_change callback failed: {}", e)
