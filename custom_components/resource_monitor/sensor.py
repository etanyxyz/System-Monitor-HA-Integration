"""Sensor platform for the System Monitor integration.

Static system sensors (CPU, load, memory, swap, uptime, boot time) plus
dynamic per-core and per-Docker-container sensors that follow the live
container list.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from urllib.parse import urlparse

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ResourceMonitorCoordinator

_LOGGER = logging.getLogger(__name__)


def _get(data: dict, *keys: str, default=None):
    """Walk a dict path, tolerating missing/None nodes."""
    node = data
    for key in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    return node if node is not None else default


def _num(value):
    """Coerce to float or None (the API returns null for stopped containers)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _device_identifiers(coordinator: ResourceMonitorCoordinator) -> set[tuple[str, str]]:
    return {(DOMAIN, coordinator.config_entry.unique_id)}


def _host_device_info(coordinator: ResourceMonitorCoordinator) -> dict:
    """Device entry shared by the system and per-core sensors."""
    netloc = urlparse(coordinator.url).netloc or coordinator.url
    return {
        "identifiers": _device_identifiers(coordinator),
        "name": f"System Monitor ({netloc})",
        "manufacturer": "etanyxyz",
        "model": "Resource Monitor",
        "configuration_url": coordinator.url,
    }


# ---------------------------------------------------------------------------
# Static system sensors
# ---------------------------------------------------------------------------


def _compute_cpu(system: dict):
    return _num(_get(system, "cpu", "overall_percent"))


def _compute_load(system: dict, index: int):
    load = _get(system, "cpu", "load_avg", default=[])
    return _num(load[index]) if isinstance(load, list) and index < len(load) else None


def _compute_mem(system: dict, key: str):
    return _num(_get(system, "memory", key))


def _compute_swap(system: dict, key: str):
    return _num(_get(system, "memory", key))


def _compute_uptime(system: dict):
    return _num(system.get("uptime_seconds"))


def _compute_boot_time(system: dict):
    stamp = _num(system.get("boot_time"))
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(stamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


SYSTEM_SENSOR_DEFS: tuple[dict, ...] = (
    {
        "key": "cpu",
        "name": "CPU usage",
        "compute": _compute_cpu,
        "native_unit_of_measurement": "%",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.cpu",
    },
    {
        "key": "load1",
        "name": "Load average (1m)",
        "compute": lambda s: _compute_load(s, 0),
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.counter",
    },
    {
        "key": "load5",
        "name": "Load average (5m)",
        "compute": lambda s: _compute_load(s, 1),
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.counter",
    },
    {
        "key": "load15",
        "name": "Load average (15m)",
        "compute": lambda s: _compute_load(s, 2),
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.counter",
    },
    {
        "key": "mem_total",
        "name": "Memory total",
        "compute": lambda s: _compute_mem(s, "total_bytes"),
        "native_unit_of_measurement": "B",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.memory",
    },
    {
        "key": "mem_used",
        "name": "Memory used",
        "compute": lambda s: _compute_mem(s, "used_bytes"),
        "native_unit_of_measurement": "B",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.memory",
    },
    {
        "key": "mem_available",
        "name": "Memory available",
        "compute": lambda s: _compute_mem(s, "available_bytes"),
        "native_unit_of_measurement": "B",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.memory",
    },
    {
        "key": "mem_percent",
        "name": "Memory used percentage",
        "compute": lambda s: _compute_mem(s, "percent"),
        "native_unit_of_measurement": "%",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.memory",
    },
    {
        "key": "swap_total",
        "name": "Swap total",
        "compute": lambda s: _compute_swap(s, "swap_total_bytes"),
        "native_unit_of_measurement": "B",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.swap",
    },
    {
        "key": "swap_used",
        "name": "Swap used",
        "compute": lambda s: _compute_swap(s, "swap_used_bytes"),
        "native_unit_of_measurement": "B",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.swap",
    },
    {
        "key": "swap_percent",
        "name": "Swap used percentage",
        "compute": lambda s: _compute_swap(s, "swap_percent"),
        "native_unit_of_measurement": "%",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.swap",
    },
    {
        "key": "uptime",
        "name": "Uptime",
        "compute": _compute_uptime,
        "native_unit_of_measurement": "s",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.clock",
    },
    {
        "key": "boot_time",
        "name": "Boot time",
        "compute": _compute_boot_time,
        "device_class": SensorDeviceClass.TIMESTAMP,
        "icon": "mdi.laptop",
    },
)


class SystemSensor(CoordinatorEntity[ResourceMonitorCoordinator], SensorEntity):
    """A fixed system metric from /api/system."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ResourceMonitorCoordinator, spec: dict) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}:{spec['key']}"
        self._attr_name = spec["name"]
        self._compute = spec["compute"]
        if spec.get("native_unit_of_measurement"):
            self._attr_native_unit_of_measurement = spec["native_unit_of_measurement"]
        if spec.get("state_class"):
            self._attr_state_class = spec["state_class"]
        if spec.get("device_class"):
            self._attr_device_class = spec["device_class"]
        elif spec.get("native_unit_of_measurement") == "B":
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
        if spec.get("icon"):
            self._attr_icon = spec["icon"]

    @property
    def native_value(self):
        return self._compute(self.coordinator.data.system)

    @property
    def device_info(self):
        return _host_device_info(self.coordinator)


class CoreSensor(CoordinatorEntity[ResourceMonitorCoordinator], SensorEntity):
    """One CPU core, created/destroyed to match the core count."""

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi.cpu-64bit"

    def __init__(
        self, coordinator: ResourceMonitorCoordinator, index: int
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._index = index
        self._attr_unique_id = f"{entry.unique_id}:core{index}"
        self._attr_name = f"CPU core {index + 1}"

    @property
    def native_value(self):
        cores = _get(self.coordinator.data.system, "cpu", "per_core_percent",
                     default=[])
        if isinstance(cores, list) and self._index < len(cores):
            return _num(cores[self._index])
        return None

    @property
    def device_info(self):
        return _host_device_info(self.coordinator)


# ---------------------------------------------------------------------------
# Per-container sensors
# ---------------------------------------------------------------------------


def _find_container(coordinator: ResourceMonitorCoordinator, cid: str):
    return next(
        (c for c in coordinator.data.containers if c.get("id") == cid), None
    )


class ContainerSensor(CoordinatorEntity[ResourceMonitorCoordinator], SensorEntity):
    """A metric for one Docker container."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ResourceMonitorCoordinator,
        container: dict,
        spec: dict,
        parent_device_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._parent_device_id = parent_device_id
        self._cid = container.get("id")
        self._cname = container.get("name") or "container"
        self._field = spec["field"]
        self._attr_unique_id = f"{entry.unique_id}:container:{self._cid}:{spec['key']}"
        self._attr_name = spec["name"]
        if spec.get("native_unit_of_measurement"):
            self._attr_native_unit_of_measurement = spec["native_unit_of_measurement"]
        if spec.get("state_class"):
            self._attr_state_class = spec["state_class"]
        if spec.get("device_class"):
            self._attr_device_class = spec["device_class"]
        elif spec.get("native_unit_of_measurement") == "B":
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
        if spec.get("icon"):
            self._attr_icon = spec["icon"]

    def _container(self):
        return _find_container(self.coordinator, self._cid)

    @property
    def native_value(self):
        container = self._container()
        if container is None:
            return None
        value = container.get(self._field)
        if self._field in ("cpu_percent", "memory_usage_bytes",
                           "memory_limit_bytes", "memory_percent"):
            return _num(value)
        return value  # status: plain string

    @property
    def extra_state_attributes(self):
        container = self._container() or {}
        return {
            "container_id": self._cid,
            "image": container.get("image"),
            "status": container.get("status"),
            "health": container.get("health"),
        }

    @property
    def device_info(self):
        entry = self.coordinator.config_entry
        container = self._container() or {}
        info = {
            "identifiers": {
                (DOMAIN, f"{entry.unique_id}:container:{self._cid}")
            },
            "name": f"Container {self._cname}",
            "manufacturer": "Docker",
            "model": container.get("image"),
        }
        if self._parent_device_id:
            info["via_device_id"] = self._parent_device_id
        return info


CONTAINER_SENSOR_DEFS: tuple[dict, ...] = (
    {
        "key": "cpu",
        "name": "CPU usage",
        "field": "cpu_percent",
        "native_unit_of_measurement": "%",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.cpu",
    },
    {
        "key": "mem",
        "name": "Memory usage",
        "field": "memory_usage_bytes",
        "native_unit_of_measurement": "B",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.memory",
    },
    {
        "key": "mem_percent",
        "name": "Memory used percentage",
        "field": "memory_percent",
        "native_unit_of_measurement": "%",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.memory",
    },
    {
        "key": "mem_limit",
        "name": "Memory limit",
        "field": "memory_limit_bytes",
        "native_unit_of_measurement": "B",
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi.swap",
    },
    {
        "key": "status",
        "name": "Status",
        "field": "status",
        "icon": "mdi.docker",
    },
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up system and container sensors."""
    coordinator: ResourceMonitorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Create the monitor device first: container devices link to it through
    # via_device_id, which needs the parent's device id up front.
    host_info = _host_device_info(coordinator)
    registry = dr.async_get(hass)
    host_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=host_info["identifiers"],
        name=host_info["name"],
        manufacturer=host_info["manufacturer"],
        model=host_info["model"],
        configuration_url=host_info["configuration_url"],
    )
    host_device_id = host_device.id

    # Static system sensors
    system_entities = [SystemSensor(coordinator, spec) for spec in SYSTEM_SENSOR_DEFS]

    # Dynamic entities, keyed by core index / container id
    core_sensors: dict[int, CoreSensor] = {}
    container_sensors: dict[str, list[ContainerSensor]] = {}
    refresh_lock = asyncio.Lock()

    async def _refresh_dynamic() -> None:
        """Create/remove per-core and per-container sensors as they appear."""
        async with refresh_lock:
            await _refresh_dynamic_locked()

    async def _refresh_dynamic_locked() -> None:
        system = coordinator.data.system
        core_count = len(
            _get(system, "cpu", "per_core_percent", default=[]) or []
        )
        new: list[Entity] = []
        # Per-core sensors: keep exactly core_count of them.
        for index in list(core_sensors):
            if index >= core_count:
                await core_sensors.pop(index).async_remove()
        for index in range(core_count):
            if index not in core_sensors:
                sensor = CoreSensor(coordinator, index)
                core_sensors[index] = sensor
                new.append(sensor)

        current: dict[str, dict] = {}
        for container in coordinator.data.containers:
            cid = container.get("id")
            if cid:
                current[cid] = container
        for cid in list(container_sensors):
            if cid not in current:
                for sensor in container_sensors.pop(cid):
                    await sensor.async_remove()
        for cid, container in current.items():
            if cid not in container_sensors:
                sensors = [
                    ContainerSensor(coordinator, container, spec, host_device_id)
                    for spec in CONTAINER_SENSOR_DEFS
                ]
                container_sensors[cid] = sensors
                new.extend(sensors)

        if new:
            async_add_entities(new)

    def _schedule_dynamic() -> None:
        """Coordinators call listeners synchronously; schedule the async refresh."""
        hass.async_create_task(_refresh_dynamic())

    entry.async_on_unload(coordinator.async_add_listener(_schedule_dynamic))
    async_add_entities(system_entities)
    await _refresh_dynamic()
