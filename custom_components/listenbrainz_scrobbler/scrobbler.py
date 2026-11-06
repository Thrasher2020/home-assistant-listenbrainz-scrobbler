"""Core scrobbling logic for the ListenBrainz Scrobbler integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any

import aiohttp

from homeassistant.const import STATE_PLAYING
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_NOW_PLAYING,
    CONF_PLAYERS,
    CONF_RADIO_MIN_SECONDS,
    CONF_SCROBBLE_PERCENT,
    CONF_STRIP_YEAR,
    CONF_TOKEN,
    CONF_URL,
    DEFAULT_NOW_PLAYING,
    DEFAULT_RADIO_MIN_SECONDS,
    DEFAULT_SCROBBLE_PERCENT,
    DEFAULT_SCROBBLE_SECONDS,
    DEFAULT_STRIP_YEAR,
    SUBMIT_TIMEOUT,
    SUBMISSION_CLIENT,
    TICK_SECONDS,
    VERSION,
)
from .helpers import (
    as_float as _as_float,
    as_text as _as_text,
    build_payload,
    build_submit_url,
    clean_title,
    is_radio,
    normalise_artist,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class _Track:
    """A track currently playing on one media player."""

    entity_id: str
    player_name: str
    title: str
    artist: str
    album: str | None
    duration: float | None
    content_id: str | None
    is_radio: bool
    started_at: float
    played: float = 0.0
    last_seen: float | None = None
    last_position: float | None = None
    scrobbled: bool = False
    now_playing_sent: bool = False

    @property
    def identity(self) -> tuple[str, str]:
        """Return the identity used to detect a track change."""
        return (self.title.casefold(), self.artist.casefold())


class ListenBrainzScrobbler:
    """Submit listens from selected media players to a ListenBrainz endpoint."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialise the scrobbler from a merged config entry."""
        self.hass = hass
        self._session = async_get_clientsession(hass)
        self._submit_url = build_submit_url(config[CONF_URL])
        self._token = config[CONF_TOKEN]
        self._players: list[str] = list(config.get(CONF_PLAYERS) or [])
        self._scrobble_percent = float(
            config.get(CONF_SCROBBLE_PERCENT, DEFAULT_SCROBBLE_PERCENT)
        )
        self._radio_min_seconds = float(
            config.get(CONF_RADIO_MIN_SECONDS, DEFAULT_RADIO_MIN_SECONDS)
        )
        self._now_playing = bool(config.get(CONF_NOW_PLAYING, DEFAULT_NOW_PLAYING))
        self._strip_year = bool(config.get(CONF_STRIP_YEAR, DEFAULT_STRIP_YEAR))
        self._tracks: dict[str, _Track] = {}
        self._unsubscribers: list[Callable[[], None]] = []

    @callback
    def async_start(self) -> None:
        """Start watching the configured players."""
        if not self._players:
            _LOGGER.warning("No media players configured; nothing will be scrobbled")
            return

        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass, self._players, self._handle_state_event
            )
        )
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass, self._async_tick, timedelta(seconds=TICK_SECONDS)
            )
        )

    @callback
    def async_stop(self) -> None:
        """Stop watching players and forget tracked state."""
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        self._tracks.clear()

    # --- event handling -------------------------------------------------

    @callback
    def _handle_state_event(self, event: Event) -> None:
        """React to a player changing track, play state, or metadata."""
        entity_id: str = event.data["entity_id"]
        new_state: State | None = event.data.get("new_state")
        if new_state is None:
            self._tracks.pop(entity_id, None)
            return

        old_state: State | None = event.data.get("old_state")
        if (
            old_state is not None
            and old_state.state == new_state.state
            and _metadata_identity(old_state) == _metadata_identity(new_state)
        ):
            return

        self.hass.async_create_task(self._async_evaluate(entity_id, new_state))

    async def _async_tick(self, _now: datetime) -> None:
        """Check every player for progress and due scrobbles."""
        for entity_id in self._players:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            try:
                await self._async_evaluate(entity_id, state)
            except Exception:  # noqa: BLE001 - one bad player must not stop the rest
                _LOGGER.exception("Error while processing %s", entity_id)

    async def _async_evaluate(self, entity_id: str, state: State) -> None:
        """Update the tracked track for one player and scrobble when due."""
        now = time.time()
        track = self._tracks.get(entity_id)

        if state.state != STATE_PLAYING:
            if track is not None:
                track.last_seen = None
            return

        raw_title = _as_text(state.attributes.get("media_title"))
        raw_artist = _as_text(state.attributes.get("media_artist"))
        if not raw_title or not raw_artist:
            # Station adverts and links often drop metadata; do not accrue
            # listening time for them.
            if track is not None:
                track.last_seen = None
            return

        artist = normalise_artist(raw_artist, state.attributes)
        raw_album = _as_text(state.attributes.get("media_album_name"))
        duration = _as_float(state.attributes.get("media_duration"))
        if duration is not None and duration <= 0:
            duration = None
        content_id = _as_text(state.attributes.get("media_content_id"))
        position = _current_position(state)
        radio = is_radio(content_id, duration)

        title = raw_title
        if radio and self._strip_year:
            title = clean_title(raw_title)

        # For radio, Music Assistant puts the station name in the album and
        # reports a meaningless duration. Drop both so the station is not
        # scrobbled as the album.
        album = None if radio else raw_album
        track_duration = None if radio else duration

        identity = (title.casefold(), artist.casefold())

        if (
            track is None
            or track.identity != identity
            or (not track.is_radio and _restarted(track, position))
        ):
            previous = track
            new_track = _Track(
                entity_id=entity_id,
                player_name=state.name,
                title=title,
                artist=artist,
                album=album,
                duration=track_duration,
                content_id=content_id,
                is_radio=radio,
                started_at=now,
                last_seen=now,
                last_position=position,
            )
            self._tracks[entity_id] = new_track
            _LOGGER.debug(
                "Tracking '%s' by '%s' on %s (radio=%s)",
                title,
                artist,
                state.name,
                radio,
            )

            if previous is not None:
                # Do not block the new track on a network call; the previous
                # track is already marked scrobbled when it is submitted.
                self.hass.async_create_task(self._async_finalise(previous))

            await self._async_send_playing_now(new_track)
            return

        if track.last_seen is not None:
            elapsed = now - track.last_seen
            track.played += max(0.0, min(elapsed, TICK_SECONDS * 3))
        track.last_seen = now
        if position is not None:
            track.last_position = position
        # A track can briefly report no duration before its metadata loads.
        # Correct the radio guess once a real duration appears.
        if track.is_radio and not radio:
            track.is_radio = False
        if not track.is_radio:
            if track.album is None and raw_album is not None:
                track.album = raw_album
            if track.duration is None and duration is not None:
                track.duration = duration

        await self._async_maybe_scrobble(track)

    # --- scrobbling -----------------------------------------------------

    async def _async_finalise(self, track: _Track) -> None:
        """Scrobble a track that ended just after crossing its threshold."""
        if track.scrobbled:
            return

        # Fold in the time since the last check: a track can change between
        # ticks, and that final slice still counts towards the threshold.
        if track.last_seen is not None:
            elapsed = max(
                0.0, min(time.time() - track.last_seen, TICK_SECONDS * 3)
            )
            track.played += elapsed
            if track.last_position is not None:
                track.last_position += elapsed

        if track.is_radio:
            if track.played >= self._radio_min_seconds:
                await self._async_scrobble(track)
            return

        await self._async_maybe_scrobble(track)

    async def _async_maybe_scrobble(self, track: _Track) -> None:
        """Scrobble the track if its listening threshold has been reached."""
        if track.scrobbled:
            return

        if track.is_radio:
            if track.played >= self._radio_min_seconds:
                await self._async_scrobble(track)
            return

        if not track.duration:
            return

        threshold = min(
            track.duration * self._scrobble_percent / 100,
            DEFAULT_SCROBBLE_SECONDS,
        )
        listened = track.last_position
        if listened is None:
            listened = track.played
        if listened >= threshold:
            await self._async_scrobble(track)

    async def _async_scrobble(self, track: _Track) -> None:
        """Submit one completed listen."""
        # Mark first: an HTTP timeout must not cause the same play to be sent
        # again on the next tick.
        track.scrobbled = True

        payload = [
            build_payload("single", self._track_metadata(track), track.started_at)
        ]
        if await self._async_submit("single", payload):
            _LOGGER.info(
                "Scrobbled '%s' by '%s' from %s",
                track.title,
                track.artist,
                track.player_name,
            )
        else:
            _LOGGER.warning(
                "Failed to scrobble '%s' by '%s' from %s",
                track.title,
                track.artist,
                track.player_name,
            )

    async def _async_send_playing_now(self, track: _Track) -> None:
        """Send a playing-now update once per track."""
        if not self._now_playing or track.now_playing_sent:
            return
        track.now_playing_sent = True
        await self._async_submit(
            "playing_now",
            [build_payload("playing_now", self._track_metadata(track))],
        )

    def _track_metadata(self, track: _Track) -> dict[str, Any]:
        """Build the ListenBrainz track_metadata object for a track."""
        metadata: dict[str, Any] = {
            "artist_name": track.artist,
            "track_name": track.title,
        }
        if track.album:
            metadata["release_name"] = track.album

        additional_info: dict[str, Any] = {
            "media_player": track.player_name,
            "submission_client": SUBMISSION_CLIENT,
            "submission_client_version": VERSION,
        }
        if track.duration:
            additional_info["duration_ms"] = int(track.duration * 1000)
        metadata["additional_info"] = additional_info
        return metadata

    async def _async_submit(
        self, listen_type: str, payload: list[dict[str, Any]]
    ) -> bool:
        """POST a listen payload to the configured endpoint."""
        headers = {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
        }
        try:
            async with self._session.post(
                self._submit_url,
                json={"listen_type": listen_type, "payload": payload},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=SUBMIT_TIMEOUT),
            ) as response:
                body = await response.text()
                _LOGGER.debug(
                    "ListenBrainz response HTTP %s: %s",
                    response.status,
                    body[:300],
                )
                if response.status >= 400:
                    _LOGGER.warning(
                        "ListenBrainz endpoint returned HTTP %s: %s",
                        response.status,
                        body[:300],
                    )
                    return False
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.warning("Could not reach the ListenBrainz endpoint: %s", err)
            return False
        return True


def _metadata_identity(state: State) -> tuple[str | None, str | None]:
    """Return the raw title/artist pair used to filter state events."""
    return (
        _as_text(state.attributes.get("media_title")),
        _as_text(state.attributes.get("media_artist")),
    )


def _current_position(state: State) -> float | None:
    """Return the current playback position in seconds, if known."""
    position = _as_float(state.attributes.get("media_position"))
    if position is None:
        return None

    updated = state.attributes.get("media_position_updated_at")
    updated_at: datetime | None = None
    if isinstance(updated, datetime):
        updated_at = updated
    elif isinstance(updated, (int, float)):
        updated_at = dt_util.utc_from_timestamp(updated)

    if updated_at is not None:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=dt_util.UTC)
        elapsed = (dt_util.utcnow() - updated_at).total_seconds()
        # Ignore stale timestamps (paused players, restarted integrations).
        if 0 < elapsed < 300:
            position += elapsed
    return max(0.0, position)


def _restarted(track: _Track, position: float | None) -> bool:
    """Return True when the same track has restarted from the beginning."""
    if position is None or track.last_position is None:
        return False
    return position + 10 < track.last_position and track.last_position > 20
