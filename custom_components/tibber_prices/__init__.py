"""
Custom integration to integrate tibber_prices with Home Assistant.

For more details about this integration, please refer to
https://github.com/jpawlowski/tibber_prices2
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_change
from homeassistant.loader import async_get_loaded_integration

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .api import TibberPricesApiClient
from .const import (
    CONF_HOME_ID,
    DOMAIN,
    LOGGER,
)
from .coordinator import TibberPricesDataUpdateCoordinator
from .data import TibberPricesData

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.helpers.typing import ConfigType

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
]


async def async_setup(_hass: HomeAssistant, _config: ConfigType) -> bool:
    """Set up the Tibber Prices component from YAML."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    hass.data.setdefault(DOMAIN, {})

    # Create API client using access token
    client = TibberPricesApiClient(
        access_token=entry.data[CONF_ACCESS_TOKEN],
        session=async_get_clientsession(hass),
    )

    # Create coordinator
    coordinator = TibberPricesDataUpdateCoordinator(
        hass=hass,
        client=client,
        home_id=entry.data.get(CONF_HOME_ID),
    )

    # Create runtime data
    runtime_data = TibberPricesData(
        client=client,
        integration=async_get_loaded_integration(hass, entry.domain),
        coordinator=coordinator,
    )

    # Store runtime data in hass.data
    hass.data[DOMAIN][entry.entry_id] = runtime_data

    # Set config entry reference in coordinator
    coordinator.config_entry = entry

    # Initialize the coordinator - load cached data before first refresh
    await coordinator.async_initialize()

    # Create listener for midnight transition
    @callback
    def _handle_midnight(_: datetime) -> None:
        """Handle midnight transition."""
        LOGGER.debug("Handling midnight data transition")
        coordinator.async_handle_midnight_transition()

    # Register midnight listener
    remove_midnight_listener = async_track_time_change(hass, _handle_midnight, hour=0, minute=0, second=0)

    # Initial data fetch - will only call API if needed
    await coordinator.async_config_entry_first_refresh()

    # Forward the setup to each platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register cleanup listeners
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    entry.async_on_unload(remove_midnight_listener)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    # Clean up any scheduled tasks in the coordinator
    if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        runtime_data = hass.data[DOMAIN][entry.entry_id]
        coordinator = runtime_data.coordinator

        # Call cancel_scheduled_updates method if it exists
        if hasattr(coordinator, "cancel_scheduled_updates"):
            coordinator.cancel_scheduled_updates()

        # Remove the data from hass.data
        hass.data[DOMAIN].pop(entry.entry_id)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
