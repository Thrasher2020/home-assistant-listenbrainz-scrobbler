"""Pure helper functions for the ListenBrainz Scrobbler integration.

Kept free of Home Assistant imports so the URL and title handling can be tested
on its own.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Matches a trailing two-digit year "(89)" or a four-digit year "(1989)".
YEAR_PATTERN = re.compile(r"\s*\((?:19|20)?\d{2}\)\s*$")

RADIO_MARKERS = ("://radio", "/radio/")


def build_submit_url(raw_url: str) -> str:
    """Return the full ListenBrainz submit-listens URL.

    Accepts a standard base URL (http://host:9078), a slug base
    (http://host/api/listenbrainz/<slug>), or a full submit URL.
    """
    url = raw_url.strip().rstrip("/")
    if url.endswith("/submit-listens"):
        return url
    if "/api/listenbrainz" in url or "/apis/listenbrainz" in url:
        return f"{url}/submit-listens"
    if url.endswith("/1"):
        return f"{url}/submit-listens"
    return f"{url}/1/submit-listens"


def normalise_url(raw_url: str) -> str:
    """Validate and normalise a user supplied server URL."""
    value = raw_url.strip()
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("URL must start with http:// or https://")
    return value.rstrip("/")


def clean_title(title: str) -> str:
    """Remove a trailing bracketed year from a radio track title."""
    cleaned = YEAR_PATTERN.sub("", title).strip()
    return cleaned or title


def is_radio(content_id: str | None, duration: float | None) -> bool:
    """Return True when the media looks like a radio/stream source.

    `duration` is expected to already be normalised: None when unknown or 0.
    """
    if content_id:
        lowered = content_id.lower()
        if any(marker in lowered for marker in RADIO_MARKERS):
            return True
    return duration is None


def normalise_artist(artist: str, attributes: dict[str, Any]) -> str:
    """Return the main artist from Music Assistant's slash-joined names."""
    if (
        attributes.get("mass_player_type")
        and "/" in artist
        and artist.casefold() != "ac/dc"
    ):
        return artist.split("/", 1)[0].strip()
    return artist


def as_text(value: Any) -> str | None:
    """Coerce a media attribute to a non-empty string.

    Emby/Jellyfin can report artist/title as a list, so the first item is used.
    """
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_float(value: Any) -> float | None:
    """Coerce a media attribute to a float, or None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_payload(
    listen_type: str,
    track_metadata: dict[str, Any],
    listened_at: float | None = None,
) -> dict[str, Any]:
    """Build one item for the ListenBrainz `payload` array.

    A `playing_now` item must contain only `track_metadata`; a completed
    listen (`single`/`import`) must include `listened_at`.
    """
    if listen_type == "playing_now":
        return {"track_metadata": track_metadata}
    if listened_at is None:
        raise ValueError("listened_at is required for completed listens")
    return {"listened_at": int(listened_at), "track_metadata": track_metadata}
