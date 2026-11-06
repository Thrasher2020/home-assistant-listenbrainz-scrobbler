"""The ListenBrainz Scrobbler integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .scrobbler import ListenBrainzScrobbler


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a ListenBrainz scrobbler from a config entry."""
    config = {**entry.data, **entry.options}
    scrobbler = ListenBrainzScrobbler(hass, config)
    scrobbler.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = scrobbler
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and stop watching media players."""
    scrobbler: ListenBrainzScrobbler = hass.data[DOMAIN].pop(entry.entry_id)
    scrobbler.async_stop()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
