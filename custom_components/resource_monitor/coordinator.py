"""Data coordinator for the System Monitor integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, MIN_SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class MonitorData:
    """Snapshot from the monitor."""

    system: dict = field(default_factory=dict)
    containers: list[dict] = field(default_factory=list)
    container_error: str | None = None


async def _json_or_none(resp: aiohttp.ClientResponse) -> object | None:
    """Decode a JSON body, tolerating non-JSON error pages."""
    try:
        return await resp.json(content_type=None)
    except (ValueError, aiohttp.ContentTypeError):
        return None


class ResourceMonitorCoordinator(DataUpdateCoordinator[MonitorData]):
    """Polls /api/system and /api/containers on the monitor."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # Token and scan interval can be overridden from the options flow.
        conf = {**entry.data, **entry.options}
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}.{entry.entry_id}",
            # Container stats are expensive on the monitor (one daemon stats
            # call per running container), so the floor is deliberately high.
            update_interval=timedelta(
                seconds=max(
                    MIN_SCAN_INTERVAL,
                    int(conf.get("scan_interval", DEFAULT_SCAN_INTERVAL)),
                )
            ),
        )
        self.url = str(conf["url"]).rstrip("/")
        self.token = conf.get("token") or None

    async def _async_get(
        self, session: aiohttp.ClientSession, path: str
    ) -> tuple[int, object | None]:
        """GET a monitor path, retrying with ?token= if headers get stripped.

        The Authorization header is tried first because it keeps the token out
        of URLs. Some reverse proxies drop it, so the dashboard's own
        ?token=<token> scheme is used as a fallback for 401/403 responses.
        """
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        url = f"{self.url}{path}"
        async with session.get(url, headers=headers) as resp:
            status = resp.status
            body = await _json_or_none(resp)
        if status in (401, 403) and self.token:
            async with session.get(url, params={"token": self.token}) as retry:
                return retry.status, await _json_or_none(retry)
        return status, body

    async def _async_update_data(self) -> MonitorData:
        """Fetch system metrics, keeping last-known containers on failure."""
        timeout = aiohttp.ClientTimeout(total=15)
        previous = self.data

        async with aiohttp.ClientSession(timeout=timeout) as session:
            try:
                status, system = await self._async_get(session, "/api/system")
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise UpdateFailed(
                    f"Cannot reach monitor at {self.url}: {err}"
                ) from err
            if status in (401, 403):
                raise ConfigEntryAuthFailed(
                    "The monitor rejected the token "
                    f"(HTTP {status}); update the token in the integration."
                )
            if status != 200 or not isinstance(system, dict):
                raise UpdateFailed(
                    f"Monitor at {self.url} returned HTTP {status}"
                )

            # Docker may be down while the host itself is fine: keep the
            # last snapshot and surface the error on a dedicated sensor.
            containers = list(previous.containers) if previous else []
            container_error = previous.container_error if previous else None

            try:
                cstatus, payload = await self._async_get(
                    session, "/api/containers"
                )
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                container_error = f"Docker stats unavailable: {err}"
            else:
                if cstatus in (401, 403):
                    raise ConfigEntryAuthFailed(
                        "The monitor rejected the token for container stats "
                        f"(HTTP {cstatus})."
                    )
                if cstatus == 200 and isinstance(payload, dict):
                    if payload.get("error"):
                        container_error = str(payload["error"])
                    else:
                        containers = list(payload.get("containers") or [])
                        container_error = None
                elif cstatus == 200 and isinstance(payload, list):
                    containers = payload
                    container_error = None
                else:
                    container_error = f"Docker stats unavailable (HTTP {cstatus})"

        return MonitorData(
            system=system,
            containers=containers,
            container_error=container_error,
        )
