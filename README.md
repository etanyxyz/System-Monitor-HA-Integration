# System Monitor for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![Validate](https://github.com/etanyxyz/System-Monitor-HA-Integration/actions/workflows/validate.yml/badge.svg)](https://github.com/etanyxyz/System-Monitor-HA-Integration/actions/workflows/validate.yml)

A Home Assistant custom integration that turns the
[System Monitor](https://github.com/etanyxyz/System-Monitor) service into
native sensors: host CPU, memory, swap, load averages and per-Docker-container
usage — no YAML, no MQTT, no extra agent.

The companion Lovelace card lives in
[System-Monitor-HA-Card](https://github.com/etanyxyz/System-Monitor-HA-Card).

## What you need

A running System Monitor (the API + dashboard container). It is built from
source — there is no published image:

```bash
git clone https://github.com/etanyxyz/System-Monitor.git
cd System-Monitor
docker compose up -d --build     # hardened container, port 1010
```

It should answer `http://<host>:1010/api/system`. Home Assistant needs to be
able to reach that address — with Home Assistant on the same Docker host
(`network_mode: host`), `http://127.0.0.1:1010` also works.

## Installation

### Marketplace / HACS (recommended)

1. Add this repository as a custom repository:
   - **Marketplace**: Marketplace → repositories → add
     `https://github.com/etanyxyz/System-Monitor-HA-Integration`
     (category **Integration**)
   - **HACS**: HACS → three-dot menu → **Custom repositories** → add
     `https://github.com/etanyxyz/System-Monitor-HA-Integration`,
     category **Integration**
2. Install **System Monitor** and restart Home Assistant.
3. Settings → Devices & services → **Add integration** → **System Monitor**.

### Manual

Copy `custom_components/resource_monitor/` into your Home Assistant
`config/custom_components/` directory, restart, then add the integration.

## Setup

| Field | Required | Notes |
| --- | --- | --- |
| Monitor URL | yes | e.g. `http://192.168.1.50:1010`. `http://` is assumed when you leave the scheme out; a trailing `/` is ignored. |
| Token | no | Only if the monitor runs with `MONITOR_TOKEN`. |
| Poll interval | yes | Seconds, minimum **2**, default **10**. Container stats cost one Docker Engine call per running container, so avoid very small values. |

The URL is the entry's unique ID: the same monitor cannot be added twice, and
changing it later means removing and re-adding the integration (that also
regenerates entity IDs).

If the monitor answers `401`, Home Assistant starts a re-authentication flow
instead of failing.

## Entities

**One device per monitor** — `System Monitor (<host>)`, with a link to the
dashboard:

| Entity | Unit / class |
| --- | --- |
| CPU usage | % |
| CPU core 1 … N | % |
| Load average (1m / 5m / 15m) | — |
| Memory total / used / available | data size |
| Memory used percentage | % |
| Swap total / used | data size |
| Swap used percentage | % |
| Uptime | s |
| Boot time | timestamp |

Per-core sensors follow the host's core count: they are created and removed as
the count changes.

**One device per Docker container** — `Container <name>`, linked to the
monitor device:

| Entity | Unit / class |
| --- | --- |
| CPU usage | % |
| Memory usage | data size |
| Memory used percentage | % |
| Memory limit | data size |
| Status | text |

Attributes on the container sensors include the container ID, image, Docker
status string and health. Containers that disappear from the host are removed
automatically. Stopped containers have no live stats, so the percentage/usage
sensors report `unknown` while `Status` stays meaningful.

## Options

**Configure** on the integration entry lets you change the token and the poll
interval, without touching the URL or losing entity history. The entry reloads
automatically.

## Behaviour details

- Polling uses `/api/system` and `/api/containers` only — the monitor exposes
  no write endpoints and holds the Docker socket read-only.
- The token is sent as `Authorization: Bearer`; if a reverse proxy strips that
  header, the integration automatically retries with the dashboard-compatible
  `?token=` query parameter.
- If Docker is down but the host is fine, the system sensors keep updating and
  the container sensors keep their last values while the error is logged.
- Network failures are retried with Home Assistant's normal `UpdateFailed`
  backoff; bad tokens trigger re-authentication.

## Removing

Delete the config entry (Settings → Devices & services → System Monitor →
Delete); all entities and devices created by it are removed with it.

## Development

- `custom_components/resource_monitor/` — `config_flow.py` (setup, reauth,
  options), `coordinator.py` (polling), `sensor.py` (entities).
- CI runs hassfest, the HACS validation action and a byte-compile check;
  pushing a `v*` tag publishes a GitHub release.
- Tested against Home Assistant 2026.11 with a live monitor.

## License

MIT — see [LICENSE](LICENSE).
