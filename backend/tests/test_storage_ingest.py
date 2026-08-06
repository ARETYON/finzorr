"""Sanity: local disk document storage (traversal jail) + text extraction."""

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from app.core.config import settings
from app.documents.ingest import (
    CHUNK_CHARS,
    CHUNK_OVERLAP,
    DocumentTooLargeError,
    UnsupportedDocumentError,
    chunk_pages,
    extract_any,
    extract_pages,
    extract_text_file,
)
from app.documents.storage import DocumentStorage, LocalDiskStorage, get_storage

pytestmark = pytest.mark.sanity


# ---------------------------------------------------------------- LocalDiskStorage


async def test_save_load_delete_roundtrip(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))
    key = "user-1/doc-1.pdf"
    await storage.save(key, b"hello bytes")
    assert await storage.load(key) == b"hello bytes"
    assert (tmp_path / "user-1" / "doc-1.pdf").read_bytes() == b"hello bytes"
    await storage.delete(key)
    assert not (tmp_path / "user-1" / "doc-1.pdf").exists()


async def test_save_overwrites_existing_key(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))
    await storage.save("a.bin", b"one")
    await storage.save("a.bin", b"two")
    assert await storage.load("a.bin") == b"two"


async def test_delete_missing_key_is_silent(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))
    await storage.delete("never-saved.pdf")  # missing_ok — no error


async def test_load_missing_key_raises(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))
    with pytest.raises(FileNotFoundError):
        await storage.load("nope.pdf")


async def test_path_traversal_rejected_on_every_operation(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path / "jail"))
    (tmp_path / "secret.txt").write_bytes(b"outside")
    for key in ("../secret.txt", "a/../../secret.txt"):
        with pytest.raises(ValueError, match="illegal storage key"):
            await storage.save(key, b"x")
        with pytest.raises(ValueError, match="illegal storage key"):
            await storage.load(key)
        with pytest.raises(ValueError, match="illegal storage key"):
            await storage.delete(key)
    assert (tmp_path / "secret.txt").read_bytes() == b"outside"  # untouched


async def test_absolute_key_rejected(tmp_path: Path) -> None:
    storage = LocalDiskStorage(str(tmp_path))
    with pytest.raises(ValueError, match="illegal storage key"):
        await storage.load("/etc/passwd")


def test_get_storage_is_a_local_disk_singleton() -> None:
    first = get_storage()
    assert isinstance(first, LocalDiskStorage)
    assert isinstance(first, DocumentStorage)
    assert get_storage() is first


# ---------------------------------------------------------------- extract_text_file


def test_csv_becomes_readable_rows() -> None:
    pages = extract_text_file(b"name,price\nTCS,100\nINFY,50\n")
    assert len(pages) == 1
    lines = pages[0].splitlines()
    assert lines[0] == "Columns: name, price"
    assert lines[1] == "name=TCS; price=100"
    assert lines[2] == "name=INFY; price=50"


def test_single_column_file_stays_plain_text() -> None:
    pages = extract_text_file(b"just\nsome\nlines\n")
    assert pages == ["just\nsome\nlines\n"]


def test_plain_text_passthrough_and_empty() -> None:
    assert extract_text_file(b"hello world") == ["hello world"]
    assert extract_text_file(b"") == []
    assert extract_text_file(b"   \n\t ") == []


def test_invalid_utf8_is_replaced_not_fatal() -> None:
    pages = extract_text_file(b"caf\xff latte")
    assert len(pages) == 1
    assert "�" in pages[0]


# ---------------------------------------------------------------- extract_any routing


def test_extract_any_routes_txt_md_csv() -> None:
    assert extract_any("notes.txt", b"plain") == ["plain"]
    assert extract_any("README.md", b"# title") == ["# title"]
    assert extract_any("Data.CSV", b"a,b\n1,2\n")[0].startswith("Columns: a, b")


def test_extract_any_rejects_unknown_types() -> None:
    with pytest.raises(UnsupportedDocumentError, match="virus.exe"):
        extract_any("virus.exe", b"MZ")
    with pytest.raises(UnsupportedDocumentError):
        extract_any("archive.zip", b"PK")


def _pdf_bytes(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    data: bytes = doc.tobytes()
    doc.close()
    return data


def test_extract_any_reads_pdf_pages() -> None:
    extracted = extract_any("report.pdf", _pdf_bytes(["alpha page", "beta page"]))
    assert len(extracted) == 2
    assert "alpha page" in extracted[0]
    assert "beta page" in extracted[1]


def test_pdf_over_page_cap_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "MAX_UPLOAD_PAGES", 1)
    with pytest.raises(DocumentTooLargeError, match="2 pages exceeds the 1-page limit"):
        extract_pages(_pdf_bytes(["one", "two"]))


# ---------------------------------------------------------------- chunk_pages


def test_chunking_windows_with_overlap_and_locators() -> None:
    long_text = "abcdefghij" * 200  # 2000 chars, > CHUNK_CHARS
    chunks = chunk_pages([long_text], "big.txt")
    assert len(chunks) == 2
    assert all(c["title"] == "big.txt" and c["locator"] == "p.1" for c in chunks)
    assert chunks[0]["text"] == long_text[:CHUNK_CHARS]
    step = CHUNK_CHARS - CHUNK_OVERLAP
    assert chunks[1]["text"] == long_text[step : step + CHUNK_CHARS]


def test_blank_pages_skipped_and_whitespace_normalized() -> None:
    chunks = chunk_pages(["", "   \n\t ", "hello   world\n\nagain"], "doc.pdf")
    assert len(chunks) == 1
    assert chunks[0]["text"] == "hello world again"
    assert chunks[0]["locator"] == "p.3"  # page numbering counts blank pages


def test_short_page_is_single_chunk() -> None:
    chunks = chunk_pages(["tiny"], "t.txt")
    assert chunks == [{"text": "tiny", "title": "t.txt", "locator": "p.1"}]
