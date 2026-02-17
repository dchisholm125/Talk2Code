from typing import Optional, Literal, Set
from context_manager import ContextManager, Channel

SYSTEM_PROMPTS = {
    "/think": """You are a senior software architect. Give concise, actionable 
advice. Focus on architecture decisions, trade-offs, and 
practical implementation guidance. Keep responses under 500 words 
unless the question demands more depth.""",
    "/code": """You are an expert coding assistant helping with codebase questions.
Provide precise, actionable code guidance. When making changes, 
explain briefly what you're doing and why. Focus on correctness 
and maintainability."""
}


class PromptBuilder:
    """Builds prompts with channel-aware context inclusion.
    
    Structure:
    1. System instructions (channel-specific)
    2. Durable summary (shared + channel-specific)
    3. Recent turns (same thread, same channel)
    4. Cross-channel glimpse (optional, small, tag-filtered)
    5. Current query
    """

    def __init__(self, context_manager: ContextManager):
        self.ctx = context_manager

    def build(
        self,
        channel: Channel,
        user_query: str,
        thread_id: str = "default",
        chat_id: Optional[int] = None,
        include_cross_channel: bool = True,
        recent_limit: int = 4,
        cross_channel_limit: int = 1
    ) -> str:
        """Build a complete prompt with context."""
        parts = []

        parts.append(f"System:\n{SYSTEM_PROMPTS.get(channel, '')}")

        durable_summary = self.ctx.get_summary(thread_id, chat_id)
        if durable_summary:
            parts.append(f"\nDurable Summary:\n{durable_summary}")

        recent = self.ctx.get_context_summary(
            limit=recent_limit,
            channel=channel,
            thread_id=thread_id,
            chat_id=chat_id
        )
        if recent:
            parts.append(f"\n{recent}")

        if include_cross_channel:
            other_channel = "/code" if channel == "/think" else "/think"
            cross = self.ctx.get_cross_channel_glimpse(other_channel, cross_channel_limit, tagged_only=True)
            if cross:
                parts.append(f"\nOther channel (key events):\n{cross}")

        parts.append(f"\nCurrent question:\n{user_query}")

        return "\n\n".join(parts)

    def build_think(self, query: str, thread_id: str = "default", chat_id: Optional[int] = None) -> str:
        """Build a prompt for /think channel."""
        return self.build("/think", query, thread_id, chat_id, include_cross_channel=True)

    def build_code(self, query: str, thread_id: str = "default", chat_id: Optional[int] = None) -> str:
        """Build a prompt for /code channel."""
        return self.build("/code", query, thread_id, chat_id, include_cross_channel=True)

    def extract_tags_from_query(self, query: str) -> Set[str]:
        """Extract decision tags from user query."""
        tags = set()
        query_lower = query.lower()
        if any(w in query_lower for w in ["decide", "decision", "choose", "pick"]):
            tags.add("decision")
        if any(w in query_lower for w in ["plan", "planned", "planning"]):
            tags.add("plan")
        if any(w in query_lower for w in ["summary", "summarize", "summarise"]):
            tags.add("summary")
        if any(w in query_lower for w in ["todo", "to-do", "task"]):
            tags.add("todo")
        return tags

    def extract_tags_from_response(self, content: str) -> Set[str]:
        """Extract decision tags from assistant response."""
        tags = set()
        content_lower = content.lower()
        if "decided" in content_lower or "decision:" in content_lower:
            tags.add("decision")
        if "changed" in content_lower or "modified" in content_lower:
            tags.add("change")
        if "result:" in content_lower or "completed" in content_lower:
            tags.add("result")
        if "#plan" in content_lower or "#todo" in content_lower:
            tags.add("todo")
        return tags
