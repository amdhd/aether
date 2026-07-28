"""Strip upstream vendor and model names out of assistant output.

The system prompt already tells the model not to discuss what it runs on, but a
prompt is a request, not a guarantee — models volunteer (and often *hallucinate*)
vendor names anyway. This is the deterministic backstop for the common case.

It is deliberately *not* a security control. Anyone who cares can still fingerprint
the backend from timing and response-style signals that no text filter touches.
The point is brand hygiene: the assistant is Aether and shouldn't say otherwise.

Because output is streamed token by token, a vendor name can straddle a chunk
boundary ("Deep" + "Seek"). ``VendorRedactor`` therefore holds back a short tail
of each chunk until enough text has arrived to rule out a match spanning it.
"""

import re

# Only names specific enough to be unambiguous. Bare "llama" and "gemini" would
# also match an animal and a star sign, so the model families require a version
# digit; "gemini" is accepted as-is since the false-positive rate is low enough
# to trade for catching the common leak.
_VENDOR_PATTERN = re.compile(
    r"deep[-\s]?seek|open[-\s]?ai|anthropic|chatgpt|claude|gemini|mistral|qwen"
    r"|gpt[-\s]?[0-9]+(?:\.[0-9]+)?|llama[-\s]?[0-9]+",
    re.IGNORECASE,
)

REPLACEMENT = "Aether"

# Longest literal spellings the pattern can match. Every proper prefix of these
# is a string that *might* still grow into a vendor name, so it must be held back
# rather than emitted. Anything else streams straight through.
_LONGEST_FORMS = (
    "deep seek",
    "deepseek",
    "open ai",
    "openai",
    "anthropic",
    "chatgpt",
    "claude",
    "gemini",
    "mistral",
    "qwen",
    "gpt-0",
    "llama-0",
)

_PARTIAL_TAIL = re.compile(
    "("
    + "|".join(
        sorted(
            {re.escape(form[:i]) for form in _LONGEST_FORMS for i in range(1, len(form))},
            key=len,
            reverse=True,
        )
    )
    + ")$",
    re.IGNORECASE,
)


def redact_vendors(text: str) -> str:
    """Replace any vendor/model name in a complete string."""
    return _VENDOR_PATTERN.sub(REPLACEMENT, text)


class VendorRedactor:
    """Incremental redactor for a single streamed field.

    Feed it chunks as they arrive and emit whatever it hands back; call ``flush``
    once the stream ends to release the final held-back tail. The text returned
    across a full feed/flush cycle equals ``redact_vendors`` over the whole input.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, text: str) -> str:
        if not text:
            return ""
        # Substitution is idempotent (REPLACEMENT never matches the pattern), so
        # re-running it over the retained tail each time is safe.
        self._buffer = redact_vendors(self._buffer + text)
        # Hold back only a tail that could still become a vendor name; ordinary
        # prose streams through untouched, so tokens aren't needlessly delayed.
        partial = _PARTIAL_TAIL.search(self._buffer)
        cut = partial.start() if partial else len(self._buffer)
        emit, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return emit

    def flush(self) -> str:
        out = redact_vendors(self._buffer)
        self._buffer = ""
        return out
