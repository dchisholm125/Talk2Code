import re
import os
import logging
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

# Default to HTML as it is more robust for LLM output than MarkdownV2
TELEGRAM_FORMATTER_ENABLED = os.getenv("TELEGRAM_FORMATTER_ENABLED", "true").lower() == "true"
TELEGRAM_PARSE_MODE = os.getenv("TELEGRAM_PARSE_MODE", "html").lower()

def escape_html(text: str) -> str:
    """Escape special characters for HTML parse mode."""
    if not text:
        return ""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text

def format_as_html(text: str) -> str:
    """Detect common markdown patterns and convert them to HTML tags safely."""
    if not text:
        return ""
    
    # 1. Handle code blocks (triple backticks)
    # Be careful not to escape the tags we add
    def replace_code_block(match):
        lang = match.group(1) or ""
        content = match.group(2)
        return f"<pre><code>{escape_html(content)}</code></pre>"
    
    text = re.sub(r'```(\w*)\n(.*?)\n?```', replace_code_block, text, flags=re.DOTALL)
    
    # 2. Handle inline code (single backticks)
    text = re.sub(r'`([^`\n]+)`', lambda m: f"<code>{escape_html(m.group(1))}</code>", text)
    
    # 3. Handle bold (**bold**)
    text = re.sub(r'\*\*([^*]+)\*\*', lambda m: f"<b>{escape_html(m.group(1))}</b>", text)
    
    # 4. Handle italic (_italic_ or *italic*)
    text = re.sub(r'\*([^*]+)\*', lambda m: f"<i>{escape_html(m.group(1))}</i>", text)
    text = re.sub(r'_([^_]+)_', lambda m: f"<i>{escape_html(m.group(1))}</i>", text)

    return text

def format_for_telegram(raw_text: str) -> str:
    """Transform raw LLM output into Telegram-friendly HTML format."""
    if not raw_text:
        return raw_text
    
    if TELEGRAM_PARSE_MODE == "markdown":
        # Keep old markdown behavior but with minimal escaping if requested, 
        # though we recommend HTML.
        return raw_text # Fallback for now
    
    try:
        # For HTML mode, we use our tag-aware formatter
        return format_as_html(raw_text)
    except Exception as e:
        logger.error(f"Formatting error: {e}")
        return escape_html(raw_text)

def should_format() -> bool:
    return TELEGRAM_FORMATTER_ENABLED

def get_parse_mode():
    if TELEGRAM_PARSE_MODE == "html":
        return ParseMode.HTML
    return ParseMode.MARKDOWN
