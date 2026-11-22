"""Config and options flow for the System Monitor integration."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    CONF_URL,
    CONFIG_FLOW_VERSION,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)


class MonitorError(HomeAssistantError):
    """Raised when the monitor cannot be reached or rejects the token."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def clean_url(value: Any) -> str:
    """Normalize a monitor URL: add a scheme, strip trailing slashes."""
    url = str(value).strip()
    if not url:
        raise vol.Invalid("empty URL")
    if "://" not in url:
        url = f"http://{url}"
    return url.rstrip("/")


def _user_schema() -> vol.Schema:
    # Keep this schema serializable: Home Assistant sends it to the frontend
    # through probatio, which cannot render bare callables. URL normalization
    # therefore happens in async_step_user instead of a vol.All validator.
    return vol.Schema(
        {
            vol.Required(
                CONF_URL, description={"suggest": "http://127.0.0.1:1010"}
            ): str,
            vol.Optional(CONF_TOKEN, default=""): str,
            # Plain int keeps the schema serializable for the frontend; the
            # step clamps it to MIN_SCAN_INTERVAL.
            vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
        }
    )


def _reauth_schema() -> vol.Schema:
    return vol.Schema({vol.Required(CONF_TOKEN): str})


def _options_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(
                CONF_TOKEN, default=current.get(CONF_TOKEN, "")
            ): str,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            ): int,
        }
    )


async def _async_validate(hass: HomeAssistant, url: str, token: str) -> None:
    """Connect to the monitor and verify the URL (and token, if set)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.get(f"{url}/api/system", headers=headers) as resp:
                status = resp.status
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, OSError) as err:
        raise MonitorError("cannot_connect") from err
    if status in (401, 403):
        raise MonitorError("invalid_auth")
    if status != 200:
        raise MonitorError("unknown")


def _merged(entry: ConfigEntry) -> dict[str, Any]:
    """Entry data with option overrides (token / interval live in options)."""
    return {**entry.data, **entry.options}


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a System Monitor instance."""

    VERSION = CONFIG_FLOW_VERSION

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First-time setup: URL, optional token, scan interval."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = clean_url(user_input[CONF_URL])
            except vol.Invalid:
                errors["base"] = "invalid_url"
            else:
                token = user_input.get(CONF_TOKEN, "").strip()
                try:
                    await _async_validate(self.hass, url, token)
                except MonitorError as err:
                    errors["base"] = err.reason
                else:
                    await self.async_set_unique_id(url)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=url,
                        data={
                            CONF_URL: url,
                            CONF_TOKEN: token,
                            CONF_SCAN_INTERVAL: max(
                                MIN_SCAN_INTERVAL, user_input[CONF_SCAN_INTERVAL]
                            ),
                        },
                    )
        return self.async_show_form(
            step_id="user", data_schema=_user_schema(), errors=errors
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """The monitor rejected the stored token; ask for a new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a new token for the existing entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            conf = _merged(entry)
            token = user_input[CONF_TOKEN].strip()
            try:
                await _async_validate(self.hass, conf[CONF_URL], token)
            except MonitorError as err:
                errors["base"] = err.reason
            else:
                await self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_TOKEN: token},
                    reason="reauth_successful",
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_reauth_schema(),
            errors=errors,
            description_placeholders={"url": entry.data.get(CONF_URL, "")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        entry: ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Edit the token and poll interval without re-adding the entry."""
        return MonitorOptionsFlow()


class MonitorOptionsFlow(config_entries.OptionsFlowWithReload):
    """Options flow: token and poll interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the new token, then save and reload the entry."""
        entry = self.config_entry
        conf = _merged(entry)
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input.get(CONF_TOKEN, "").strip()
            try:
                await _async_validate(self.hass, conf[CONF_URL], token)
            except MonitorError as err:
                errors["base"] = err.reason
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_TOKEN: token,
                        CONF_SCAN_INTERVAL: max(
                            MIN_SCAN_INTERVAL, user_input[CONF_SCAN_INTERVAL]
                        ),
                    },
                )
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(conf),
            errors=errors,
            description_placeholders={"url": conf.get(CONF_URL, "")},
        )
