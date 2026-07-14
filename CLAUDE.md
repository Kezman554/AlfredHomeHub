Project: Alfred Home Hub
Raspberry Pi 5 home hub — smart home server, voice assistant, and kitchen dashboard.
Built with Docker, Python, Home Assistant, Claude API, Fish Audio.
Structure

docker/ - Docker Compose configs for Pi services (Home Assistant, etc.)
scripts/ - Deployment and maintenance scripts (vault sync, setup helpers)
src/alfred/ - Alfred voice orchestration code (Stage 2+)
docs/ - PRD and progress log

Context
This repo produces files that run on a Raspberry Pi 5, not this laptop. Claude Code sessions here write configs, scripts, and code that get pushed to GitHub and pulled onto the Pi. The Pi's OS is Raspberry Pi OS (Debian/ARM64).
The Obsidian vault (separate private repo: ObsidianVault — git@github.com:Kezman554/ObsidianVault.git) contains Alfred's architecture docs, personality files (SOUL.md, modes/), and project notes. The Pi clones that vault separately to /home/kezman554/alfred-vault and pulls it on a cron schedule (scripts/vault-sync.sh).
Commands

No local run command — services run on the Pi via Docker
Scripts are bash, targeted at Raspberry Pi OS (Debian ARM64)

Git

Do not push to GitHub without explicit permission
Commit after completing each session
Update docs/progress.txt briefly if significant work was done

Conventions

Scripts assume the Pi user is kezman554, home at /home/kezman554/
Docker Compose files go in docker/, one per service grouping
All paths in scripts should be configurable via variables at top of file

Runtime & Deploy

Targets a Raspberry Pi 5 (ARM64, Raspberry Pi OS) running Docker — build for ARM, services run as containers. Develop on laptop → push to GitHub → Pi pulls. Access + deploy steps: see DEPLOY.md.

Reference

Requirements: docs/alfred-home-hub_PRD.md
Progress log: docs/progress.txt
Architecture docs: ObsidianVault (hardware-roadmap.md, data-routing-architecture.md, pi-transition-considerations.md, alfred-alexa-relationship.md)
Task prompts: Kanban app