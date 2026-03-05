"""AssistantService — facade combining orchestration, delivery, and post-workflow verification."""

from __future__ import annotations

import asyncio
import os
import py_compile
import subprocess
from dataclasses import asdict
from typing import List, Tuple

from core.events import (
    SessionID,
    LifecycleEvent,
    LifecycleStatus,
    StateChanged,
    WorkflowState,
)
from core.interfaces import DeliveryInterface
from core.message import Message
from srm.context import SRMContextEngine
from ambient.session import session_manager
from core.telemetry import EventLedger
from core.logger import get_logger
from core.services.orchestrator_service import OrchestratorService
from core.services.prompt_handler import handle_prompt_intent as _handle_prompt_intent

_logger = get_logger()


class AssistantService:
    """
    High-level service used by the Telegram daemon.

    Responsibilities:
    - Run the #code workflow via OrchestratorService, streaming events to Telegram.
    - Run the #prompt workflow via the prompt handler.
    - After every #code session: verify syntax on all modified Python files and
      send a structured session-report message (git diff stat + error list).
    """

    def __init__(
        self,
        file_path: str,
        edit_rate_limit: float,
        srm_engine: SRMContextEngine,
        event_ledger: EventLedger,
    ) -> None:
        self.file_path = file_path
        self.edit_rate_limit = edit_rate_limit
        self.srm_engine = srm_engine
        self.event_ledger = event_ledger
        self.orchestrator = OrchestratorService(file_path, edit_rate_limit)

    # ------------------------------------------------------------------
    # Public intent handlers
    # ------------------------------------------------------------------

    async def handle_code_intent(
        self, message: Message, delivery: DeliveryInterface, extra: str = ""
    ) -> None:
        """Run the full #code workflow then emit a post-workflow session report."""

        state = session_manager.get_or_create_session(message.chat_id)
        session_id = state.session_id

        _logger.info(
            f"[ASSISTANT SERVICE #code] chat={message.chat_id} session={session_id} "
            f"extra={extra!r} text={message.text!r}"
        )

        await self.event_ledger.log_event(
            session_id,
            "VoiceCaptured",
            payload={
                "text": message.text,
                "chat_id": message.chat_id,
                "user_id": message.user_id,
                "message_id": message.message_id,
            },
        )

        async def _run_workflow():
            yield LifecycleEvent(
                LifecycleStatus.STARTED,
                "🧠 Processing your #code request..."
            )
            yield StateChanged(
                WorkflowState.TRANSCRIBING,
                "Analyzing intent and preparing a tailored prompt"
            )

            # SRM ENFORCEMENT: We let the orchestrator handle the symbolic extraction.
            # We just pass the extra arguments (if any) and the user text.
            async for event in self.orchestrator.stream_code_workflow(
                session_id,
                message.chat_id,
                message.text,
                extra=extra,
            ):
                if isinstance(event, LifecycleEvent) and event.status == LifecycleStatus.STARTED:
                    continue
                yield event

        await delivery.consume_domain_events(_run_workflow(), message)

        await self._post_workflow_report(message, delivery)

    async def handle_prompt_intent(
        self,
        message: Message,
        prompt_body: str,
        delivery: DeliveryInterface,
    ) -> None:
        """Run the #prompt workflow (direct, single-shot LLM call)."""
        await _handle_prompt_intent(
            message, prompt_body, delivery, self.file_path, self.edit_rate_limit
        )

    # ------------------------------------------------------------------
    # Post-workflow verification & git summary
    # ------------------------------------------------------------------

    async def _post_workflow_report(self, message: Message, delivery: DeliveryInterface) -> None:
        """
        After a #code session completes:
          1. Run `git diff --stat HEAD` to enumerate changed files.
          2. Collect newly untracked .py files.
          3. Run py_compile on every changed/new Python file.
          4. Send a structured report to the user.
        """
        try:
            git_stat, changed_py, untracked_py = await asyncio.to_thread(
                self._collect_changes
            )
            _logger.info(
                f"[POST-WORKFLOW] changed_py={changed_py} untracked_py={untracked_py}"
            )
            syntax_errors = await asyncio.to_thread(
                self._check_syntax, changed_py + untracked_py
            )
            _logger.info(f"[POST-WORKFLOW] syntax_errors={syntax_errors}")
            
            # Synchronize changes back to the SRM Brain (Synaptic Plasticity)
            all_modified = changed_py + untracked_py
            if all_modified:
                _logger.info(f"[SRM] Syncing {len(all_modified)} files to the Brain...")
                await asyncio.to_thread(self.srm_engine.sync_file_changes, all_modified)

            report = self._format_report(git_stat, syntax_errors)
            _logger.info(f"[POST-WORKFLOW] chat={message.chat_id}: session report ready, errors={len(syntax_errors)}")
        except Exception as exc:
            _logger.warning(f"[POST-WORKFLOW] report failed: {exc}", exc_info=True)
            report = f"⚠️ Could not generate session report: {exc}"

        await delivery.send_message(
            Message(None, message.chat_id, None, report, reply_to_id=message.message_id)
        )

    def _collect_changes(self) -> Tuple[str, List[str], List[str]]:
        """
        Return (git_stat, changed_py_paths, untracked_py_paths).

        Uses `git diff --stat HEAD` for modified tracked files and
        `git ls-files --others --exclude-standard` for new untracked files.
        """
        # --- tracked changes ---
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            capture_output=True,
            text=True,
            cwd=self.file_path,
        )
        git_stat = result.stdout.strip()

        changed_py: List[str] = []
        for line in git_stat.splitlines():
            parts = line.split("|")
            if len(parts) >= 2:
                fname = parts[0].strip()
                if fname.endswith(".py"):
                    full = os.path.join(self.file_path, fname)
                    if os.path.isfile(full):
                        changed_py.append(full)

        # --- untracked new files ---
        result2 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            cwd=self.file_path,
        )
        untracked_py: List[str] = [
            os.path.join(self.file_path, line.strip())
            for line in result2.stdout.splitlines()
            if line.strip().endswith(".py")
        ]

        return git_stat, changed_py, untracked_py

    def _check_syntax(self, py_files: List[str]) -> List[str]:
        """
        Return a list of 'filename: error message' strings for every Python
        file that fails to compile.  Empty list means all files are clean.
        """
        errors: List[str] = []
        for fpath in py_files:
            try:
                py_compile.compile(fpath, doraise=True)
            except py_compile.PyCompileError as exc:
                errors.append(str(exc))
        return errors

    def _format_report(self, git_stat: str, syntax_errors: List[str]) -> str:
        import html as _html

        lines = ["<b>📋 Session Report</b>"]

        if git_stat:
            lines.append(f"\n<pre>{_html.escape(git_stat)}</pre>")
        else:
            lines.append("\n<i>No git changes detected.</i>")

        if syntax_errors:
            lines.append("\n⚠️ <b>Syntax errors found:</b>")
            for err in syntax_errors:
                lines.append(f"  • <code>{_html.escape(err)}</code>")
        else:
            lines.append("\n✅ All modified Python files pass syntax check.")

        return "\n".join(lines)
