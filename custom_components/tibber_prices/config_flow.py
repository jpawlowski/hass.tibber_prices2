"""Adds config flow for Tibber Prices."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    TibberPricesApiClient,
    TibberPricesApiClientAuthenticationError,
    TibberPricesApiClientCommunicationError,
    TibberPricesApiClientError,
)
from .const import (
    CONF_FETCH_MODE,
    CONF_HOME_ID,
    CONF_HOME_NAME,
    CONF_PRICE_UNIT,
    CONF_SCAN_INTERVAL,
    DEFAULT_FETCH_MODE,
    DEFAULT_PRICE_UNIT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    FETCH_MODE_ACTIVE,
    FETCH_MODE_AGGRESSIVE,
    FETCH_MODE_AUTO,
    FETCH_MODE_CONSERVATIVE,
)


async def validate_api_token(hass: HomeAssistant, access_token: str) -> dict[str, Any]:
    """Validate the API token by making a request to the Tibber API."""
    client = TibberPricesApiClient(
        access_token=access_token,
        session=async_get_clientsession(hass),
    )
    return await client.async_get_user_info()


class TibberPricesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tibber Prices."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._user_data: dict[str, Any] = {}
        self._homes: list[dict[str, Any]] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                user_data = await validate_api_token(self.hass, user_input[CONF_ACCESS_TOKEN])

                # Check if there are homes in the user's account
                if "viewer" not in user_data or "homes" not in user_data["viewer"] or not user_data["viewer"]["homes"]:
                    errors["base"] = "no_homes"
                else:
                    self._user_data = user_input
                    self._homes = user_data["viewer"]["homes"]

                    # Set a unique ID to prevent duplicate entries
                    user_id = user_data["viewer"]["userId"]
                    await self.async_set_unique_id(user_id)
                    self._abort_if_unique_id_configured()

                    # For now, create the entry directly with the first home if available
                    main_entry_data = {
                        CONF_ACCESS_TOKEN: self._user_data[CONF_ACCESS_TOKEN],
                    }

                    # Add the first home's ID if available
                    if self._homes and len(self._homes) > 0:
                        home = self._homes[0]
                        home_id = home["id"]
                        if home.get("appNickname"):
                            home_name = home["appNickname"]
                        elif "address" in home and home["address"].get("address1"):
                            home_name = home["address"]["address1"]
                        else:
                            home_name = home_id

                        main_entry_data[CONF_HOME_ID] = home_id
                        main_entry_data[CONF_HOME_NAME] = home_name

                    # Set default options
                    options = {
                        CONF_FETCH_MODE: DEFAULT_FETCH_MODE,
                        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                        CONF_PRICE_UNIT: DEFAULT_PRICE_UNIT,
                    }

                    # Create the entry
                    title = f"Tibber Prices ({user_id})"
                    return self.async_create_entry(title=title, data=main_entry_data, options=options)

            except TibberPricesApiClientAuthenticationError:
                errors["base"] = "auth"
            except TibberPricesApiClientCommunicationError:
                errors["base"] = "connection"
            except TibberPricesApiClientError:
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCESS_TOKEN): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return TibberPricesOptionsFlow(config_entry)


class TibberPricesOptionsFlow(OptionsFlow):
    """Handle options for the Tibber Prices integration."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle options flow."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options = self.config_entry.options
        fetch_mode = options.get(CONF_FETCH_MODE, DEFAULT_FETCH_MODE)
        scan_interval = options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        price_unit = options.get(CONF_PRICE_UNIT, DEFAULT_PRICE_UNIT)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_FETCH_MODE, default=fetch_mode): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=FETCH_MODE_AUTO,
                                    label="Auto (time-based)",
                                ),
                                selector.SelectOptionDict(
                                    value=FETCH_MODE_CONSERVATIVE,
                                    label="Conservative (minimal API calls)",
                                ),
                                selector.SelectOptionDict(
                                    value=FETCH_MODE_ACTIVE,
                                    label="Active (periodic checking)",
                                ),
                                selector.SelectOptionDict(
                                    value=FETCH_MODE_AGGRESSIVE,
                                    label="Aggressive (frequent checking)",
                                ),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_SCAN_INTERVAL, default=scan_interval): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=15,
                            max=60,
                            step=15,
                            unit_of_measurement="minutes",
                            mode=selector.NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(CONF_PRICE_UNIT, default=price_unit): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(value="kWh", label="kWh"),
                                selector.SelectOptionDict(value="MWh", label="MWh"),
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )
