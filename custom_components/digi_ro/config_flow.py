"""Config flow for Digi Romania (email/password + SMS/email 2FA)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    DigiApiClient,
    DigiAuthError,
    DigiError,
    DigiTwoFactorError,
    DigiTwoFactorRequired,
)
from .const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_LABEL,
    CONF_COOKIES,
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
)
from .models import TwoFactorContext

_LOGGER = logging.getLogger(__name__)


class DigiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Digi Romania config and re-auth flows."""

    VERSION = 1

    def __init__(self) -> None:
        self._client: DigiApiClient | None = None
        self._email: str = ""
        self._password: str = ""
        self._twofa_ctx: TwoFactorContext | None = None
        self._twofa_choice: tuple[str, str | None] | None = None
        self._address_options: list[Any] = []
        self._reauth_entry: ConfigEntry | None = None

    # -- Step 1: credentials --------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._password = user_input[CONF_PASSWORD]
            self._client = DigiApiClient(async_create_clientsession(self.hass))
            try:
                final_url, html = await self._client.begin_login(self._email, self._password)
            except DigiAuthError:
                errors["base"] = "invalid_auth"
            except DigiError:
                errors["base"] = "cannot_connect"
            else:
                if "/auth/2fa" in final_url:
                    try:
                        self._twofa_ctx = await self._client.get_2fa_context(html)
                    except DigiTwoFactorRequired:
                        errors["base"] = "twofa_parse"
                    else:
                        return await self._start_twofa()
                else:
                    addr_html = html if "address-select" in final_url else None
                    return await self._proceed_to_address(addr_html)

        default_email = self._reauth_entry.data[CONF_EMAIL] if self._reauth_entry else ""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=default_email): str,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    # -- Step 2: 2FA method/target selection ---------------------------------

    def _twofa_choices(self) -> dict[str, str]:
        choices: dict[str, str] = {}
        assert self._twofa_ctx is not None
        for name, method in self._twofa_ctx.methods.items():
            if name == "sms":
                targets = method.get("target_options") or []
                if targets:
                    for t in targets:
                        choices[f"sms::{t['value']}"] = f"SMS: {t['label']}"
                else:
                    choices["sms::"] = "Cod prin SMS"
            elif name == "email":
                choices["email::"] = "Cod prin e-mail"
        return choices

    async def _start_twofa(self) -> ConfigFlowResult:
        choices = self._twofa_choices()
        if len(choices) == 1:
            key = next(iter(choices))
            return await self._send_and_continue(key)
        return await self.async_step_twofa()

    async def async_step_twofa(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        choices = self._twofa_choices()
        if user_input is not None:
            return await self._send_and_continue(user_input["choice"], errors)

        options = [SelectOptionDict(value=k, label=v) for k, v in choices.items()]
        return self.async_show_form(
            step_id="twofa",
            data_schema=vol.Schema(
                {
                    vol.Required("choice"): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
            errors=errors,
        )

    async def _send_and_continue(
        self, choice: str, errors: dict[str, str] | None = None
    ) -> ConfigFlowResult:
        method, _, target = choice.partition("::")
        target = target or None
        assert self._client is not None and self._twofa_ctx is not None
        try:
            await self._client.send_2fa_code(self._twofa_ctx, method, target)
        except DigiTwoFactorError:
            errors = errors if errors is not None else {}
            errors["base"] = "twofa_send"
            return await self.async_step_twofa()
        self._twofa_choice = (method, target)
        return await self.async_step_code()

    # -- Step 3: 2FA code -----------------------------------------------------

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._client is not None and self._twofa_ctx is not None and self._twofa_choice
            method, _target = self._twofa_choice
            try:
                _url, html = await self._client.validate_2fa_code(
                    self._twofa_ctx, method, user_input["code"]
                )
            except DigiTwoFactorError:
                errors["base"] = "twofa_invalid"
            except DigiError:
                errors["base"] = "cannot_connect"
            else:
                return await self._proceed_to_address(html)

        return self.async_show_form(
            step_id="code",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )

    # -- Step 4: address selection -------------------------------------------

    async def _proceed_to_address(self, html: str | None) -> ConfigFlowResult:
        assert self._client is not None
        try:
            options = await self._client.get_address_options(html)
        except DigiError:
            options = []

        if len(options) <= 1:
            chosen = options[0] if options else None
            if chosen and chosen.value:
                try:
                    await self._client.confirm_address(chosen.value)
                except DigiError:
                    pass
            return await self._finish(
                chosen.value if chosen else None, chosen.label if chosen else None
            )

        self._address_options = options
        return await self.async_step_address()

    async def async_step_address(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._client is not None
            value = user_input["address"]
            label = next(
                (o.label for o in self._address_options if o.value == value), value
            )
            try:
                await self._client.confirm_address(value)
            except DigiError:
                errors["base"] = "cannot_connect"
            else:
                return await self._finish(value, label)

        options = [
            SelectOptionDict(value=o.value, label=o.label)
            for o in self._address_options
            if o.value
        ]
        return self.async_show_form(
            step_id="address",
            data_schema=vol.Schema(
                {
                    vol.Required("address"): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
            errors=errors,
        )

    # -- Finish ---------------------------------------------------------------

    async def _finish(
        self, address_id: str | None, address_label: str | None
    ) -> ConfigFlowResult:
        assert self._client is not None
        # Confirm the session actually reaches invoices before saving.
        try:
            await self._client.async_fetch_data(1)
        except DigiError:
            return self.async_abort(reason="cannot_connect")

        data = {
            CONF_EMAIL: self._email,
            CONF_PASSWORD: self._password,
            CONF_COOKIES: self._client.export_cookies(),
            CONF_ADDRESS_ID: address_id,
            CONF_ADDRESS_LABEL: address_label,
        }

        await self.async_set_unique_id(self._email.lower())

        if self._reauth_entry is not None:
            # A re-auth must land on the same Digi account. Without this the
            # entry keeps its old title and unique id while the entities start
            # reporting a different account's invoices.
            self._abort_if_unique_id_mismatch()
            return self.async_update_reload_and_abort(self._reauth_entry, data_updates=data)

        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._email, data=data)

    # -- Re-auth --------------------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._reauth_entry = self._get_reauth_entry()
        return await self.async_step_user()
