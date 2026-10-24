"""Types shared by several tasks (API_SPEC.md §3)."""

from __future__ import annotations

from enum import StrEnum

from app.tasks.base import HouseStyleData, PostCheckFailed


class Platform(StrEnum):
    facebook = "facebook"
    instagram = "instagram"
    tiktok = "tiktok"
    youtube = "youtube"
    x = "x"
    google_business = "google_business"
    podcast = "podcast"
    website = "website"


def normalize_tag(tag: str) -> str:
    cleaned = "".join(ch for ch in tag.strip() if ch.isalnum() or ch == "_")
    return f"#{cleaned}" if cleaned else ""


def merge_tags(tags: list[str], style: HouseStyleData) -> list[str]:
    """Normalize hashtags, drop duplicates (case-insensitive), and add the client's
    always-include tags that are missing."""
    seen: set[str] = set()
    merged: list[str] = []
    for tag in [*tags, *style.always_tags]:
        normalized = normalize_tag(tag)
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            merged.append(normalized)
    return merged


def check_avoid_words(texts: list[str], style: HouseStyleData) -> None:
    lowered = [t.lower() for t in texts]
    for word in style.avoid_words:
        if word and any(word.lower() in t for t in lowered):
            # Never include the text itself in the error (no content in logs).
            raise PostCheckFailed("avoid_word_present")
