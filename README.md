# ListenBrainz Scrobbler

Custom Home Assistant integration that submits listens from selected
`media_player` entities to a ListenBrainz-compatible endpoint. It was built for
[multi-scrobbler](https://docs.multi-scrobbler.app/configuration/sources/listenbrainz-endpoint/),
which emulates the ListenBrainz API so it can forward scrobbles to Last.fm,
ListenBrainz, Maloja and the rest of its clients.

## Setup

1. Copy/keep this folder at `custom_components/listenbrainz_scrobbler/`.
2. **Restart Home Assistant** — integration code is only loaded at startup.
3. Go to **Settings → Devices & Services → Add Integration** and search for
   **ListenBrainz Scrobbler**.
4. Enter a name, the server URL and the token configured on the
   multi-scrobbler ListenBrainz endpoint.
5. On the next page tick the media players to watch and adjust the scrobble
   settings.

## Server URL

multi-scrobbler has two ListenBrainz entry points. Either form is accepted:

| Mode | What to enter | What is posted to |
|---|---|---|
| Standard (recommended) | `http://192.168.1.23:9078` | `http://192.168.1.23:9078/1/submit-listens` |
| Slug | `http://192.168.1.23:9078/api/listenbrainz/<slug>` | `.../<slug>/submit-listens` |
| Full path | `http://192.168.1.23:9078/1/submit-listens` | used unchanged |

The token is sent as `Authorization: Token <token>`; it must match the
`LZE_TOKEN` (or `token`) configured on the multi-scrobbler source.

## Behaviour

- **Tracks with a known duration** are scrobbled at the configured percentage
  (default 50%), or after 4 minutes, whichever comes first.
- **Radio / streams** (`media_duration` 0 or unknown, or a `...://radio/...`
  content id) have no reliable duration, so they are scrobbled after the
  configured radio time (default 30 seconds) of continuous playback.
- **Now playing** is sent once per track when enabled.
- **Year cleanup** applies only to radio: a trailing `(89)` or `(1989)` is
  removed, so `Lonely (89)` is submitted as `Lonely`.
- Music Assistant radio puts the station name in `media_album_name`, so the
  album is dropped for radio listens instead of scrobbling the station as the
  album.
- Music Assistant slash-joined artists (`Artist A/Artist B`) are reduced to the
  first artist; `AC/DC` is left alone.

A play is marked as submitted *before* the HTTP request, so a timeout cannot
produce duplicate scrobbles on the next check. The trade-off is that a failed
request loses that one play; the warning is in the log.

## Notes

- Each entry has a unique id derived from URL + token, so the same
  multi-scrobbler server can be used with more than one token.
- Options (players, URL, token, thresholds, year cleanup) can be edited from
  the integration's **Configure** button; the entry reloads automatically.
- Watching is event-driven, with a 10-second progress check while players are
  playing.
- Select **one entity per physical player**. If you select both the Music
  Assistant player and the Sonos/native player for the same speaker, every play
  is submitted twice.
