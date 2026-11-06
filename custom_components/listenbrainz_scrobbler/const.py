"""Constants for the ListenBrainz Scrobbler integration."""

DOMAIN = "listenbrainz_scrobbler"
NAME = "ListenBrainz Scrobbler"
VERSION = "1.0.0"

CONF_URL = "url"
CONF_TOKEN = "token"
CONF_PLAYERS = "players"
CONF_SCROBBLE_PERCENT = "scrobble_percent"
CONF_RADIO_MIN_SECONDS = "radio_min_seconds"
CONF_NOW_PLAYING = "now_playing"
CONF_STRIP_YEAR = "strip_year"

DEFAULT_SCROBBLE_PERCENT = 50
# Last.fm's upper bound: scrobble after 4 minutes regardless of track length.
DEFAULT_SCROBBLE_SECONDS = 240
DEFAULT_RADIO_MIN_SECONDS = 30
DEFAULT_NOW_PLAYING = True
DEFAULT_STRIP_YEAR = True

TICK_SECONDS = 10
SUBMIT_TIMEOUT = 10
SUBMISSION_CLIENT = "Home Assistant ListenBrainz Scrobbler"
