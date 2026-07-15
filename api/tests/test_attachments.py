import pytest

from app.services.attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENT_ROWS,
    AttachmentError,
    parse_tabular_file,
)


def test_parses_csv_and_summarises_shape() -> None:
    raw = b"campaign,spend,revenue\nBrand,100,500\nProspecting,200,300\n"
    name, table = parse_tabular_file("campaigns.csv", raw)
    assert name == "campaigns.csv"
    assert "2 data rows × 3 columns" in table
    assert "campaign,spend,revenue" in table
    assert "Prospecting,200,300" in table


def test_sniffs_semicolon_delimiter() -> None:
    raw = b"a;b;c\n1;2;3\n"
    _, table = parse_tabular_file("euro.csv", raw)
    # Normalised to comma-separated regardless of source delimiter.
    assert "a,b,c" in table
    assert "1,2,3" in table


def test_parses_tsv() -> None:
    raw = b"a\tb\n1\t2\n"
    _, table = parse_tabular_file("data.tsv", raw)
    assert "a,b" in table
    assert "1,2" in table


def test_rejects_unsupported_extension() -> None:
    with pytest.raises(AttachmentError) as exc:
        parse_tabular_file("report.pdf", b"whatever")
    assert exc.value.status_code == 422


def test_rejects_empty_file() -> None:
    with pytest.raises(AttachmentError):
        parse_tabular_file("empty.csv", b"")


def test_rejects_oversized_file() -> None:
    raw = b"a,b\n" + b"1,2\n" * (MAX_ATTACHMENT_BYTES)
    with pytest.raises(AttachmentError):
        parse_tabular_file("big.csv", raw)


def test_truncates_excess_rows() -> None:
    rows = "\n".join(f"{i},{i}" for i in range(MAX_ATTACHMENT_ROWS + 50))
    raw = ("a,b\n" + rows + "\n").encode()
    _, table = parse_tabular_file("many.csv", raw)
    assert "truncated" in table
    # Header + kept rows only.
    assert table.count("\n") <= MAX_ATTACHMENT_ROWS + 2
