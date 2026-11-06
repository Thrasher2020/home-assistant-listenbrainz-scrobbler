"""Config flow for the ListenBrainz Scrobbler integration."""

from __future__ import annotations

import hashlib
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

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
    DEFAULT_STRIP_YEAR,
    DOMAIN,
    NAME,
)
from .helpers import normalise_url


def _player_options(hass, current: list[str]) -> list[SelectOptionDict]:
    """Build a tick-list of every media_player, keeping saved entries."""
    players: list[tuple[str, str]] = [
        (state.entity_id, state.name or state.entity_id)
        for state in hass.states.async_all("media_player")
    ]

    # Keep a previously selected player even if it has no state right now
    # (for example an unavailable or temporarily removed speaker).
    known = {entity_id for entity_id, _ in players}
    for entity_id in current or []:
        if entity_id not in known:
            players.append((entity_id, entity_id))

    # Friendly names are not unique in this installation, so disambiguate.
    label_counts: dict[str, int] = {}
    for _, label in players:
        label_counts[label] = label_counts.get(label, 0) + 1

    options = [
        SelectOptionDict(
            value=entity_id,
            label=(
                f"{label} ({entity_id})"
                if label_counts[label] > 1
                else label
            ),
        )
        for entity_id, label in players
    ]
    options.sort(key=lambda option: option["label"].casefold())
    return options


def _player_selector(hass, current: list[str]):
    """Return a tick-list selector, or an entity picker if there are none."""
    options = _player_options(hass, current)
    if not options:
        return EntitySelector(
            EntitySelectorConfig(domain="media_player", multiple=True)
        )
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=SelectSelectorMode.LIST,
        )
    )


def _behaviour_schema(
    hass, defaults: dict[str, Any] | None = None
) -> vol.Schema:
    """Build the media-player and scrobble-behaviour fields."""
    defaults = defaults or {}
    current_players = defaults.get(CONF_PLAYERS) or []
    return vol.Schema(
        {
            vol.Required(
                CONF_PLAYERS, default=current_players
            ): _player_selector(hass, current_players),
            vol.Required(
                CONF_SCROBBLE_PERCENT,
                default=defaults.get(
                    CONF_SCROBBLE_PERCENT, DEFAULT_SCROBBLE_PERCENT
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=100,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="%",
                )
            ),
            vol.Required(
                CONF_RADIO_MIN_SECONDS,
                default=defaults.get(
                    CONF_RADIO_MIN_SECONDS, DEFAULT_RADIO_MIN_SECONDS
                ),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=5,
                    max=600,
                    step=5,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_NOW_PLAYING,
                default=defaults.get(CONF_NOW_PLAYING, DEFAULT_NOW_PLAYING),
            ): bool,
            vol.Required(
                CONF_STRIP_YEAR,
                default=defaults.get(CONF_STRIP_YEAR, DEFAULT_STRIP_YEAR),
            ): bool,
        }
    )


def _connection_schema(defaults: dict[str, Any] | None = None) -> dict[Any, Any]:
    """Build the name, URL and token fields."""
    defaults = defaults or {}
    return {
        vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, NAME)): TextSelector(
            TextSelectorConfig(type=TextSelectorType.TEXT)
        ),
        vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
        vol.Required(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }


def _validate_connection(user_input: dict[str, Any]) -> dict[str, Any]:
    """Validate and clean the connection fields in place."""
    user_input[CONF_URL] = normalise_url(user_input[CONF_URL])
    token = user_input[CONF_TOKEN].strip()
    if not token:
        raise ValueError("token")
    user_input[CONF_TOKEN] = token
    user_input[CONF_NAME] = user_input[CONF_NAME].strip() or NAME
    return user_input


class ListenBrainzScrobblerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._connection: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the server URL and token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                connection = _validate_connection(dict(user_input))
            except ValueError as err:
                if str(err).startswith("URL"):
                    errors[CONF_URL] = "invalid_url"
                else:
                    errors[CONF_TOKEN] = "invalid_token"
            else:
                unique_id = hashlib.sha256(
                    f"{connection[CONF_URL]}\0{connection[CONF_TOKEN]}".encode()
                ).hexdigest()[:32]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                self._connection = connection
                return await self.async_step_behaviour()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(_connection_schema(user_input)),
            errors=errors,
        )

    async def async_step_behaviour(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose players and scrobble behaviour, then create the entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not user_input.get(CONF_PLAYERS):
                errors[CONF_PLAYERS] = "no_players"
            else:
                return self.async_create_entry(
                    title=self._connection[CONF_NAME],
                    data={**self._connection, **user_input},
                )

        return self.async_show_form(
            step_id="behaviour",
            data_schema=_behaviour_schema(self.hass, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return ListenBrainzScrobblerOptionsFlow()


class ListenBrainzScrobblerOptionsFlow(OptionsFlow):
    """Edit the connection and scrobble behaviour."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the options form."""
        errors: dict[str, str] = {}
        config = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            if not user_input.get(CONF_PLAYERS):
                errors[CONF_PLAYERS] = "no_players"
            else:
                try:
                    _validate_connection(user_input)
                except ValueError as err:
                    if str(err).startswith("URL"):
                        errors[CONF_URL] = "invalid_url"
                    else:
                        errors[CONF_TOKEN] = "invalid_token"
                else:
                    return self.async_create_entry(data=user_input)

        form_defaults = user_input or config
        schema: dict[Any, Any] = _connection_schema(form_defaults)
        schema.update(_behaviour_schema(self.hass, form_defaults).schema)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            errors=errors,
        )
