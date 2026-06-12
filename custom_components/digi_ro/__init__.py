"""The Digi Romania integration.

Author: PanTeraS
Login/2FA/parsing adapted from HAForgeLabs/utilitati_romania (MIT).
"""

from __future__ import annotations

__author__ = "PanTeraS"

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import DigiApiClient
from .coordinator import DigiDataCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]

type DigiConfigEntry = ConfigEntry[DigiDataCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: DigiConfigEntry) -> bool:
    """Set up Digi Romania from a config entry."""
    # Isolated session: Digi's cookies must never touch HA's shared jar.
    client = DigiApiClient(async_create_clientsession(hass))
    coordinator = DigiDataCoordinator(hass, entry, client)

    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DigiConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
