from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Upper bound on a note body. Every write embeds the note, so an unbounded
# field made the request body limit (1 MB) the only ceiling on what one call
# could send to the embeddings API. Generous for a written note, and ~5k tokens
# at 4 chars/token — comfortably inside the embedding model's 8191-token input
# limit, past which the API rejects the call and the note silently loses its
# embedding. Tunable.
MAX_NOTE_CONTENT_CHARS = 20_000

# Matches the notes.title column width.
MAX_NOTE_TITLE_CHARS = 255

# Tags are a filing aid, not a payload. Bounded in both directions so neither a
# long tag nor a long list of them becomes an unbounded field by another route.
MAX_NOTE_TAGS = 25
MAX_NOTE_TAG_CHARS = 50

NoteTag = Annotated[str, StringConstraints(max_length=MAX_NOTE_TAG_CHARS)]


class NoteBase(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_NOTE_TITLE_CHARS)
    content: str = Field(default="", max_length=MAX_NOTE_CONTENT_CHARS)
    tags: list[NoteTag] = Field(default_factory=list, max_length=MAX_NOTE_TAGS)


class NoteCreate(NoteBase):
    pass


class NoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=MAX_NOTE_TITLE_CHARS)
    content: str | None = Field(default=None, max_length=MAX_NOTE_CONTENT_CHARS)
    tags: list[NoteTag] | None = Field(default=None, max_length=MAX_NOTE_TAGS)


class NoteRead(BaseModel):
    """Deliberately does not inherit NoteBase's length limits.

    Those bound what may be *written*. A response model that enforced them too
    would start rejecting rows the database legitimately holds — every note
    written before the limits existed would 500 on read instead of displaying.
    """

    model_config = ConfigDict(from_attributes=True)

    title: str
    content: str
    tags: list[str]
    id: int
    created_at: datetime
    updated_at: datetime
