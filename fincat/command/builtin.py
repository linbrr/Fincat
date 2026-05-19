"""Built-in slash command handlers."""

from __future__ import annotations

import asyncio
import os
import sys

from fincat import __version__
from fincat.bus.events import OutboundMessage
from fincat.command.router import CommandContext, CommandRouter
from fincat.utils.helpers import build_status_content
from fincat.utils.restart import set_restart_notice_to_env


async def cmd_stop(ctx: CommandContext) -> OutboundMessage:
    """Cancel all active tasks and subagents for the session."""
    loop = ctx.loop
    msg = ctx.msg
    tasks = loop._active_tasks.pop(msg.session_key, [])
    cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
    for t in tasks:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass
    sub_cancelled = await loop.subagents.cancel_by_session(msg.session_key)
    total = cancelled + sub_cancelled
    content = f"Stopped {total} task(s)." if total else "No active task to stop."
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content=content,
        metadata=dict(msg.metadata or {})
    )


async def cmd_restart(ctx: CommandContext) -> OutboundMessage:
    """Restart the process in-place via os.execv."""
    msg = ctx.msg
    set_restart_notice_to_env(channel=msg.channel, chat_id=msg.chat_id)

    async def _do_restart():
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable, "-m", "fincat"] + sys.argv[1:])

    asyncio.create_task(_do_restart())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Restarting...",
        metadata=dict(msg.metadata or {})
    )


async def cmd_status(ctx: CommandContext) -> OutboundMessage:
    """Build an outbound status message for a session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    ctx_est = 0
    try:
        ctx_est, _ = loop.consolidator.estimate_session_prompt_tokens(session)
    except Exception:
        pass
    if ctx_est <= 0:
        ctx_est = loop._last_usage.get("prompt_tokens", 0)
    
    # Fetch web search provider usage (best-effort, never blocks the response)
    search_usage_text: str | None = None
    try:
        from fincat.utils.searchusage import fetch_search_usage
        web_cfg = getattr(loop, "web_config", None)
        search_cfg = getattr(web_cfg, "search", None) if web_cfg else None
        if search_cfg is not None:
            provider = getattr(search_cfg, "provider", "duckduckgo")
            api_key = getattr(search_cfg, "api_key", "") or None
            usage = await fetch_search_usage(provider=provider, api_key=api_key)
            search_usage_text = usage.format()
    except Exception:
        pass  # Never let usage fetch break /status
    active_tasks = loop._active_tasks.get(ctx.key, [])
    task_count = sum(1 for t in active_tasks if not t.done())
    try:
        task_count += loop.subagents.get_running_count_by_session(ctx.key)
    except Exception:
        pass
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_status_content(
            version=__version__, model=loop.model,
            start_time=loop._start_time, last_usage=loop._last_usage,
            context_window_tokens=loop.context_window_tokens,
            session_msg_count=len(session.get_history(max_messages=0)),
            context_tokens_estimate=ctx_est,
            search_usage_text=search_usage_text,
            active_task_count=task_count,
        ),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_new(ctx: CommandContext) -> OutboundMessage:
    """Start a fresh session."""
    loop = ctx.loop
    session = ctx.session or loop.sessions.get_or_create(ctx.key)
    snapshot = session.messages[session.last_consolidated:]
    session.clear()
    loop.sessions.save(session)
    loop.sessions.invalidate(session.key)
    if snapshot:
        loop._schedule_background(loop.consolidator.archive(snapshot))
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content="New session started.",
        metadata=dict(ctx.msg.metadata or {})
    )


async def cmd_dream(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger a Dream consolidation run."""
    import time

    loop = ctx.loop
    msg = ctx.msg

    async def _run_dream():
        t0 = time.monotonic()
        try:
            loop.dream.populate_extraction_from_resources()
            changelog, items_by_type, total_batches, total_items = await loop.dream.run_extraction()
            await loop.dream.run(changelog=changelog, new_items_by_type=items_by_type)
            elapsed = time.monotonic() - t0

            if total_batches == 0:
                content = "Dream: 没有待处理的内容。"
            elif total_items == 0:
                content = f"Dream 完成，耗时 {elapsed:.1f}s。\n处理了 {total_batches} 个批次，未提取到新记忆（对话可能不含可提取内容）。"
            else:
                type_counts = {mt: len(items) for mt, items in items_by_type.items()}
                type_str = "、".join(f"{mt}({cnt})" for mt, cnt in type_counts.items())
                lines = [
                    f"Dream 完成，耗时 {elapsed:.1f}s。",
                    f"处理 {total_batches} 个批次，提取 {total_items} 条记忆：{type_str}",
                ]
                if changelog:
                    lines.append(f"变更 {len(changelog)} 条。")
                content = "\n".join(lines)
        except Exception as e:
            elapsed = time.monotonic() - t0
            content = f"Dream 失败，耗时 {elapsed:.1f}s: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_dream())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Dreaming...",
    )


async def cmd_learn(ctx: CommandContext) -> OutboundMessage:
    """Manually trigger skill extraction from the last task."""
    loop = ctx.loop
    msg = ctx.msg

    async def _run_learn():
        task_ctx = getattr(loop, "_last_task_ctx", None)
        if not task_ctx:
            content = "No recent task to learn from."
        else:
            try:
                skill_name = await loop._skill_evolver.on_task_completed(task_ctx)
                if skill_name:
                    content = f"Learned new skill: {skill_name}"
                else:
                    content = "No new skill generated (pattern may already exist or task not suitable)."
            except Exception as e:
                content = f"Learning failed: {e}"
        await loop.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    asyncio.create_task(_run_learn())
    return OutboundMessage(
        channel=msg.channel, chat_id=msg.chat_id, content="Learning...",
    )


def _extract_changed_files(diff: str) -> list[str]:
    """Extract changed file paths from a unified diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        path = parts[3]
        if path.startswith("b/"):
            path = path[2:]
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _format_changed_files(diff: str) -> str:
    files = _extract_changed_files(diff)
    if not files:
        return "No tracked memory files changed."
    return ", ".join(f"`{path}`" for path in files)


def _format_dream_log_content(commit, diff: str, *, requested_sha: str | None = None) -> str:
    files_line = _format_changed_files(diff)
    lines = [
        "## Dream Update",
        "",
        "Here is the selected Dream memory change." if requested_sha else "Here is the latest Dream memory change.",
        "",
        f"- Commit: `{commit.sha}`",
        f"- Time: {commit.timestamp}",
        f"- Changed files: {files_line}",
    ]
    if diff:
        lines.extend([
            "",
            f"Use `/dream-restore {commit.sha}` to undo this change.",
            "",
            "```diff",
            diff.rstrip(),
            "```",
        ])
    else:
        lines.extend([
            "",
            "Dream recorded this version, but there is no file diff to display.",
        ])
    return "\n".join(lines)


def _format_dream_restore_list(commits: list) -> str:
    lines = [
        "## Dream Restore",
        "",
        "Choose a Dream memory version to restore. Latest first:",
        "",
    ]
    for c in commits:
        lines.append(f"- `{c.sha}` {c.timestamp} - {c.message.splitlines()[0]}")
    lines.extend([
        "",
        "Preview a version with `/dream-log <sha>` before restoring it.",
        "Restore a version with `/dream-restore <sha>`.",
    ])
    return "\n".join(lines)


async def cmd_dream_log(ctx: CommandContext) -> OutboundMessage:
    """Show what the last Dream changed.

    Default: diff of the latest commit (HEAD~1 vs HEAD).
    With /dream-log <sha>: diff of that specific commit.
    """
    store = ctx.loop.consolidator.store
    git = store.git

    if not git.is_initialized():
        if store.get_last_dream_cursor() == 0:
            msg = "Dream has not run yet. Run `/dream`, or wait for the next scheduled Dream cycle."
        else:
            msg = "Dream history is not available because memory versioning is not initialized."
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=msg, metadata={"render_as": "text"},
        )

    args = ctx.args.strip()

    if args:
        # Show diff of a specific commit
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        if not result:
            content = (
                f"Couldn't find Dream change `{sha}`.\n\n"
                "Use `/dream-restore` to list recent versions, "
                "or `/dream-log` to inspect the latest one."
            )
        else:
            commit, diff = result
            content = _format_dream_log_content(commit, diff, requested_sha=sha)
    else:
        # Default: show the latest commit's diff
        commits = git.log(max_entries=1)
        result = git.show_commit_diff(commits[0].sha) if commits else None
        if result:
            commit, diff = result
            content = _format_dream_log_content(commit, diff)
        else:
            content = "Dream memory has no saved versions yet."

    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_dream_restore(ctx: CommandContext) -> OutboundMessage:
    """Restore memory files from a previous dream commit.

    Usage:
        /dream-restore          — list recent commits
        /dream-restore <sha>    — revert a specific commit
    """
    store = ctx.loop.consolidator.store
    git = store.git
    if not git.is_initialized():
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content="Dream history is not available because memory versioning is not initialized.",
        )

    args = ctx.args.strip()
    if not args:
        # Show recent commits for the user to pick
        commits = git.log(max_entries=10)
        if not commits:
            content = "Dream memory has no saved versions to restore yet."
        else:
            content = _format_dream_restore_list(commits)
    else:
        sha = args.split()[0]
        result = git.show_commit_diff(sha)
        changed_files = _format_changed_files(result[1]) if result else "the tracked memory files"
        new_sha = git.revert(sha)
        if new_sha:
            content = (
                f"Restored Dream memory to the state before `{sha}`.\n\n"
                f"- New safety commit: `{new_sha}`\n"
                f"- Restored files: {changed_files}\n\n"
                f"Use `/dream-log {new_sha}` to inspect the restore diff."
            )
        else:
            content = (
                f"Couldn't restore Dream change `{sha}`.\n\n"
                "It may not exist, or it may be the first saved version with no earlier state to restore."
            )
    return OutboundMessage(
        channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
        content=content, metadata={"render_as": "text"},
    )


async def cmd_kb(ctx: CommandContext) -> OutboundMessage:
    """Knowledge base management: /kb add|list|delete|stats"""
    import time
    from pathlib import Path

    def _get_private_store():
        from fincat.config.paths import get_knowledge_dir
        from fincat.knowledge.store import RAGKnowledgeStore
        kb_dir = get_knowledge_dir()
        return RAGKnowledgeStore(kb_dir / "knowledge.db")

    args = ctx.args.strip()
    if not args:
        return OutboundMessage(
            channel=ctx.msg.channel, chat_id=ctx.msg.chat_id,
            content=(
                "用法：\n"
                "/kb add <PDF路径> — 导入 PDF 到私有知识库\n"
                "/kb list — 列出已导入文件\n"
                "/kb delete <file_id> — 删除文件\n"
                "/kb stats — 显示统计信息"
            ),
        )

    parts = args.split(maxsplit=1)
    subcmd = parts[0].lower()
    loop = ctx.loop
    msg = ctx.msg

    if subcmd == "add":
        file_path_str = parts[1].strip() if len(parts) > 1 else ""
        if not file_path_str:
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="请指定 PDF 文件路径，如：/kb add D:\\docs\\report.pdf",
            )

        async def _do_add():
            import shutil
            t0 = time.monotonic()
            try:
                from fincat.config.paths import get_knowledge_dir
                from fincat.knowledge.ingest import IngestPipeline
                from fincat.knowledge.vector_store import KnowledgeVectorStore
                fp = Path(file_path_str)
                if not fp.exists():
                    content = f"文件不存在：{fp}"
                elif fp.suffix.lower() != ".pdf":
                    content = f"仅支持 PDF 文件，当前：{fp.suffix}"
                else:
                    kb_dir = get_knowledge_dir()
                    # Copy PDF to workspace/knowledge/pdfs/
                    pdfs_dir = kb_dir / "pdfs"
                    dest = pdfs_dir / fp.name
                    if not dest.exists():
                        shutil.copy2(str(fp), str(dest))
                    # Ingest into private knowledge store
                    private_db = kb_dir / "knowledge.db"
                    from fincat.agent.embedding import EmbeddingEngine
                    from fincat.knowledge.store import RAGKnowledgeStore
                    embedding = EmbeddingEngine()
                    vs = KnowledgeVectorStore(
                        db_path=private_db,
                        vector_dir=kb_dir / "vectors",
                        embedding=embedding,
                    )
                    pipeline = IngestPipeline(embedding=embedding, vector_store=vs)
                    # Override store to use private DB (singleton may return public)
                    pipeline.store = RAGKnowledgeStore(private_db)
                    vr = pipeline.ingest_file(
                        file_path=fp, category="user_upload", user_id="user",
                    )
                    # Rebuild FAISS index to clean up any orphan vectors
                    vs.rebuild_index()
                    elapsed = time.monotonic() - t0
                    content = (
                        f"导入完成，耗时 {elapsed:.1f}s\n"
                        f"文件: {fp.name}\n"
                        f"切片: {vr.get('chunk_count', 0)}\n"
                        f"向量: {vr.get('vector_count', 0)}"
                    )
            except Exception as e:
                content = f"导入失败：{e}"
            await loop.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content,
            ))

        asyncio.create_task(_do_add())
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"正在导入 {Path(file_path_str).name}...",
        )

    elif subcmd == "list":
        try:
            store = _get_private_store()
            files = store.list_files(user_id="user")
            if not files:
                files = store.list_files()
            if not files:
                content = "知识库中暂无文件。"
            else:
                lines = ["## 已导入文件\n"]
                for f in files:
                    fid = f.get("file_id", "?")
                    fname = f.get("file_path", "?")
                    if isinstance(fname, str):
                        fname = Path(fname).name
                    status = f.get("status", "?")
                    chunks = f.get("chunk_count", 0)
                    lines.append(f"- `{fid}` | {fname} | {status} | {chunks} chunks")
                content = "\n".join(lines)
        except Exception as e:
            content = f"获取文件列表失败：{e}"
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=content, metadata={"render_as": "text"},
        )

    elif subcmd == "delete":
        file_id = parts[1].strip() if len(parts) > 1 else ""
        if not file_id:
            return OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="请指定 file_id，如：/kb delete file_abc123\n使用 /kb list 查看可用 ID。",
            )
        try:
            store = _get_private_store()
            result = store.delete_file_and_chunks(file_id)
            if result["deleted"]:
                content = f"已删除：{file_id}"
            else:
                content = f"未找到文件：{file_id}"
        except Exception as e:
            content = f"删除失败：{e}"
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        )

    elif subcmd == "stats":
        try:
            from fincat.config.paths import get_knowledge_dir
            from fincat.knowledge.vector_store import KnowledgeVectorStore
            store = _get_private_store()
            stats = store.get_stats()
            # Also get chunk count from rag_chunks table
            chunk_count = store.get_chunk_count()
            lines = [
                "## 私有知识库统计",
                f"- 文档: {stats.get('documents', 0)}",
                f"- 章节: {stats.get('sections', 0)}",
                f"- 切片: {chunk_count}",
                f"- 事实: {stats.get('facts', 0)}",
            ]
            # Vector store stats
            try:
                from fincat.agent.embedding import EmbeddingEngine
                kb_dir = get_knowledge_dir()
                embedding = EmbeddingEngine()
                vs = KnowledgeVectorStore(
                    db_path=kb_dir / "knowledge.db",
                    vector_dir=kb_dir / "vectors",
                    embedding=embedding,
                )
                vs_stats = vs.get_stats()
                lines.append(f"- 向量: {vs_stats.get('total_vectors', 0)}")
            except Exception:
                pass
            content = "\n".join(lines)
        except Exception as e:
            content = f"获取统计失败：{e}"
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=content, metadata={"render_as": "text"},
        )

    else:
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id,
            content=f"未知子命令：{subcmd}\n可用：add, list, delete, stats",
        )


async def cmd_help(ctx: CommandContext) -> OutboundMessage:
    """Return available slash commands."""
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=build_help_text(),
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


async def cmd_skill_dedup(ctx: CommandContext) -> OutboundMessage:
    """Show skill merge suggestions based on vector similarity."""
    loop = ctx.loop
    if not loop or not hasattr(loop, '_skill_evolver'):
        return OutboundMessage(
            channel=ctx.msg.channel,
            chat_id=ctx.msg.chat_id,
            content="Skill evolver not available.",
        )
    suggestions = loop._skill_evolver.get_merge_suggestions()
    if not suggestions:
        content = "当前没有可合并的 skill。"
    else:
        content = "## Skill 合并建议\n\n" + "\n".join(suggestions)
    return OutboundMessage(
        channel=ctx.msg.channel,
        chat_id=ctx.msg.chat_id,
        content=content,
        metadata={**dict(ctx.msg.metadata or {}), "render_as": "text"},
    )


def build_help_text() -> str:
    """Build canonical help text shared across channels."""
    lines = [
        "🐈 fincat commands:",
        "/new — Start a new conversation",
        "/stop — Stop the current task",
        "/restart — Restart the bot",
        "/status — Show bot status",
        "/dream — Manually trigger Dream consolidation",
        "/dream-log — Show what the last Dream changed",
        "/dream-restore — Revert memory to a previous state",
        "/learn — Save last task as a reusable skill",
        "/skill-dedup — Show skill merge suggestions",
        "/kb — Manage private knowledge base (add/list/delete/stats)",
        "/help — Show available commands",
    ]
    return "\n".join(lines)


def register_builtin_commands(router: CommandRouter) -> None:
    """Register the default set of slash commands."""
    router.priority("/stop", cmd_stop)
    router.priority("/restart", cmd_restart)
    router.priority("/status", cmd_status)
    router.exact("/new", cmd_new)
    router.exact("/status", cmd_status)
    router.exact("/dream", cmd_dream)
    router.exact("/dream-log", cmd_dream_log)
    router.prefix("/dream-log ", cmd_dream_log)
    router.exact("/dream-restore", cmd_dream_restore)
    router.prefix("/dream-restore ", cmd_dream_restore)
    router.exact("/learn", cmd_learn)
    router.exact("/skill-dedup", cmd_skill_dedup)
    router.prefix("/kb", cmd_kb)
    router.exact("/help", cmd_help)
