"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Opening ---, YAML body (group 1), closing --- on its own line; supports CRLF.
_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.

    Priority order: workspace > external_dirs > builtin
    """

    def __init__(
        self,
        workspace: Path,
        builtin_skills_dir: Path | None = None,
        disabled_skills: set[str] | None = None,
        skills_dirs: list[Path] | None = None,
        external_mutable: bool = False,
        usage_tracker=None,  # SkillUsageTracker | None
        content_injector=None,  # SkillContentInjector | None
        tools_registry=None,
    ):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self.disabled_skills = disabled_skills or set()
        # External skill directories (path, is_mutable)
        self._external_dirs: list[tuple[Path, bool]] = []
        if skills_dirs:
            for d in skills_dirs:
                p = Path(d).expanduser()
                if p.exists() and p.is_dir():
                    self._external_dirs.append((p, external_mutable))
        self.usage_tracker = usage_tracker
        self._injector = content_injector
        self.tools_registry = tools_registry

    def _skill_entries_from_dir(
        self, base: Path, source: str, *, skip_names: set[str] | None = None, is_mutable: bool = False
    ) -> list[dict[str, str]]:
        if not base.exists():
            return []
        entries: list[dict[str, str]] = []
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            name = skill_dir.name
            if skip_names is not None and name in skip_names:
                continue
            entries.append({"name": name, "path": str(skill_file), "source": source, "mutable": is_mutable})
        return entries

    def list_skills(
        self, filter_unavailable: bool = True, include_stale: bool = False
    ) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.
            include_stale: If False, filter out stale skills (not used for > STALE_DAYS).

        Returns:
            List of skill info dicts with 'name', 'path', 'source', 'mutable'.
        """
        skills: list[dict[str, str]] = []
        seen_names: set[str] = set()

        # Priority 1: Workspace skills (highest)
        ws_skills = self._skill_entries_from_dir(
            self.workspace_skills, "workspace", skip_names=seen_names, is_mutable=True
        )
        skills.extend(ws_skills)
        seen_names.update(e["name"] for e in ws_skills)

        # Priority 2: External directories
        for ext_dir, is_mutable in self._external_dirs:
            ext_skills = self._skill_entries_from_dir(
                ext_dir, f"external:{ext_dir.name}", skip_names=seen_names, is_mutable=is_mutable
            )
            skills.extend(ext_skills)
            seen_names.update(e["name"] for e in ext_skills)

        # Priority 3: Builtin skills (lowest)
        if self.builtin_skills and self.builtin_skills.exists():
            builtin_skills = self._skill_entries_from_dir(
                self.builtin_skills, "builtin", skip_names=seen_names
            )
            skills.extend(builtin_skills)

        if self.disabled_skills:
            skills = [s for s in skills if s["name"] not in self.disabled_skills]

        if filter_unavailable:
            result = []
            for skill in skills:
                meta = self._get_skill_meta(skill["name"])
                if not self._check_requirements(meta):
                    continue
                active, _ = self._check_activate_conditions(meta)
                if not active:
                    continue
                if not include_stale and self.usage_tracker:
                    qs = self.usage_tracker.get_quality_score(skill["name"])
                    if qs.is_stale:
                        continue
                result.append(skill)
            return result

        return skills

    def load_skill(self, name: str, inject_content: bool = True) -> str | None:
        """
        Load a skill by name (respects priority: workspace > external > builtin).

        Args:
            name: Skill name (directory name).
            inject_content: If True, apply SkillContentInjector (config/env var substitution).

        Returns:
            Skill content or None if not found.
        """
        # Check workspace first
        path = self.workspace_skills / name / "SKILL.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
            if inject_content and self._injector:
                content = self._injector.inject(content)
            return content

        # Check external dirs
        for ext_dir, _ in self._external_dirs:
            path = ext_dir / name / "SKILL.md"
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if inject_content and self._injector:
                    content = self._injector.inject(content)
                return content

        # Check builtin
        if self.builtin_skills:
            path = self.builtin_skills / name / "SKILL.md"
            if path.exists():
                content = path.read_text(encoding="utf-8")
                if inject_content and self._injector:
                    content = self._injector.inject(content)
                return content

        return None

    def load_skills_for_context(self, skill_names: list[str], inject_content: bool = True) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.
            inject_content: If True, apply SkillContentInjector.

        Returns:
            Formatted skills content.
        """
        parts = [
            f"### Skill: {name}\n\n{self._strip_frontmatter(markdown)}"
            for name in skill_names
            if (markdown := self.load_skill(name, inject_content=inject_content))
        ]
        return "\n\n---\n\n".join(parts)

    def build_skills_summary(self, exclude: set[str] | None = None, include_stale: bool = False) -> str:
        """
        Build a summary of all skills (name, description, path, availability, quality).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Args:
            exclude: Set of skill names to omit from the summary.
            include_stale: If False, stale skills are marked as [长期未使用].

        Returns:
            Markdown-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False, include_stale=include_stale)
        if not all_skills:
            return ""

        lines: list[str] = []
        for entry in all_skills:
            skill_name = entry["name"]
            if exclude and skill_name in exclude:
                continue
            meta = self._get_skill_meta(skill_name)
            available = self._check_requirements(meta)
            active, act_reason = self._check_activate_conditions(meta)
            desc = self._get_skill_description(skill_name)

            # Quality suffix
            quality_suffix = ""
            if self.usage_tracker:
                qs = self.usage_tracker.get_quality_score(skill_name)
                if qs.invocation_count > 0:
                    quality_suffix = f" (used {qs.invocation_count}x, quality={qs.quality_score:.2f})"
                if qs.is_stale:
                    quality_suffix += " [长期未使用]"

            if available and active:
                lines.append(f"- **{skill_name}** — {desc}{quality_suffix}  `{entry['path']}`")
            elif not active:
                lines.append(f"- **{skill_name}** — {desc}{quality_suffix} (inactive: {act_reason})  `{entry['path']}`")
            else:
                missing = self._get_missing_requirements(meta)
                suffix = f" (unavailable: {missing})" if missing else " (unavailable)"
                lines.append(f"- **{skill_name}** — {desc}{suffix}  `{entry['path']}`")
        return "\n".join(lines)

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return ", ".join(
            [f"CLI: {command_name}" for command_name in required_bins if not shutil.which(command_name)]
            + [f"ENV: {env_name}" for env_name in required_env_vars if not os.environ.get(env_name)]
        )

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return content
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            return content[match.end():].strip()
        return content

    def _parse_fincat_metadata(self, raw: str) -> dict:
        """Parse skill metadata JSON from frontmatter (supports fincat and openclaw keys)."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        payload = data.get("fincat", data.get("openclaw", {}))
        return payload if isinstance(payload, dict) else {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        required_bins = requires.get("bins", [])
        required_env_vars = requires.get("env", [])
        return all(shutil.which(cmd) for cmd in required_bins) and all(
            os.environ.get(var) for var in required_env_vars
        )

    def _check_activate_conditions(self, skill_meta: dict) -> tuple[bool, str]:
        """
        Check activate_when conditions in skill metadata.

        Returns (is_active, reason_if_inactive).
        Supports:
          - tool_available('tool_name')
          - time_range('HH:MM-HH:MM')
          - env('VAR_NAME')
        """
        activate_when = skill_meta.get("activate_when")
        if not activate_when:
            return True, ""

        # Parse into list
        if isinstance(activate_when, str):
            conditions = [activate_when]
        elif isinstance(activate_when, list):
            conditions = activate_when
        else:
            return True, ""

        for cond in conditions:
            cond = cond.strip().strip("'\"")
            if cond.startswith("tool_available(") and cond.endswith(")"):
                tool_name = cond[len("tool_available("):-1].strip().strip("'\"")
                if self.tools_registry and tool_name not in self.tools_registry.tool_names:
                    return False, f"tool '{tool_name}' not available"
            elif cond.startswith("time_range(") and cond.endswith(")"):
                time_range = cond[len("time_range("):-1]
                if not self._check_time_range(time_range):
                    return False, f"time_range('{time_range}') not active"
            elif cond.startswith("env(") and cond.endswith(")"):
                env_var = cond[len("env("):-1].strip().strip("'\"")
                if not os.environ.get(env_var):
                    return False, f"env '{env_var}' not set"

        return True, ""

    def _check_time_range(self, time_range: str) -> bool:
        """Check if current time is within HH:MM-HH:MM range."""
        try:
            start_str, end_str = time_range.split("-")
            now = datetime.now().time()
            from datetime import time as time_cls
            start = time_cls.fromisoformat(start_str.strip())
            end = time_cls.fromisoformat(end_str.strip())
            if start <= end:
                return start <= now <= end
            else:
                return now >= start or now <= end
        except (ValueError, TypeError):
            return True  # Malformed range → don't filter

    def _get_skill_meta(self, name: str) -> dict:
        """Get fincat metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_fincat_metadata(meta.get("metadata", ""))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        return [
            entry["name"]
            for entry in self.list_skills(filter_unavailable=True)
            if (meta := self.get_skill_metadata(entry["name"]) or {})
            and (
                self._parse_fincat_metadata(meta.get("metadata", "")).get("always")
                or meta.get("always")
            )
        ]

    def get_top_quality_skills(
        self, n: int = 5, exclude: set[str] | None = None,
    ) -> list[str]:
        """Get top-N skills by quality score (invocation count × quality_score).

        Used as fallback when SkillRouter is unavailable.
        """
        all_skills = self.list_skills(filter_unavailable=True, include_stale=False)
        if not all_skills:
            return []

        scored: list[tuple[str, float]] = []
        for entry in all_skills:
            name = entry["name"]
            if exclude and name in exclude:
                continue
            score = 0.0
            if self.usage_tracker:
                qs = self.usage_tracker.get_quality_score(name)
                score = qs.invocation_count * qs.quality_score
            scored.append((name, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored[:n]]

    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.

        Args:
            name: Skill name.

        Returns:
            Metadata dict or None.
        """
        content = self.load_skill(name)
        if not content or not content.startswith("---"):
            return None
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if not match:
            return None
        metadata: dict[str, str] = {}
        for line in match.group(1).splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip().strip('"\'')
        return metadata




# =============================================================================
# Skill Validator — 5-step validation before saving a new/improved skill
# =============================================================================

import re

class SkillValidator:
    """
    skill准入控制器
    Validates SKILL.md content against 5 criteria.
    Used by SkillEvolver before saving any generated or improved skill.
    """

    def __init__(self, tools_registry=None):
        self.tools_registry = tools_registry  # ToolRegistry instance

    def validate(self, skill_md: str) -> tuple[bool, list[str]]:
        """Run all 5 validation checks.
        Returns (is_valid, error_messages).
        """
        # ==========================================
        # 核心改进 1: 预处理“脱壳”，增强容错性
        # ==========================================
        # 1. 剔除大模型的 <think> 思考过程
        clean_content = re.sub(r'<think>.*?</think>', '', skill_md, flags=re.DOTALL).strip()
        
        # 2. 如果被 Markdown 代码块包裹 (```markdown ... ```)，提取纯文本
        if clean_content.startswith("```"):
            code_match = re.search(r'^```[a-zA-Z]*\n(.*?)\n```$', clean_content, flags=re.DOTALL | re.MULTILINE)
            if code_match:
                clean_content = code_match.group(1).strip()
            else:
                # 兜底清理首尾的 ```
                clean_content = re.sub(r'^```[a-zA-Z]*\n|```$', '', clean_content, flags=re.MULTILINE).strip()

        errors = []

        # 注意：后续所有校验均使用脱壳后的 clean_content
        fm_ok, fm_errs = self._validate_frontmatter(clean_content)
        errors.extend(fm_errs)

        tools_ok, tools_errs = self._validate_tools(clean_content)
        errors.extend(tools_errs)

        desc_ok, desc_errs = self._validate_description(clean_content)
        errors.extend(desc_errs)

        ex_ok, ex_errs = self._validate_examples(clean_content)
        errors.extend(ex_errs)

        return fm_ok and tools_ok and desc_ok and ex_ok, errors

    # ------------------------------------------------------------------------
    # Step 1: Frontmatter
    # ------------------------------------------------------------------------
    def _validate_frontmatter(self, content: str) -> tuple[bool, list[str]]:
        if not content.startswith("---"):
            return False, ["Frontmatter 缺失：必须以 '---' 开头（已清理无效头部）"]
        rest = content[3:]
        if "\n---" not in rest:
            return False, ["Frontmatter 未正确闭合：需要第二行 '---'"]
        return True, []

    # ------------------------------------------------------------------------
    # Step 2: Tool schema — 只扫描 Frontmatter 中的 tools 字段
    # ------------------------------------------------------------------------
    def _validate_tools(self, content: str) -> tuple[bool, list[str]]:
        errors = []
        if not self.tools_registry:
            return True, []  # No registry → skip

        # ==========================================
        # 核心改进 2: 缩小扫描范围，避免全篇误伤
        # ==========================================
        # 仅截取 Frontmatter 区域 (两个 --- 之间)，不使用 DOTALL 以避免 .*? 跨行匹配
        fm_match = re.search(r'^---\n(.*?)\n---', content, re.MULTILINE)
        if not fm_match:
            return True, []  # 如果没有 Frontmatter，交给 Step 1 报错即可

        fm_text = fm_match.group(1)

        # 寻找 tools: [xxx, yyy] 格式（不用 DOTALL，防止 . 匹配换行符导致串行）
        tools_match = re.search(r'tools:\s*\[(.*?)\]', fm_text)
        if not tools_match:
            return True, []  # 若没有 tools 字段，则视作不依赖外部工具
            
        tools_str = tools_match.group(1).strip()
        if not tools_str:
            return True, []  # tools: [] 为空

        # 提取工具名，去除引号和两端空格
        actual_tools = [t.strip().strip("'\"") for t in tools_str.split(",")]
        
        registered = getattr(self.tools_registry, "tool_names", []) or []
        for name in actual_tools:
            if name and name not in registered:
                errors.append(f"未知工具名：'{name}'（ToolRegistry 中不存在）")
                
        return len(errors) == 0, errors

    # ------------------------------------------------------------------------
    # Step 3: Description completeness (20-500 chars)
    # ------------------------------------------------------------------------
    def _validate_description(self, content: str) -> tuple[bool, list[str]]:
        # ==========================================
        # 核心改进 3: 放宽限制，兼容无引号 YAML 格式
        # ==========================================
        # 兼容带引号和不带引号的情况，使用 ^ 限定在行首查找
        desc_match = re.search(r'^description:\s*["\']?(.*?)["\']?$', content, re.MULTILINE)
        if not desc_match:
            return False, ["description 字段缺失（格式：description: ...）"]
            
        desc = desc_match.group(1).strip()
        
        # 将长度下限从 50 放宽至 20
        if len(desc) < 20:
            return False, [f"description 太短（{len(desc)} 字符，需要 ≥20）"]
        if len(desc) > 500:
            return False, [f"description 太长（{len(desc)} 字符，需要 ≤500）"]
        return True, []

    # ------------------------------------------------------------------------
    # Step 4: Examples validation (relaxed — checks examples exist)
    # ------------------------------------------------------------------------
    def _validate_examples(self, content: str) -> tuple[bool, list[str]]:
        # Relaxed: just check that ## 示例 section exists with content
        if "## 示例" in content or "## Example" in content:
            return True, []
        return True, []  # No strict requirement for MVP

    # ------------------------------------------------------------------------
    # Step 5: Required metadata fields (version, created_at)
    # ------------------------------------------------------------------------
    def _validate_metadata(self, content: str) -> tuple[bool, list[str]]:
        errors = []
        # 使用 MULTILINE 确保匹配的是 YAML 字段名，而非正文文本
        if not re.search(r'^version:\s*["\']?\d+["\']?', content, re.MULTILINE):
            errors.append("version 字段缺失（frontmatter 中需要 version: N）")
        if not re.search(r'^created_at:\s*["\']?\d{4}-\d{2}-\d{2}', content, re.MULTILINE):
            errors.append("created_at 字段缺失（frontmatter 中需要 created_at: YYYY-MM-DD）")
        return len(errors) == 0, errors


# =============================================================================
# Skill Content Injector — config/env variable substitution in skill content
# =============================================================================


class SkillContentInjector:
    """
    Pre-processes skill content, injecting config/env variables.

    Syntax:
      {{config.var_name}}  - inject from agent config (e.g. config.agents.defaults.model)
      {{env.VAR_NAME}}    - inject from environment variable
    """

    CONFIG_PATTERN = re.compile(r'\{\{config\.([a-zA-Z_][a-zA-Z0-9_.]*)\}\}')
    ENV_PATTERN = re.compile(r'\{\{env\.([A-Z_][A-Z0-9_]*)\}\}')

    def __init__(self, config=None):
        self.config = config

    def inject(self, content: str) -> str:
        """Process skill content, substituting variables."""
        content = self._inject_config(content)
        content = self._inject_env(content)
        return content

    def _inject_config(self, content: str) -> str:
        """Replace {{config.var}} with values from config."""

        def replacer(match):
            var_name = match.group(1)
            if self.config:
                parts = var_name.split(".")
                val = self.config
                for part in parts:
                    val = getattr(val, part, None)
                    if val is None:
                        return match.group(0)
                return str(val)
            return match.group(0)

        return self.CONFIG_PATTERN.sub(replacer, content)

    def _inject_env(self, content: str) -> str:
        """Replace {{env.VAR}} with environment variable values."""

        def replacer(match):
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))

        return self.ENV_PATTERN.sub(replacer, content)
