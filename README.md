# AI Room — Autonomous RPG Negotiation Simulator

Fully autonomous text RPG where 4 LLM models (1 narrator + 3 NPCs) run a negotiation scenario without human intervention. Each model has a different "personality" because it's a different LLM. The output is a log of the entire negotiation — readable as a short story.

## Quick Start

```bash
# Install
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Set API key
export OPENROUTER_API_KEY=sk-or-v1-...

# Run web UI
uvicorn web:app --host 0.0.0.0 --port 8091

# Or run CLI
python engine.py
```

## Architecture

| Role | Model | Description |
|------|-------|-------------|
| Narrator | deepseek-chat-v3 | Drives the scenario, describes situations, judges actions |
| NPC 1 | mistral-small-3.1 | Corporate negotiator (NovaTech) |
| NPC 2 | llama-4-scout | Indigenous community elder (Kai'nua) |
| NPC 3 | qwen3-4b | Government minister (Tuvanu) |

Models are configurable in `config.yaml`.

## Web UI

Live-streaming negotiation viewer at `http://localhost:8091`. Start a session, watch NPC decisions unfold in real-time.

## License

MIT
