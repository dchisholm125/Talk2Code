"""Telegram daemon entrypoint wired to the clean core services."""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

from adapters.telegram_delivery import TelegramDeliveryAdapter
from assistant_manager import manager
from assistants.opencode import AVAILABLE_MODELS, OpenCodeAssistant
from llm_orchestrator import StreamOrchestrator
from logger import get_logger
from observability.server import (
    OBSERVABILITY_HOST,
    OBSERVABILITY_PORT,
    start_observability_server,
)
from context_engine import ContextEngine
from telemetry import get_event_ledger
from services.assistant_service import AssistantService
from services.brainstorm_service import BrainstormService
from session_manager import session_manager
from telegram_handler import (
    handle_clear,
    handle_cancel,
    handle_restart,
    handle_solo,
    handle_start,
    handle_stop,
    is_authorized,
)
from core.message import Message
from core.events import SessionID

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = os.getenv("ALLOWED_USER_ID")
FILE_PATH = os.getenv("FILE_PATH") or "."
TELEGRAM_EDIT_RATE_LIMIT = float(os.getenv("TELEGRAM_EDIT_RATE_LIMIT", "0.5"))
OBSERVABILITY_HOST = os.getenv("OBSERVABILITY_HOST", OBSERVABILITY_HOST)
OBSERVABILITY_PORT = int(os.getenv("OBSERVABILITY_PORT", OBSERVABILITY_PORT))

_logger = get_logger()
_processed_message_ids: set[int] = set()

event_ledger = get_event_ledger()
context_engine = ContextEngine(FILE_PATH, TELEGRAM_EDIT_RATE_LIMIT)
assistant_service = AssistantService(
    FILE_PATH, TELEGRAM_EDIT_RATE_LIMIT, context_engine, event_ledger
)
brainstorm_service = BrainstormService(FILE_PATH, TELEGRAM_EDIT_RATE_LIMIT)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_user:
        _logger.debug("[MSG] Received update with no message or user — skipping")
        return

    user = update.effective_user
    chat_id = update.message.chat_id
    msg_id = update.message.message_id
    raw_text = (update.message.text or "").strip()

    # ── Full inbound message log ──────────────────────────────────
    _logger.info(
        f"[MSG IN] chat={chat_id} msg_id={msg_id} "
        f"user={user.username or user.first_name!r} (id={user.id}) "
        f"text={raw_text!r}"
    )

    if not raw_text:
        _logger.debug(f"[MSG] Empty message from user {user.id} — skipping")
        return

    if not is_authorized(user.id, ALLOWED_USER_ID):
        _logger.warning(f"[AUTH DENIED] user_id={user.id} username={user.username!r} chat={chat_id}")
        await update.message.reply_text("Unauthorized. Contact the owner to add you.")
        return

    if msg_id in _processed_message_ids:
        _logger.debug(f"[MSG DUPLICATE] msg_id={msg_id} already processed — skipping")
        return
    _processed_message_ids.add(msg_id)

    lower = raw_text.lower()
    reply_to = (
        update.message.reply_to_message.message_id
        if update.message.reply_to_message
        else None
    )
    incoming = Message(user.id, chat_id, msg_id, raw_text, reply_to_id=reply_to)
    delivery = TelegramDeliveryAdapter(context.bot)

    pending_mode = session_manager.get_pending_model_selection(chat_id)
    if pending_mode:
        _logger.info(f"[ROUTE] chat={chat_id} → model_selection (pending_mode={pending_mode}) choice={raw_text!r}")
        await _apply_model_selection(chat_id, pending_mode, raw_text, update, context)
        return

    if lower == "#model" or lower == "#model #code":
        mode = "build" if lower == "#model #code" else "plan"
        _logger.info(f"[ROUTE] chat={chat_id} → #model mode={mode}")
        session_manager.set_pending_model_selection(chat_id, mode)
        await update.message.reply_text(_build_model_list_message(mode), parse_mode=ParseMode.HTML)
        return

    if lower.startswith("#stop") or lower.startswith("#cancel"):
        _logger.info(f"[ROUTE] chat={chat_id} → #stop/#cancel text={raw_text!r}")
        await handle_stop(chat_id)
        _logger.info(f"[TO-USER] chat={chat_id}: ⛔ Stop requested.")
        await update.message.reply_text("⛔ Stop requested. Terminating current action...")
        return

    if lower.startswith("#restart"):
        _logger.info(f"[ROUTE] chat={chat_id} → #restart")
        _logger.info(f"[TO-USER] chat={chat_id}: 🔍 Restart requested.")
        status_msg = await update.message.reply_text("🔍 Checking for syntax errors before restart...")
        await handle_restart(update, context, status_msg)
        return

    if lower.startswith("#solo"):
        content = raw_text[5:].strip()
        _logger.info(f"[ROUTE] chat={chat_id} → #solo content={content!r}")
        if not content:
            _logger.warning(f"[ROUTE] chat={chat_id} → #solo with no content — ignoring")
            return
        await handle_solo(chat_id, content)
        return

    if lower.startswith("#code"):
        extra = raw_text[5:].strip()
        _logger.info(f"[ROUTE] chat={chat_id} → #code extra_hint={extra!r} full_text={raw_text!r}")
        try:
            await assistant_service.handle_code_intent(incoming, delivery, extra)
        except Exception as exc:
            _logger.error(f"[#CODE FAILED] chat={chat_id}: {exc}", exc_info=True)
            await delivery.send_message(Message(None, chat_id, None, f"⚠️ Error running #code: {exc}"))
        return

    if lower.startswith("#prompt"):
        _logger.info(f"[ROUTE] chat={chat_id} → #prompt")
        prompt_body = raw_text[7:].strip()
        if not prompt_body:
            _logger.warning(f"[#PROMPT] chat={chat_id}: received #prompt with no body")
            await delivery.send_message(Message(None, chat_id, None, "Please include a prompt after #prompt."))
            return

        pending_question = session_manager.get_pending_question(chat_id)
        if pending_question:
            _logger.info(f"[#PROMPT] chat={chat_id}: injecting pending question: {pending_question!r}")
            prompt_body = (
                f"[Context: The assistant previously asked: \"{pending_question}\"]\n\n"
                f"User response: {prompt_body}"
            )
            session_manager.clear_pending_question(chat_id)
            _logger.info(f"[CONTEXT] chat={chat_id}: included pending question in prompt")

        _logger.info(f"[#PROMPT] chat={chat_id}: prompt_body={prompt_body!r}")
        try:
            await assistant_service.handle_prompt_intent(incoming, prompt_body, delivery)
        except Exception as exc:
            _logger.error(f"[#PROMPT FAILED] chat={chat_id}: {exc}", exc_info=True)
            await delivery.send_message(Message(None, chat_id, None, f"⚠️ Error running prompt: {exc}"))
        return

    if lower.startswith("#") and not lower.startswith("#solo"):
        tag = lower.split()[0][1:]
        _logger.info(f"[ROUTE] chat={chat_id} → generic assistant tag=#{tag}")
        if tag:
            ast = manager.get_assistant(tag)
            if ast:
                prompt = raw_text[len(tag) + 1:].strip()
                _logger.info(f"[#{tag.upper()}] chat={chat_id} assistant={ast.name} prompt={prompt!r}")
                if not prompt:
                    _logger.warning(f"[#{tag.upper()}] chat={chat_id}: no prompt body provided")
                    await update.message.reply_text(f"Please provide a prompt for #{tag}.")
                    return
                status = await update.message.reply_text(f"🚀 Routing to {ast.name}...")
                status_msg = Message(None, chat_id, status.message_id, status.text or "")
                stream_orchestrator = StreamOrchestrator(FILE_PATH, TELEGRAM_EDIT_RATE_LIMIT)
                try:
                    await stream_orchestrator.run_streaming(prompt, status_msg, assistant=ast)
                except Exception as exc:
                    _logger.error(f"[#{tag.upper()} FAILED] chat={chat_id}: {exc}", exc_info=True)
                    await delivery.send_message(Message(None, chat_id, None, f"⚠️ Error routing to #{tag}: {exc}"))
                return
            else:
                _logger.warning(f"[ROUTE] chat={chat_id}: unknown tag #{tag} — falling through to brainstorm")

    _logger.info(f"[ROUTE] chat={chat_id} → brainstorm text={raw_text!r}")
    session_state = session_manager.get_or_create_session(chat_id)
    session_manager.add_message(chat_id, "user", raw_text, solo=False)
    event_stream = brainstorm_service.stream_brainstorm(session_state.session_id, chat_id, incoming)
    try:
        await delivery.consume_domain_events(event_stream, incoming)
    except Exception as exc:
        _logger.error(f"[BRAINSTORM FAILED] chat={chat_id}: {exc}", exc_info=True)
        await delivery.send_message(Message(None, chat_id, None, f"⚠️ Brainstorm error: {exc}"))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_start(update, ALLOWED_USER_ID)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_clear(update, ALLOWED_USER_ID)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await handle_cancel(update, ALLOWED_USER_ID)


def _build_model_list_message(mode: str) -> str:
    mode_label = "Plan (brainstorm/reasoning)" if mode == "plan" else "Build (coder)"
    ast = manager.get_default_assistant()
    plan_lookup = getattr(ast, "get_plan_model", None)
    build_lookup = getattr(ast, "get_build_model", None)
    if mode == "plan" and plan_lookup:
        current = plan_lookup()
    elif mode == "build" and build_lookup:
        current = build_lookup()
    else:
        current = ast.get_model()
    lines = [
        f"<b>🔧 Change model — {mode_label} mode</b>",
        f"Current: <code>{current}</code>",
        "",
        "Reply with a number to switch, or anything else to cancel:\n",
    ]
    for i, (label, model_id) in enumerate(AVAILABLE_MODELS, 1):
        marker = "✅ " if model_id == current else f"{i}. "
        lines.append(f"{marker}<code>{label}</code>")
        lines.append(f"   <i>{model_id}</i>")
    return "\n".join(lines)


async def _apply_model_selection(chat_id: int, mode: str, choice: str, update, context) -> None:
    from pathlib import Path

    session_manager.clear_pending_model_selection(chat_id)
    ast = manager.get_default_assistant()
    if not isinstance(ast, OpenCodeAssistant):
        await update.message.reply_text("⚠️ Model switching is only supported for the OpenCode assistant.")
        return

    choice = choice.strip()
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(AVAILABLE_MODELS):
            label, model_id = AVAILABLE_MODELS[idx]
            env_path = Path(FILE_PATH) / ".env" if FILE_PATH != "." else Path(".env")
            if not env_path.exists():
                env_path = Path(__file__).parent.parent / ".env"
            if mode == "plan":
                ast.set_plan_model(model_id, env_path)
                mode_label = "Plan"
            else:
                ast.set_build_model(model_id, env_path)
                mode_label = "Build"
            await update.message.reply_text(
                f"✅ <b>{mode_label} model updated!</b>\n<code>{model_id}</code>\n\n"
                f"<i>.env has been updated. The change is live immediately.</i>",
                parse_mode=ParseMode.HTML,
            )
            _logger.info(f"[MODEL] {mode} model changed to {model_id} by chat {chat_id}")
        else:
            await update.message.reply_text(f"❌ Invalid choice. Pick a number between 1 and {len(AVAILABLE_MODELS)}.")
    else:
        await update.message.reply_text("❌ Model change cancelled.")


async def _start_observability(application: Application) -> None:
    def _on_obs_done(task: asyncio.Task) -> None:
        """Prevent 'Task exception was never retrieved' if the server fails to bind."""
        exc = task.exception() if not task.cancelled() else None
        if exc is not None:
            _logger.error(
                f"[OBSERVABILITY] Server task ended with exception: {exc!r} "
                "— bot continues without observability HTTP endpoint."
            )

    task = asyncio.create_task(
        start_observability_server(host=OBSERVABILITY_HOST, port=OBSERVABILITY_PORT)
    )
    task.add_done_callback(_on_obs_done)
    application.bot_data["observability_task"] = task
    _logger.info(
        f"[OBSERVABILITY] Starting on http://{OBSERVABILITY_HOST}:{OBSERVABILITY_PORT}/observability/progress"
    )


async def _stop_observability(application: Application) -> None:
    task = application.bot_data.pop("observability_task", None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _print_startup_banner() -> None:
    banner = f"""
╔══════════════════════════════════════════════════╗
║                VOICE-TO-CODE                      ║
║            Telegram Coding Assistant               ║
╠══════════════════════════════════════════════════╣
║  FILE_PATH  : {FILE_PATH:<35} ║
║  LOG_LEVEL  : {os.getenv('LOG_LEVEL', 'INFO'):<35} ║
║  VERBOSE    : {os.getenv('VERBOSE_LOGGING', 'false'):<35} ║
║  OBS HOST   : {OBSERVABILITY_HOST}:{OBSERVABILITY_PORT:<28} ║
╚══════════════════════════════════════════════════╝
"""
    print(banner)


def _check_dependencies() -> bool:
    import shutil, subprocess

    issues = []
    if not TOKEN:
        issues.append("TELEGRAM_BOT_TOKEN not set")
    if not shutil.which("opencode"):
        issues.append("opencode CLI not found in PATH")
    try:
        result = subprocess.run(["python", "-c", "import telegram"], capture_output=True, timeout=5)
        if result.returncode != 0:
            issues.append("python-telegram-bot not installed")
    except Exception:
        issues.append("python-telegram-bot import failed")
    if issues:
        for issue in issues:
            _logger.error(f"Dependency check failed: {issue}")
        return False
    return True


def main() -> None:
    _print_startup_banner()
    if not TOKEN:
        _logger.error("TELEGRAM_BOT_TOKEN missing")
        print("ERROR: TELEGRAM_BOT_TOKEN not configured. Check .env")
        return

    if not _check_dependencies():
        print("ERROR: dependency check failed. See logs.")
        return

    _logger.info("Voice-to-Code bot starting...")

    async def post_init(application: Application) -> None:
        await _start_observability(application)
        if "--restart-chat-id" in sys.argv:
            idx = sys.argv.index("--restart-chat-id")
            if idx + 1 < len(sys.argv):
                try:
                    chat_id = int(sys.argv[idx + 1])
                    await application.bot.send_message(chat_id=chat_id, text="🚀 Bot is back online!")
                except Exception as exc:
                    _logger.error(f"Failed to notify restart chat: {exc}")

    async def post_shutdown(application: Application) -> None:
        await _stop_observability(application)

    request = HTTPXRequest(connect_timeout=20, read_timeout=20)
    app = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    _logger.info("Bot running and polling for updates")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    except NetworkError as exc:
        _logger.error(
            "Telegram network initialization failed — check connectivity and proxy settings",
            exc_info=exc,
        )
        print("ERROR: Unable to reach Telegram API. Check your network or proxy configuration.")
    except Exception as exc:  # pragma: no cover - fatal startup failure
        _logger.error("Unhandled error while running bot", exc_info=exc)
        raise


if __name__ == "__main__":
    main()
