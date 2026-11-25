"""Small shared helpers."""

from __future__ import annotations


def clip(text: str, limit: int = 500) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def csv_safe(value: object) -> str:
    """Stop spreadsheet apps from treating a cell as a formula."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text
