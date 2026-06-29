from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

_PDF_PASSWORDS: dict[str, str] = {}


def set_pdf_password(pdf_path: Path, password: str | None) -> None:
    key = str(pdf_path.resolve())
    cleaned = (password or "").strip()
    if cleaned:
        _PDF_PASSWORDS[key] = cleaned
    else:
        _PDF_PASSWORDS.pop(key, None)


def clear_pdf_password(pdf_path: Path) -> None:
    _PDF_PASSWORDS.pop(str(pdf_path.resolve()), None)


def open_pdf(pdf_path: Path):
    password = _PDF_PASSWORDS.get(str(pdf_path.resolve()))
    return pdfplumber.open(str(pdf_path), password=password or None)


def is_password_error(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in {
        "PDFPasswordIncorrect",
        "PasswordProtected",
        "PDFEncryptionError",
    }:
        return True

    message = str(exc).lower()
    return (
        "password" in message
        or "encrypted" in message
        or "decrypt" in message
        or "crypt filter" in message
    )


def find_regex_in_pages(
    pages: list[pdfplumber.page.Page],
    pattern: str,
    *,
    flags: int = 0,
    reverse: bool = False,
) -> str | None:
    ordered_pages = reversed(pages) if reverse else iter(pages)
    for page in ordered_pages:
        text = page.extract_text() or ""
        match = re.search(pattern, text, flags=flags)
        if match:
            return match.group(1)
    return None
