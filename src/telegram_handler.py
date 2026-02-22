import asyncio
import os
import sys
import html
import py_compile
from pathlib import Path
from typing import Optional, Any, Dict
from telegram import Update
from telegram.constants import ParseMode
from telegram.error import TimedOut, RetryAfter

from logger import get_logger
from session_manager import session_manager
from telegram_message_utils import prepare_html_preview

_logger = get_logger()


async def _edit_with_retry(bot, chat_id: int, message_id: int, text: str, **kwargs) -> bool:
    preview = text[:150].replace('\n', ' ') + ('...' if len(text) > 150 else '')
    _logger.info(f"[TO-USER-EDIT] chat={chat_id} msg_id={message_id}: {preview}")
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            **kwargs
        )
        return True
    except (TimedOut, RetryAfter) as e:
        _logger.warning(f"Edit message retry after error for chat {chat_id}, msg {message_id}")
        await asyncio.sleep(0.5)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                **kwargs
            )
            return True
        except Exception as retry_err:
            _logger.error(f"Edit message failed after retry for chat {chat_id}, msg {message_id}: {retry_err}")
            return False
    except Exception as e:
        if "not modified" in str(e).lower():
            return True
        _logger.error(f"Edit message failed for chat {chat_id}, msg {message_id}: {e}")
        return False


async def _edit_with_fallback(bot, chat_id: int, message_id: int, text: str, update: Update, **kwargs) -> bool:
    """Edit message, and if it fails, send a new message as fallback."""
    success = await _edit_with_retry(bot, chat_id, message_id, text, **kwargs)
    if not success:
        _logger.warning(f"Edit failed, sending fallback message to chat {chat_id}")
        try:
            preview = text[:150].replace('\n', ' ') + ('...' if len(text) > 150 else '')
            _logger.info(f"[TO-USER-FALLBACK] chat={chat_id}: {preview}")
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            return True
        except Exception as fallback_err:
            _logger.error(f"Fallback message also failed for chat {chat_id}: {fallback_err}")
            return False
    return True


async def update_heartbeat_with_progress(
    bot,
    chat_id: int,
    message_id: int,
    header: str,
    body: str,
    elapsed: int,
    progress: Optional[float] = None,
    eta_seconds: Optional[int] = None,
    tokens: int = 0
) -> None:
    progress_str = f" {int(progress * 100)}%" if progress is not None else ""
    
    eta_str = ""
    if eta_seconds is not None:
        if eta_seconds < 60:
            eta_str = f" | ETA: {eta_seconds}s"
        else:
            eta_mins = eta_seconds // 60
            eta_secs = eta_seconds % 60
            eta_str = f" | ETA: {eta_mins}m{eta_secs}s"
    
    token_str = f" | Tokens: {tokens}" if tokens > 0 else ""
    
    body_block = f"\n\n<code>{prepare_html_preview(body, limit=3500)}</code>" if body else ""
    
    text = f"{header} <i>[Wait: {elapsed}s]{progress_str}{eta_str}{token_str}</i>{body_block}"
    
    await _edit_with_retry(
        bot,
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode=ParseMode.HTML
    )


def is_authorized(user_id: int, allowed_user_id: Optional[str]) -> bool:
    return not allowed_user_id or str(user_id) == allowed_user_id


async def handle_start(update: Update, allowed_user_id: Optional[str]) -> None:
    user = update.effective_user
    _logger.info(f"User {user.id} ({user.username}) started the bot")

    if not is_authorized(user.id, allowed_user_id):
        await update.message.reply_html(f"Unauthorized. Your ID: {user.id}")
        return

    await update.message.reply_html(
        f"Hey {user.mention_html()}! 💬 <b>Chatroom mode active.</b>\n\n"
        f"Just talk — I'll brainstorm with you.\n\n"
        f"<code>#solo your thoughts</code> — monologue mode; I'll listen silently.\n"
        f"<code>#code [optional focus]</code> — compress this conversation into a "
        f"coding task and hand it off to opencode.\n\n"
        f"<code>#stop</code> — cancel an ongoing session.\n\n"
        f"/clear — wipe the slate and start a fresh conversation.\n"
        f"/cancel — cancel an ongoing #code session."
    )


async def handle_clear(update: Update, allowed_user_id: Optional[str]) -> None:
    if not is_authorized(update.effective_user.id, allowed_user_id):
        return

    chat_id = update.message.chat_id
    session_manager.clear_conversation(chat_id)
    await update.message.reply_text("🧹 Conversation cleared. Fresh start!")


async def handle_cancel(update: Update, allowed_user_id: Optional[str]) -> None:
    if not is_authorized(update.effective_user.id, allowed_user_id):
        return

    chat_id = update.message.chat_id
    session_manager.cancel_session(chat_id)
    await update.message.reply_text("⛔ Session cancelled.")


async def handle_restart(update: Update, context: Any, status_msg) -> None:
    chat_id = update.message.chat_id
    
    await _edit_with_retry(
        context.bot,
        chat_id=chat_id,
        message_id=status_msg.message_id,
        text="🔍 Checking for syntax errors before restart..."
    )
    
    error_files = []
    for py_file in Path("src").glob("*.py"):
        try:
            py_compile.compile(str(py_file), doraise=True)
        except py_compile.PyCompileError as e:
            error_files.append(f"{py_file.name}: {str(e)}")
    
    if error_files:
        error_list = "\n".join(error_files)
        await _edit_with_retry(
            context.bot,
            chat_id=chat_id,
            message_id=status_msg.message_id,
            text=f"❌ Restart aborted! Syntax errors detected:\n\n{error_list}"
        )
        return

    await _edit_with_retry(
        context.bot,
        chat_id=chat_id,
        message_id=status_msg.message_id,
        text="🔄 Syntax check passed! Restarting daemon... Be right back!"
    )
    
    argv = [arg for arg in sys.argv if arg != '--restart-chat-id' and not str(arg).replace('-', '').isdigit()]
    argv.extend(['--restart-chat-id', str(chat_id)])
    
    os.execv(sys.executable, ['python'] + argv)


async def handle_solo(chat_id: int, content: str) -> None:
    _logger.debug(f"[SOLO] {content}")
    session_manager.add_message(chat_id, "user", content, solo=True)


async def handle_stop(chat_id: int) -> None:
    session_manager.cancel_session(chat_id)
    _logger.info(f"Stop requested for chat {chat_id}")
