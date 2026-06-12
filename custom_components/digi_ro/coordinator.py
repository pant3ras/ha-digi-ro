"""Data update coordinator for Digi Romania."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DigiApiClient, DigiError, DigiReauthRequired
from .const import CONF_COOKIES, CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT, DEFAULT_SCAN_INTERVAL, DOMAIN
from .models import DigiData

_LOGGER = logging.getLogger(__name__)


class DigiDataCoordinator(DataUpdateCoordinator[DigiData]):
    """Polls Digi My Account invoices on a schedule."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: DigiApiClient) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=DEFAULT_SCAN_INTERVAL)
        self.entry = entry
        self.client = client
        self._history_limit = entry.data.get(CONF_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT)
        # Resume the persisted session so a restart doesn't force a re-login.
        client.import_cookies(entry.data.get(CONF_COOKIES))

    async def _async_update_data(self) -> DigiData:
        try:
            data = await self.client.async_fetch_data(self._history_limit)
        except DigiReauthRequired as err:
            # The session died; 2FA blocks a silent re-login, so prompt re-auth.
            raise ConfigEntryAuthFailed(str(err)) from err
        except DigiError as err:
            raise UpdateFailed(str(err)) from err

        self._persist_cookies()
        return data

    def _persist_cookies(self) -> None:
        """Save the rotated cookie jar so the session survives restarts."""
        cookies = self.client.export_cookies()
        if cookies and cookies != self.entry.data.get(CONF_COOKIES):
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, CONF_COOKIES: cookies}
            )
