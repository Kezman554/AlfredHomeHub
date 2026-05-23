Alfred Home Hub - Product Requirements Document
Version: 1.0
Last Updated: 2026-05-23
GitHub: https://github.com/Kezman554/AlfredHomeHub.git
Overview
Problem Statement
The household needs a centralised home hub — voice assistant, smart home controller, and dashboard — that runs locally on a Raspberry Pi 5, uses Claude as its brain, and speaks with a custom Fish Audio voice. Commercial assistants (Alexa) remain standalone; Alfred replaces none of them but adds a personal, context-aware layer they cannot provide.
Goals

Pi 5 running as an always-on home server with Home Assistant and household apps
Voice pipeline: wake word → STT → Claude API → Fish Audio TTS → speaker
Wall-mounted 10" touchscreen dashboard in the kitchen
Swappable AI personalities (Alfred, Holly, Jarvis, HAL) with mode system
Room expansion via Pi Zero satellites

Target Users
Nick's household — Nick (primary/admin), Jess, Oliver, and guests. Nick builds and maintains; the family uses it daily.
Features
Smart Home (Stage 1)

Home Assistant controlling Calex and Lutz smart bulbs
Light scenes (e.g. evening, cooking, movie)
Alexa Media Player integration (Echos as HA-routed music/announcement output)
Household apps accessible on local network (KitchenSync, Kanban)

Voice Assistant (Stage 2)

Custom wake words ("Alfred", "Alfie")
Speech-to-text via Whisper
Claude API conversational responses with personality
Fish Audio TTS with custom Alfred voice
Function calling: Home Assistant, KitchenSync, Google Calendar

Dashboard (Stage 3)

Wall-mounted 10" touchscreen in kitchen
Today's schedule, meals, tasks, weather, smart home controls
Visual listening indicator

Multi-Room (Stage 4)

Pi Zero 2W satellites with mic and speaker per room
Audio streamed to main Pi for processing, response played locally

Scope
In Scope

Stage 1 fully detailed: Pi server setup, Home Assistant, smart bulbs, Alexa Media Player integration, app deployment, vault sync
Stages 2–4 as planned milestones with known hardware and architecture

Out of Scope

KitchenSync application build (separate kanban project)
Capture App build (separate future project; one placeholder integration card here)
Alexa as voice or smart home control chain (Echos are audio output only; see alfred-alexa-relationship.md)
Alfred orchestration code design (covered in vault architecture docs, built at Stage 2)

Future Considerations

Per-personality memory stores (Holly's grudge system)
Speaker voiceprint recognition vs manual identification
Local LLM fallback for offline operation

Technical
Stack

Raspberry Pi 5 (8GB): Primary server
Raspberry Pi OS: Operating system
Docker: Service containerisation (Home Assistant)
Python: Alfred orchestration code (Stage 2+)
FastAPI: Household app backends (KitchenSync, Task API)
Home Assistant: Smart home control

Integrations

Claude API: Conversational AI brain
Fish Audio API: Text-to-speech with custom voice
Google Calendar API: Schedule queries
Home Assistant API: Device control
Alexa Media Player (HA integration): Echo devices as audio output endpoints

Constraints

All services run on a single Pi 5 (no cloud hosting)
WiFi networking (no Ethernet run to router)
Wife Acceptance Factor governs all visible hardware decisions
Monthly running costs target: £10–20/month

Project Structure
AlfredHomeHub/
├── CLAUDE.md
├── docs/
│   ├── alfred-home-hub_PRD.md
│   └── progress.txt
├── docker/
│   └── docker-compose.yml
├── scripts/
│   └── vault-sync.sh
└── src/
    └── alfred/              # Stage 2+ orchestration
Success Criteria

 Pi 5 boots reliably and is accessible via SSH from laptop
 Home Assistant running, controlling all smart bulbs with scenes
 Echos controllable as media players via HA (music and announcements)
 KitchenSync and Kanban accessible on home network (blocked on app builds)
 Obsidian vault syncing to Pi on a cron schedule
 Stage 2+: "Alfred, what's for dinner?" returns a voiced response with real data
 Stage 3: Clean wall-mounted dashboard, household approved