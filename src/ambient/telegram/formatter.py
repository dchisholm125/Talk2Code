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
    
    # 1. Escape everything first to protect Telegram from stray <, >, &
    text = escape_html(text)
    
    placeholders = []
    def add_placeholder(token):
        idx = len(placeholders)
        # Use a unique string WITHOUT underscores, asterisks, or backticks so it isn't mutated
        placeholder = f"PHTALK2CODEPH{idx}PH"
        placeholders.append(token)
        return placeholder

    # 2. Handle code blocks (triple backticks)
    def replace_code_block(match):
        content = match.group(2)
        # Content is already escaped by top-level escape_html
        return add_placeholder(f"<pre><code>{content}</code></pre>")
    
    text = re.sub(r'```(\w*)\n(.*?)\n?```', replace_code_block, text, flags=re.DOTALL)
    
    # 3. Handle inline code (single backticks)
    def replace_inline_code(match):
        content = match.group(1)
        return add_placeholder(f"<code>{content}</code>")
    
    text = re.sub(r'`([^`\n]+)`', replace_inline_code, text)
    
    # 4. Handle bold (**bold**) - use non-greedy
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text, flags=re.DOTALL)
    
    # 5. Handle italic (*italic* or _italic_) - use non-greedy
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text, flags=re.DOTALL)
    text = re.sub(r'_(.*?)_', r'<i>\1</i>', text, flags=re.DOTALL)

    # 6. Re-insert placeholders in reverse order to ensure integrity
    for i in range(len(placeholders) - 1, -1, -1):
        text = text.replace(f"PHTALK2CODEPH{i}PH", placeholders[i])

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
