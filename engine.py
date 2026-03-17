"""Main game loop — orchestrates narrator and NPCs."""

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

import yaml

from llm_client import TokenTracker, set_delay, get_delay
from narrator import Narrator
from npc import NPC
from scenario import DEFAULT_SCENARIO, SCENARIOS, Scenario


class Session:
    """A single negotiation session."""

    def __init__(self, scenario: Scenario = None, config: dict = None):
        self.scenario = scenario or DEFAULT_SCENARIO
        self.config = config or {}
        models = self.config.get("models", {})

        # Shared token tracker for the whole session
        self.token_tracker = TokenTracker()

        self.narrator = Narrator(
            self.scenario,
            models.get("narrator", "nvidia/nemotron-3-super-120b-a12b:free"),
            token_tracker=self.token_tracker,
        )
        self.npcs = []
        npc_model_keys = ["npc_1", "npc_2", "npc_3"]
        default_models = [
            "mistralai/mistral-small-3.1-24b-instruct:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "qwen/qwen3-4b:free",
        ]
        for i, npc_def in enumerate(self.scenario.npcs):
            model_key = npc_model_keys[i] if i < len(npc_model_keys) else f"npc_{i+1}"
            model = models.get(model_key, default_models[i] if i < len(default_models) else default_models[0])
            self.npcs.append(NPC(npc_def, model, token_tracker=self.token_tracker))

        self.rounds: list[dict] = []
        self.status = "idle"  # idle, running, paused, finished, error
        self.current_round = 0
        self.epilogue = None
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.created_at = datetime.now().isoformat()
        self.error = None

        # Callbacks for live updates
        self._on_event = None

    def set_event_callback(self, callback):
        self._on_event = callback

    async def _emit(self, event_type: str, data: dict):
        if self._on_event:
            await self._on_event(event_type, data)

    async def _emit_tokens(self):
        """Emit current token stats."""
        await self._emit("tokens", self.token_tracker.to_dict())

    async def run(self):
        """Run the full negotiation session."""
        self.status = "running"
        await self._emit("status", {
            "status": "running",
            "session_id": self.session_id,
            "scenario_id": self.scenario.id,
            "scenario_title": self.scenario.title,
        })

        try:
            # Opening scene
            await self._emit("phase", {"phase": "narrator", "round": 0, "detail": "Opening scene..."})
            narrator_result = await self.narrator.open_scene()
            self.rounds.append({
                "round": 0,
                "narrator": narrator_result,
                "npc_actions": [],
            })
            await self._emit("round", {"round": 0, "narrator": narrator_result, "npc_actions": []})
            await self._emit_tokens()

            # Main loop
            prev_public_actions = []
            for round_num in range(1, self.scenario.max_rounds + 1):
                if self.status == "paused":
                    await self._emit("status", {"status": "paused"})
                    while self.status == "paused":
                        await asyncio.sleep(1)
                    if self.status != "running":
                        break

                self.current_round = round_num

                # Check if previous round ended the scenario
                prev_narrator = self.rounds[-1]["narrator"]
                if prev_narrator.get("scenario_ended"):
                    break

                # NPC actions (sequential to respect rate limits)
                npc_actions = []
                situations = prev_narrator.get("npc_situations", {})

                for npc in self.npcs:
                    situation = situations.get(npc.id, {
                        "you_see": "Sedíš u jednacího stolu.",
                        "options": ["Mluvit", "Poslouchat", "Navrhnout kompromis"],
                        "pressure": "Čas běží."
                    })
                    await self._emit("phase", {
                        "phase": "npc",
                        "round": round_num,
                        "npc_id": npc.id,
                        "npc_name": npc.name,
                        "detail": f"{npc.name} přemýšlí..."
                    })
                    action = await npc.act(situation, round_num, prev_public_actions)
                    npc_actions.append(action)
                    await self._emit("npc_action", {
                        "round": round_num,
                        "npc": action
                    })
                    await self._emit_tokens()

                # Public actions (no thinking) for next round's NPC input
                prev_public_actions = [
                    {
                        "id": a["id"],
                        "name": a["name"],
                        "faction": a["faction"],
                        "action": a.get("action", ""),
                        "dialogue": a.get("dialogue", ""),
                    }
                    for a in npc_actions
                ]

                # Narrator processes actions
                await self._emit("phase", {
                    "phase": "narrator",
                    "round": round_num,
                    "detail": "Vypravěč hodnotí situaci..."
                })
                narrator_result = await self.narrator.next_round(round_num, npc_actions)

                self.rounds.append({
                    "round": round_num,
                    "narrator": narrator_result,
                    "npc_actions": npc_actions,
                })
                await self._emit("round", {
                    "round": round_num,
                    "narrator": narrator_result,
                    "npc_actions": npc_actions,
                })
                await self._emit_tokens()

                # Check end
                if narrator_result.get("scenario_ended"):
                    break

            # Epilogue
            last_actions = self.rounds[-1].get("npc_actions", [])
            await self._emit("phase", {"phase": "epilogue", "detail": "Vypravěč píše epilog..."})
            self.epilogue = await self.narrator.write_epilogue(last_actions)
            await self._emit("epilogue", {"text": self.epilogue})
            await self._emit_tokens()

            self.status = "finished"
            await self._emit("status", {"status": "finished"})

        except Exception as e:
            self.status = "error"
            self.error = str(e)
            await self._emit("error", {"error": str(e)})
            raise

    def to_transcript_md(self) -> str:
        """Generate a readable Markdown transcript."""
        lines = [
            f"# {self.scenario.title}",
            f"*Autonomní vyjednávací simulace — {self.created_at[:10]}*\n",
        ]

        for rd in self.rounds:
            round_num = rd["round"]
            narrator = rd["narrator"]
            npc_actions = rd.get("npc_actions", [])

            lines.append(f"## Kolo {round_num}\n")

            narration = narrator.get("narration", "")
            if narration:
                for para in narration.split("\n"):
                    para = para.strip()
                    if para:
                        lines.append(f"> {para}\n")

            for action in npc_actions:
                emotion = action.get("emotion", "")
                emotion_str = f" — *{emotion}*" if emotion else ""
                name = action.get("name", "?")
                faction = action.get("faction", "?")
                dialogue = action.get("dialogue", "...")

                lines.append(f"**{name}** ({faction}){emotion_str}:")
                lines.append("\u201E" + dialogue + "\u201C\n")

            tension = narrator.get("tension", "?")
            remaining = self.scenario.max_rounds - round_num
            lines.append("---")
            lines.append(f"*Napětí: {tension}/10 | Zbývá: {remaining} kol*")
            lines.append("---\n")

        if self.epilogue:
            lines.append("## Epilog\n")
            lines.append(self.epilogue)

        # Token stats at the end
        t = self.token_tracker
        lines.append("\n---\n## Statistiky")
        lines.append(f"- Celkem tokenů: {t.total_tokens}")
        lines.append(f"- Prompt tokenů: {t.total_prompt}")
        lines.append(f"- Completion tokenů: {t.total_completion}")
        lines.append(f"- Počet requestů: {t.request_count}")
        for model, stats in t.per_model.items():
            lines.append(f"- {model}: {stats['total']} tokenů / {stats['requests']} req")

        return "\n".join(lines)

    def to_full_log(self) -> dict:
        """Full JSON log with all data including NPC thinking."""
        return {
            "session_id": self.session_id,
            "scenario_id": self.scenario.id,
            "scenario": self.scenario.title,
            "created_at": self.created_at,
            "status": self.status,
            "rounds": self.rounds,
            "epilogue": self.epilogue,
            "tokens": self.token_tracker.to_dict(),
            "models": {
                "narrator": self.narrator.model,
                **{npc.id: npc.model for npc in self.npcs},
            },
            "npcs": [
                {"id": npc.id, "name": npc.name, "faction": npc.faction, "model": npc.model}
                for npc in self.npcs
            ],
        }

    def pause(self):
        if self.status == "running":
            self.status = "paused"

    def resume(self):
        if self.status == "paused":
            self.status = "running"

    def stop(self):
        self.status = "finished"


def load_config() -> dict:
    config_path = Path(__file__).parent / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {}


async def run_session():
    """CLI entry point."""
    config = load_config()
    session = Session(config=config)

    async def print_event(event_type, data):
        if event_type == "phase":
            print(f"  [{data.get('phase', '?')}] {data.get('detail', '')}")
        elif event_type == "round":
            r = data["round"]
            narration = data["narrator"].get("narration", "")[:100]
            print(f"\n=== Kolo {r} ===")
            print(f"  {narration}...")
        elif event_type == "npc_action":
            npc = data["npc"]
            print(f"  {npc['name']}: \"{npc.get('dialogue', '...')[:80]}\"")
        elif event_type == "epilogue":
            print(f"\n=== EPILOG ===\n{data['text'][:300]}...")
        elif event_type == "tokens":
            t = data
            print(f"  [tokens] {t['total_tokens']} total / {t['request_count']} requests")
        elif event_type == "error":
            print(f"\n!!! CHYBA: {data['error']}")

    session.set_event_callback(print_event)
    await session.run()

    # Save outputs
    out_dir = Path(__file__).parent / "sessions" / session.session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "transcript.md", "w") as f:
        f.write(session.to_transcript_md())

    with open(out_dir / "full_log.json", "w") as f:
        json.dump(session.to_full_log(), f, ensure_ascii=False, indent=2)

    print(f"\nUloženo do: {out_dir}")


if __name__ == "__main__":
    asyncio.run(run_session())
