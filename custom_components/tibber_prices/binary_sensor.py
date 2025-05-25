"""Binary sensor platform for tibber_prices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import TibberPricesDataUpdateCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary_sensor platform."""
    # No entities to add at this time
    # Access coordinator correctly for future use
    runtime_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime_data.coordinator
