import os
import re
import logging

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = int(os.getenv("TELEGRAM_MAX_MESSAGE_LENGTH", "4000"))
TELEGRAM_SAFE_MESSAGE_LENGTH = int(os.getenv("TELEGRAM_SAFE_MESSAGE_LENGTH", "3800"))

MARKDOWN_MAX_LENGTH = int(os.getenv("TELEGRAM_MARKDOWN_MAX_LENGTH", "3500"))

CODE_BLOCK_PREFIX = "```\n"
CODE_BLOCK_SUFFIX = "\n```"

NUMBERING_FORMAT = " [{current}/{total}]"


def calculate_message_overhead(text: str, parse_mode: str | None) -> int:
    """Calculate overhead added by formatting."""
    if parse_mode == "markdown":
        return len(CODE_BLOCK_PREFIX) + len(CODE_BLOCK_SUFFIX)
    elif parse_mode == "html":
        return 0
    return 0


def is_within_limit(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> bool:
    """Check if message is within Telegram's length limit."""
    return len(text) <= limit


def find_split_point(text: str, max_length: int) -> int:
    """Find the best position to split the text."""
    if len(text) <= max_length:
        return len(text)
    
    search_text = text[:max_length]
    
    newline_pos = search_text.rfind('\n')
    if newline_pos > max_length * 0.7:
        return newline_pos
    
    double_newline_pos = search_text.rfind('\n\n')
    if double_newline_pos > max_length * 0.5:
        return double_newline_pos
    
    sentence_end = max(
        search_text.rfind('. '),
        search_text.rfind('! '),
        search_text.rfind('? '),
        search_text.rfind('.\n'),
        search_text.rfind('!\n'),
        search_text.rfind('?\n'),
    )
    if sentence_end > max_length * 0.6:
        return sentence_end + 1
    
    return max_length


def split_message(
    text: str,
    max_length: int = TELEGRAM_SAFE_MESSAGE_LENGTH,
    add_numbering: bool = True,
) -> list[str]:
    """
    Split a message into chunks that fit within Telegram's limits.
    
    Args:
        text: The message text to split
        max_length: Maximum length per chunk (default: TELEGRAM_SAFE_MESSAGE_LENGTH)
        add_numbering: Whether to add message numbering (e.g., "1/3")
    
    Returns:
        List of message chunks
    """
    if not text:
        return []
    
    if is_within_limit(text, max_length):
        return [text]
    
    chunks = []
    remaining = text
    chunk_num = 0
    
    while remaining:
        chunk_num += 1
        
        overhead = 0
        if add_numbering:
            overhead = len(NUMBERING_FORMAT.format(current=chunk_num, total="?"))
        
        available = max_length - overhead
        
        if len(remaining) <= available:
            chunk = remaining
            remaining = ""
        else:
            split_pos = find_split_point(remaining, available)
            if split_pos == 0:
                split_pos = available
            chunk = remaining[:split_pos]
            remaining = remaining[split_pos:].lstrip('\n ')
        
        if add_numbering and remaining:
            total_estimate = chunk_num + (len(remaining) // available) + 1
            chunk += NUMBERING_FORMAT.format(current=chunk_num, total=total_estimate)
        
        if chunk:
            chunks.append(chunk)
    
    if add_numbering and len(chunks) > 1:
        chunks = _renumber_chunks(chunks)
    
    return [c for c in chunks if c.strip()]


def _renumber_chunks(chunks: list[str]) -> list[str]:
    """Update chunk numbering to reflect actual total count."""
    total = len(chunks)
    result = []
    for i, chunk in enumerate(chunks, 1):
        chunk = re.sub(r'\s\[\d+/\d+\]\s*$', '', chunk)
        chunk = chunk.rstrip()
        if i < total:
            chunk += f" [{i}/{total}]"
        result.append(chunk)
    return result


def split_message_with_code_block(
    text: str,
    max_length: int = TELEGRAM_SAFE_MESSAGE_LENGTH,
) -> list[str]:
    """
    Split a message that should be wrapped in a code block.
    
    Accounts for the code block wrapper overhead when calculating limits.
    """
    if not text:
        return []
    
    wrapper_length = len(CODE_BLOCK_PREFIX) + len(CODE_BLOCK_SUFFIX)
    available = max_length - wrapper_length
    
    if is_within_limit(text, available):
        return [f"{CODE_BLOCK_PREFIX}{text}{CODE_BLOCK_SUFFIX}"]
    
    chunks = split_message(text, available, add_numbering=True)
    return [f"{CODE_BLOCK_PREFIX}{chunk}{CODE_BLOCK_SUFFIX}" for chunk in chunks]


def format_chunk_numbering(chunk: str, current: int, total: int) -> str:
    """Add message numbering to a chunk."""
    chunk = chunk.rstrip()
    if current < total:
        return f"{chunk} [{current}/{total}]"
    return chunk


def prepare_html_preview(text: str, limit: int = 3500) -> str:
    """
    Escape text for HTML and truncate to fit within a Telegram message limit.
    Ensures we don't break HTML entities during truncation.
    """
    import html
    if not text:
        return ""
    
    escaped = html.escape(text)
    if len(escaped) <= limit:
        return escaped
    
    # Truncate from the end (since we usually show the tail of the buffer)
    truncated = escaped[-limit:]
    
    # Fix broken entities at the start of the truncation
    first_semi = truncated.find(";")
    first_amp = truncated.find("&")
    
    if first_semi != -1 and (first_amp == -1 or first_semi < first_amp):
        truncated = truncated[first_semi + 1:]
        
    return "..." + truncated
