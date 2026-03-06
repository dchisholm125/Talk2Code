import re
from typing import Tuple

_ASSISTANT_PROMPT_RE = re.compile(
    r"💬\s*The assistant asked:.*?(?:\n\n|\n|$)",
    re.IGNORECASE | re.DOTALL,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CODE_BLOCK_RE = re.compile(r"```[^`]*```", re.DOTALL)


def strip_assistant_summaries(text: str) -> str:
    return _ASSISTANT_PROMPT_RE.sub("", text).strip()


def _first_sentences(text: str, limit: int = 2) -> str:
    parts = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]
    if not parts:
        return text.strip()
    return " ".join(parts[:limit])


def _first_code_block(text: str) -> str:
    match = _CODE_BLOCK_RE.search(text)
    return match.group(0).strip() if match else ""


def summarize_response(full_response: str, mode: str = "auto") -> Tuple[str, bool]:
    cleaned = strip_assistant_summaries(full_response)
    if not cleaned:
        return "", False

    normalized_mode = mode.lower()
    if normalized_mode not in {"auto", "brief", "expandable"}:
        normalized_mode = "auto"

    if normalized_mode == "auto":
        normalized_mode = "brief" if len(cleaned) > 600 or cleaned.count("\n") > 2 else "expandable"

    if normalized_mode == "brief":
        snippet = _first_sentences(cleaned, limit=2)
        code_block = _first_code_block(cleaned)
        if code_block:
            snippet = f"{snippet}\n\n{code_block}" if snippet else code_block
        snippet = snippet.strip() or cleaned
        has_more = len(cleaned) > len(snippet) + 40
        return snippet, has_more

    prompt = "\n\nWant me to dig deeper?"
    return f"{cleaned}{prompt}", False
