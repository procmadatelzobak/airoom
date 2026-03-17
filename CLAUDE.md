# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this

Autonomous RPG negotiation simulator. 4 LLM models (1 narrator + 3 NPC) run a negotiation scenario without human intervention. Each NPC is a different model = different "personality". Output is a readable story log.

## Commands

```bash
# Install
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Run web UI (production)
.venv/bin/uvicorn web:app --host 0.0.0.0 --port 8091

# Run CLI (single session, saves to sessions/)
OPENROUTER_API_KEY=... .venv/bin/python3 engine.py

# Service management (on Kliment)
systemctl --user status sinuhetcloud-airoom
systemctl --user restart sinuhetcloud-airoom
journalctl --user -u sinuhetcloud-airoom -f
```

## Architecture

```
web.py          — FastAPI server, WebSocket live streaming, REST API
engine.py       — Game loop: Session class, orchestrates narrator + NPCs
narrator.py     — Narrator prompt building, JSON response parsing, history
npc.py          — NPC logic: own memory, information asymmetry, JSON parsing
llm_client.py   — OpenRouter wrapper: rate limiting, retry with backoff, token tracking
scenario.py     — Scenario definitions (DEFAULT_SCENARIO + CUBAN_CRISIS_SCENARIO)
config.yaml     — Model selection, engine params, web config
templates/      — Jinja2 HTML (single-page app with WebSocket)
sessions/       — Output: transcript.md + full_log.json per session (gitignored)
```

### Game loop (one round)

1. **Narrator** gets full history + all NPC actions → returns JSON with narration, per-NPC situations, tension, end flag
2. **Each NPC** (sequentially, for rate limits) gets its own situation + public actions only (no other NPCs' thinking) → returns JSON with thinking, action, dialogue, emotion
3. Public actions fed back to narrator for next round
4. Narrator sets `scenario_ended: true` → epilogue generated

### Information asymmetry

- NPCs see only public actions (dialogue + action) of others, never `thinking`
- Narrator sees everything including internal thoughts — is the "god" of the simulation
- Each NPC maintains its own memory history

### Rate limiting

- OpenRouter free tier: 200 req/day per model, 20 req/min
- Configurable delay between requests (default 3.5s)
- On 429: retries same model (up to 20 attempts) with increasing backoff (15, 20, 25... seconds)
- No fallback to other models — preserves model identity for comparison

## Deployment

- **Server**: Kliment (10.200.80.103), port 8091
- **URL**: https://airoom.sinuhetcloud.coitus.cz
- **Nginx**: Julie, config at `/etc/nginx/conf.d/airoom.conf`
- **SSL**: Let's Encrypt via certbot
- **Env**: `.env` with `OPENROUTER_API_KEY` (gitignored)
- **Service**: `~/.config/systemd/user/sinuhetcloud-airoom.service`

## Current working models (free tier, as of 2026-03-17)

| Role | Model | Status |
|------|-------|--------|
| Narrator | `nvidia/nemotron-3-super-120b-a12b:free` | Stable |
| NPC 1 | `arcee-ai/trinity-large-preview:free` | Stable |
| NPC 2 | `stepfun/step-3.5-flash:free` | Occasional 429 |
| NPC 3 | `z-ai/glm-4.5-air:free` | Stable |

Models that DON'T work: `deepseek/deepseek-chat-v3-0324:free` (404), `meta-llama/llama-4-scout:free` (404), `google/gemma-3-*:free` (often 429), `nousresearch/hermes-3-*:free` (often 429).

## Known issues

- `arcee-ai/trinity-large-preview:free` sometimes returns very short responses (1 token)
- Free models have daily limits (~200 req/model) — a full 20-round session uses ~80 requests
- LLM responses sometimes contain nested JSON (narration field contains stringified JSON) — `narrator.py:_clean_narrator_result()` handles this
