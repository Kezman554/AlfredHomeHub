# Deployment

## Target Device — Raspberry Pi 5 (8GB), "Alfred"

| Field | Value |
|---|---|
| Hostname | `alfred` (resolves as `alfred.local` via mDNS) |
| User | `kezman554` |
| Static IP | `192.168.1.100` — DHCP reservation on the router, bound to MAC `2C:CF:67:B7:5F:21` (stable across reboots) |
| SSH | `ssh alfred` (alias in laptop `~/.ssh/config`) or `ssh kezman554@192.168.1.100` — passwordless key auth |
| OS | Raspberry Pi OS (Debian-based, aarch64, kernel 6.12.x) |
| Runtime | Docker + docker compose plugin; `kezman554` is in the `docker` group (no `sudo` for docker) |

> `.local` resolution fails from the laptop while a VPN is connected — disconnect the VPN if `alfred.local` won't resolve.

## Service Ports

| Port | Service | Notes |
|---|---|---|
| 8123 | Home Assistant | Host networking (device discovery) |
| 8200 | Vault API | Read-only vault content for the MorningSync alarm app. `http://192.168.1.100:8200` — `/health`, `/chalkboard` |

## Deploy Model

- Laptop is the source of truth: develop here, push to GitHub (`git@github.com:Kezman554/AlfredHomeHub.git`).
- The Pi **pulls** — repo lives on the Pi at `~/projects/AlfredHomeHub`.
- Services (including Home Assistant) run as Docker containers; build/select ARM64 images.
