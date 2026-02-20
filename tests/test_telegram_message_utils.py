import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from telegram_message_utils import (
    is_within_limit,
    find_split_point,
    split_message,
    split_message_with_code_block,
    calculate_message_overhead,
    TELEGRAM_MAX_MESSAGE_LENGTH,
    TELEGRAM_SAFE_MESSAGE_LENGTH,
)


class TestIsWithinLimit:
    def test_short_message_within_limit(self):
        assert is_within_limit("Hello, world!") is True

    def test_message_at_exact_limit(self):
        text = "a" * TELEGRAM_MAX_MESSAGE_LENGTH
        assert is_within_limit(text) is True

    def test_message_just_over_limit(self):
        text = "a" * (TELEGRAM_MAX_MESSAGE_LENGTH + 1)
        assert is_within_limit(text) is False

    def test_empty_message(self):
        assert is_within_limit("") is True


class TestFindSplitPoint:
    def test_split_at_newline(self):
        text = "Hello\nWorld\n" + "x" * 100
        pos = find_split_point(text, 12)
        assert pos > 0 and pos <= 12

    def test_split_at_double_newline(self):
        text = "Hello\n\nWorld\n" + "x" * 100
        pos = find_split_point(text, 12)
        assert pos > 0 and pos <= 12

    def test_split_at_sentence_end(self):
        text = "This is a sentence. This is another." + "x" * 100
        pos = find_split_point(text, 30)
        assert pos > 0

    def test_no_good_split_point_returns_max(self):
        text = "a" * 5000
        pos = find_split_point(text, 100)
        assert pos == 100


class TestSplitMessage:
    def test_single_short_message(self):
        text = "Hello, world!"
        chunks = split_message(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_message_at_limit(self):
        text = "a" * TELEGRAM_SAFE_MESSAGE_LENGTH
        chunks = split_message(text)
        assert len(chunks) == 1
        assert len(chunks[0]) <= TELEGRAM_SAFE_MESSAGE_LENGTH

    def test_message_slightly_over_limit(self):
        text = "a" * (TELEGRAM_SAFE_MESSAGE_LENGTH + 100)
        chunks = split_message(text)
        assert len(chunks) == 2
        assert all(len(c) <= TELEGRAM_SAFE_MESSAGE_LENGTH for c in chunks)

    def test_very_long_message_multi_way_split(self):
        text = "a" * 20000
        chunks = split_message(text)
        assert len(chunks) > 1
        assert all(len(c) <= TELEGRAM_SAFE_MESSAGE_LENGTH for c in chunks)

    def test_split_at_line_boundaries(self):
        lines = ["line " + str(i) for i in range(100)]
        text = "\n".join(lines)
        chunks = split_message(text, max_length=200)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 200

    def test_single_character_over_limit(self):
        text = "a" * (TELEGRAM_SAFE_MESSAGE_LENGTH + 1)
        chunks = split_message(text)
        assert len(chunks) == 2

    def test_empty_message_returns_empty_list(self):
        assert split_message("") == []

    def test_numbering_added(self):
        text = "a" * 10000
        chunks = split_message(text)
        assert len(chunks) > 1
        assert any(f"[{i}/" in c for i, c in enumerate(chunks, 1))

    def test_no_numbering_when_within_limit(self):
        text = "Short message"
        chunks = split_message(text, add_numbering=True)
        assert len(chunks) == 1
        assert "[" not in chunks[0]


class TestSplitMessageWithCodeBlock:
    def test_short_message_wrapped(self):
        text = "Hello"
        chunks = split_message_with_code_block(text)
        assert len(chunks) == 1
        assert chunks[0].startswith("```")
        assert chunks[0].endswith("```")

    def test_long_message_multiple_chunks(self):
        text = "x" * 10000
        chunks = split_message_with_code_block(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.startswith("```")
            assert chunk.endswith("```")
            assert len(chunk) <= TELEGRAM_SAFE_MESSAGE_LENGTH

    def test_empty_message_returns_empty(self):
        assert split_message_with_code_block("") == []


class TestCalculateMessageOverhead:
    def test_markdown_overhead(self):
        overhead = calculate_message_overhead("test", "markdown")
        assert overhead == 8

    def test_html_overhead(self):
        overhead = calculate_message_overhead("test", "html")
        assert overhead == 0

    def test_no_parse_mode_overhead(self):
        overhead = calculate_message_overhead("test", None)
        assert overhead == 0


class TestEdgeCases:
    def test_whitespace_only(self):
        chunks = split_message("   \n\n   ")
        assert len(chunks) == 1

    def test_very_long_single_word(self):
        text = "a" * 10000
        chunks = split_message(text)
        assert all(len(c) <= TELEGRAM_SAFE_MESSAGE_LENGTH for c in chunks)

    def test_message_with_unicode(self):
        text = "Hello 🌍 " * 1000
        chunks = split_message(text)
        assert all(len(c) <= TELEGRAM_SAFE_MESSAGE_LENGTH for c in chunks)
        assert len(chunks) > 1

    def test_preserves_paragraphs(self):
        text = "Paragraph 1\n\nParagraph 2\n\nParagraph 3"
        chunks = split_message(text, max_length=20)
        assert len(chunks) > 0
        combined = " ".join(chunks)
        assert "Paragraph 1" in combined
        assert "Paragraph 2" in combined
        assert "Paragraph 3" in combined


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
