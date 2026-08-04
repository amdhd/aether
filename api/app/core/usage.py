"""A task-local meter for LLM token usage.

The agent's streaming loop already reads ``usage`` off its own response and hands
it to ``app.core.metrics``. That works because the loop makes exactly one call it
cares about. Anything that fans out — the eval harness scoring one sample with a
generation call plus a dozen judge calls, some of them nested inside
``asyncio.gather`` — has no single place to read usage from.

This is that place. Open a meter around a unit of work and every call underneath
it accumulates into the same :class:`TokenUsage`::

    with usage.meter() as u:
        await do_a_bunch_of_llm_work()
    print(u.total_tokens)

Attribution is per-:mod:`contextvars` context, not global, so concurrent units of
work do not bleed into each other: ``asyncio`` copies the current context when a
task is created, so a task started inside a meter records into *that* meter even
while sibling tasks record into theirs. Recording outside any meter is a no-op,
which is what keeps this free for normal request paths that don't opt in.

Mutation is unsynchronised on purpose. The accumulate helpers contain no
``await``, so under a single-threaded event loop they cannot be preempted
mid-update; a lock would buy nothing.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class TokenUsage:
    """Token and call counts accumulated over some unit of work."""

    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    embedding_calls: int = 0
    embedding_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens + self.embedding_tokens

    def add_llm(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.llm_calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    def add_embedding(self, tokens: int) -> None:
        self.embedding_calls += 1
        self.embedding_tokens += tokens

    def merge(self, other: TokenUsage) -> None:
        self.llm_calls += other.llm_calls
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.embedding_calls += other.embedding_calls
        self.embedding_tokens += other.embedding_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "embedding_calls": self.embedding_calls,
            "embedding_tokens": self.embedding_tokens,
            "total_tokens": self.total_tokens,
        }


_current: contextvars.ContextVar[TokenUsage | None] = contextvars.ContextVar(
    "usage_meter", default=None
)


@contextmanager
def meter(into: TokenUsage | None = None) -> Iterator[TokenUsage]:
    """Collect usage recorded in this context (and tasks spawned from it).

    Pass ``into`` to keep accumulating in an existing counter — the eval harness
    uses that to fold a sample's retrieval usage and its later generation/judging
    usage into one row even though the two happen in separate phases.
    """
    usage = into if into is not None else TokenUsage()
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def current() -> TokenUsage | None:
    """The meter in scope, or ``None`` when nothing is collecting."""
    return _current.get()


def record_llm(prompt_tokens: int, completion_tokens: int) -> None:
    usage = _current.get()
    if usage is not None:
        usage.add_llm(prompt_tokens, completion_tokens)


def record_embedding(tokens: int) -> None:
    usage = _current.get()
    if usage is not None:
        usage.add_embedding(tokens)
