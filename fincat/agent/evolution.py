"""Self-evolution engine for code generation, sandbox execution, and skill registry."""

from __future__ import annotations

import asyncio
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class EvolutionResult:
    """Result of an evolution attempt."""
    success: bool
    skill_name: str | None = None
    code: str | None = None
    error: str | None = None
    backtest_sharpe: float | None = None


@dataclass
class EvolutionConfig:
    """Configuration for the evolution engine."""
    enabled: bool = False  # Disabled by default
    max_generation_attempts: int = 3
    min_sharpe_ratio: float = 1.0  # Minimum Sharpe ratio to register a skill
    sandbox_timeout: int = 30  # Seconds before sandbox execution times out
    skills_dir: str = "skills"


@dataclass
class FailedToolCall:
    """Record of a failed tool call that might need a new skill."""
    tool_name: str
    arguments: dict[str, Any]
    error_message: str
    timestamp: str


class EvolutionEngine:
    """Self-evolution engine that discovers, generates, tests, and registers new skills.

    Flow: Discovery (failed tool call) -> Code Generation -> Sandbox Execution ->
          Backtest Validation -> Skill Registry
    """

    def __init__(
        self,
        workspace: Path,
        config: EvolutionConfig | None = None,
        memory_store: Any = None,
    ):
        """Initialize the evolution engine.

        Args:
            workspace: Workspace path for skills directory
            config: Optional evolution configuration
            memory_store: SQLiteMemoryStore instance for storing evolution history
        """
        self.workspace = Path(workspace)
        self.config = config or EvolutionConfig()
        self.memory_store = memory_store
        self._skills_dir = self.workspace / self.config.skills_dir
        self._evolution_history: list[EvolutionResult] = []

    def _should_evolve(self, failed_call: FailedToolCall) -> bool:
        """Determine if a failed tool call warrants skill generation.

        Args:
            failed_call: The failed tool call record

        Returns:
            True if evolution should be attempted
        """
        if not self.config.enabled:
            return False

        # Only evolve for "tool not found" type errors
        tool_not_found_keywords = [
            "not found",
            "not implemented",
            "does not exist",
            "unknown tool",
            "没有找到",
            "未实现",
        ]
        error_lower = failed_call.error_message.lower()
        return any(kw in error_lower for kw in tool_not_found_keywords)

    async def _generate_code(self, failed_call: FailedToolCall) -> str | None:
        """Generate code to address the failed tool call.

        This is a placeholder that would integrate with an LLM for code generation.
        In production, this would prompt an LLM to generate the implementation.

        Args:
            failed_call: The failed tool call record

        Returns:
            Generated Python code or None
        """
        # TODO: Integrate with LLM for actual code generation
        # For now, return a skeleton
        logger.info("Code generation would be triggered for tool: {}", failed_call.tool_name)

        skeleton = f'''
async def {failed_call.tool_name}(**kwargs) -> str:
    """Auto-generated skill for: {failed_call.tool_name}"""
    # TODO: Implement based on failed call: {failed_call.arguments}
    return "Not yet implemented"
'''
        return skeleton

    async def _sandbox_execute(self, code: str) -> tuple[bool, str]:
        """Execute code in a sandboxed environment.

        Args:
            code: Python code to execute

        Returns:
            Tuple of (success, output/error message)
        """
        if not self.config.enabled:
            return False, "Evolution disabled"

        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            temp_path = f.name

        try:
            # Execute in a separate process with timeout
            proc = await asyncio.create_subprocess_exec(
                "python",
                temp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.config.sandbox_timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return False, f"Sandbox execution timed out after {self.config.sandbox_timeout}s"

            if proc.returncode != 0:
                return False, stderr.decode() or "Execution failed"

            return True, stdout.decode() or "Success"
        except Exception as e:
            return False, str(e)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    async def _run_backtest(
        self,
        code: str,
        symbols: list[str] | None = None,
    ) -> float | None:
        """Run a backtest to validate generated code.

        Args:
            code: The generated code to backtest
            symbols: Optional list of symbols to backtest

        Returns:
            Sharpe ratio or None if backtest failed
        """
        # TODO: Integrate with backtest engine (e.g., backtrader, vectorbt)
        logger.info("Backtest validation for code length: {}", len(code))
        return None  # Placeholder

    async def _register_skill(
        self,
        skill_name: str,
        code: str,
    ) -> bool:
        """Register a generated skill to the skills directory.

        Args:
            skill_name: Name of the skill
            code: Python code for the skill

        Returns:
            True if registration succeeded
        """
        try:
            self._skills_dir.mkdir(parents=True, exist_ok=True)
            skill_file = self._skills_dir / f"{skill_name}.py"
            skill_file.write_text(code, encoding="utf-8")
            logger.info("Registered new skill: {}", skill_name)
            return True
        except Exception as e:
            logger.error("Failed to register skill '{}': {}", skill_name, e)
            return False

    async def maybe_evolve(self, failed_call: FailedToolCall) -> bool:
        """Main entry point: attempt to evolve a skill from a failed tool call.

        Args:
            failed_call: The failed tool call record

        Returns:
            True if evolution succeeded and skill was registered
        """
        if not self._should_evolve(failed_call):
            logger.debug("Evolution not warranted for: {}", failed_call.tool_name)
            return False

        logger.info("Starting evolution for failed tool: {}", failed_call.tool_name)

        # Step 1: Generate code
        code = await self._generate_code(failed_call)
        if not code:
            result = EvolutionResult(success=False, error="Code generation failed")
            self._evolution_history.append(result)
            return False

        # Step 2: Sandbox execution
        success, output = await self._sandbox_execute(code)
        if not success:
            result = EvolutionResult(success=False, code=code, error=f"Sandbox failed: {output}")
            self._evolution_history.append(result)
            logger.warning("Sandbox execution failed: {}", output)
            return False

        # Step 3: Backtest (if applicable for trading code)
        sharpe = await self._run_backtest(code, symbols=failed_call.arguments.get("symbols"))
        if sharpe is not None and sharpe < self.config.min_sharpe_ratio:
            result = EvolutionResult(
                success=False,
                code=code,
                backtest_sharpe=sharpe,
                error=f"Sharpe ratio {sharpe} below threshold {self.config.min_sharpe_ratio}",
            )
            self._evolution_history.append(result)
            logger.warning("Backtest failed: Sharpe {} < {}", sharpe, self.config.min_sharpe_ratio)
            return False

        # Step 4: Register skill
        skill_name = f"auto_{failed_call.tool_name}"
        registered = await self._register_skill(skill_name, code)

        result = EvolutionResult(
            success=registered,
            skill_name=skill_name if registered else None,
            code=code,
            backtest_sharpe=sharpe,
            error=None if registered else "Registration failed",
        )
        self._evolution_history.append(result)

        if registered:
            logger.info("Successfully evolved and registered skill: {}", skill_name)
            # Store reflection about this evolution
            if self.memory_store:
                try:
                    self.memory_store.add_reflection(
                        content=f"Auto-evolved skill '{skill_name}' for tool '{failed_call.tool_name}'. "
                               f"Sharpe: {sharpe}, Error: {result.error}",
                        tags=["evolution", skill_name, failed_call.tool_name],
                        reflection_type="skill_evolution",
                    )
                except Exception as e:
                    logger.debug("Failed to store evolution reflection: {}", e)

        return registered

    def get_evolution_history(self) -> list[EvolutionResult]:
        """Get the history of evolution attempts."""
        return self._evolution_history.copy()

    def get_registered_skills(self) -> list[str]:
        """Get list of auto-generated skills."""
        if not self._skills_dir.exists():
            return []
        return [f.stem for f in self._skills_dir.glob("auto_*.py")]
