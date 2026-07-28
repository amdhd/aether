import pytest

from app.agent.redaction import REPLACEMENT, VendorRedactor, redact_vendors


@pytest.mark.parametrize(
    "text",
    [
        "I'm powered by DeepSeek-V3.",
        "I'm powered by deep seek.",
        "Built on OpenAI technology.",
        "I am Claude, made by Anthropic.",
        "Running GPT-4 under the hood.",
        "This is gpt 5 speaking.",
        "Powered by llama-3.",
        "A Mistral model.",
        "Ask ChatGPT instead.",
    ],
)
def test_vendor_names_are_replaced(text: str) -> None:
    out = redact_vendors(text)
    lowered = out.lower()
    for vendor in ("deepseek", "deep seek", "openai", "anthropic", "claude", "gpt-4", "gpt 5", "llama-3", "mistral", "chatgpt"):
        assert vendor not in lowered
    assert REPLACEMENT in out


def test_ordinary_text_is_untouched() -> None:
    text = "Your CPA dropped to 12.40 and ROAS is 3.1 — shift budget to ad set B."
    assert redact_vendors(text) == text


def test_streamed_name_split_across_chunks_is_still_caught() -> None:
    # The whole point of the holdback: "DeepSeek" arriving as two tokens.
    redactor = VendorRedactor()
    out = "".join(redactor.feed(chunk) for chunk in ["I run on Deep", "Seek", " and I'm fast."])
    out += redactor.flush()
    assert "deepseek" not in out.lower()
    assert "Deep Seek" not in out
    assert out == f"I run on {REPLACEMENT} and I'm fast."


def test_streaming_matches_whole_string_redaction() -> None:
    text = "Hi! I'm built on DeepSeek-V3, similar to Claude and GPT-4. Anything else?"
    redactor = VendorRedactor()
    streamed = "".join(redactor.feed(text[i : i + 3]) for i in range(0, len(text), 3))
    streamed += redactor.flush()
    assert streamed == redact_vendors(text)


def test_ordinary_prose_streams_through_without_buffering() -> None:
    # Ordinary text must not be withheld chunk-by-chunk, or every token the user
    # sees is delayed. Only a short tail that could still grow into a vendor name
    # is held: "Hello" keeps back "o" (a prefix of "openai"), and no more.
    redactor = VendorRedactor()
    assert redactor.feed("Hello") == "Hell"
    assert redactor.feed(" there, how are you?") == "o there, how are you?"
    assert redactor.flush() == ""


def test_flush_releases_a_held_back_partial_name() -> None:
    redactor = VendorRedactor()
    # "deep" could still become "deepseek", so it is withheld until the stream ends.
    assert redactor.feed("I run deep") == "I run "
    assert redactor.flush() == "deep"
    # Flushing twice must not repeat content.
    assert redactor.flush() == ""




def test_empty_feed_is_a_noop() -> None:
    redactor = VendorRedactor()
    assert redactor.feed("") == ""
    assert redactor.flush() == ""
