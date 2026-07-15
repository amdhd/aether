"""Parsing of user-uploaded tabular files (CSV/TSV) for chat attachments.

Kept deliberately dependency-free (stdlib ``csv`` only). The parsed table is
injected into the agent's context as plain text, so the goal here is to
normalise the upload into a compact, readable table and to enforce hard limits
so a large paste can't blow the model's context window.
"""

from __future__ import annotations

import csv
import io

from fastapi import HTTPException, status

MAX_ATTACHMENT_BYTES = 200_000  # ~200 KB
MAX_ATTACHMENT_ROWS = 300  # data rows kept before truncation (excludes header)
ALLOWED_EXTENSIONS = (".csv", ".tsv")


class AttachmentError(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _extension(filename: str) -> str:
    _, _, ext = filename.rpartition(".")
    return f".{ext.lower()}" if ext else ""


def parse_tabular_file(filename: str, raw: bytes) -> tuple[str, str]:
    """Validate and normalise an uploaded CSV/TSV file.

    Returns ``(name, table_text)`` where ``table_text`` is a normalised,
    possibly row-truncated rendering of the table suitable for injecting into
    the model context. Raises ``AttachmentError`` (HTTP 422) on any problem.
    """

    name = (filename or "").strip() or "attachment"
    ext = _extension(name)
    if ext not in ALLOWED_EXTENSIONS:
        raise AttachmentError(
            f"Unsupported file type '{ext or name}'. Upload a .csv or .tsv file "
            "(in Google Sheets or Excel: File → Download → CSV)."
        )
    if not raw:
        raise AttachmentError("The uploaded file is empty.")
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError(
            f"File is too large ({len(raw) // 1000} KB). The limit is "
            f"{MAX_ATTACHMENT_BYTES // 1000} KB — trim the rows or columns and try again."
        )

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError as exc:  # pragma: no cover - defensive
            raise AttachmentError("Could not read the file as text. Please upload a plain CSV/TSV export.") from exc

    delimiter = "\t" if ext == ".tsv" else ","
    try:
        # Sniff the delimiter for .csv (handles semicolon exports from some locales).
        if ext == ".csv":
            sample = text[:2048]
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
            except csv.Error:
                delimiter = ","
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as exc:
        raise AttachmentError("Could not parse the file as a table. Check that it's a valid CSV/TSV export.") from exc

    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise AttachmentError("The file has no readable rows.")

    header, *data_rows = rows
    truncated = len(data_rows) > MAX_ATTACHMENT_ROWS
    kept_rows = data_rows[:MAX_ATTACHMENT_ROWS]

    lines = [",".join(cell.strip() for cell in header)]
    lines.extend(",".join(cell.strip() for cell in row) for row in kept_rows)

    summary = f"{len(kept_rows)} data rows × {len(header)} columns"
    if truncated:
        summary += f" (truncated from {len(data_rows)} rows; only the first {MAX_ATTACHMENT_ROWS} are shown)"

    table_text = f"{summary}\n" + "\n".join(lines)
    return name, table_text
