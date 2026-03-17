"""Narrator logic — builds prompts, parses responses, tracks story."""

import json
from llm_client import chat, parse_json_response, LLMResponse, TokenTracker
from scenario import Scenario


def _clean_narrator_result(result: dict) -> dict:
    """Fix common LLM issues: nested JSON in narration, escaped newlines, etc."""
    if not result:
        return result

    # If narration contains a nested JSON string, try to parse and extract
    narration = result.get("narration", "")
    if isinstance(narration, str) and narration.strip().startswith("{"):
        try:
            inner = json.loads(narration)
            if isinstance(inner, dict):
                # Merge inner into result, preferring inner values
                for key in ["narration", "npc_situations", "tension", "scenario_ended", "end_reason"]:
                    if key in inner:
                        result[key] = inner[key]
        except (json.JSONDecodeError, TypeError):
            pass

    # Clean escaped newlines in narration text
    narration = result.get("narration", "")
    if isinstance(narration, str):
        result["narration"] = narration.replace("\\n", "\n").strip()

    # Clean NPC situations
    situations = result.get("npc_situations", {})
    if isinstance(situations, dict):
        for npc_id, sit in situations.items():
            if isinstance(sit, dict):
                for field in ["you_see", "pressure"]:
                    val = sit.get(field, "")
                    if isinstance(val, str):
                        sit[field] = val.replace("\\n", "\n").strip()

    return result


class Narrator:
    def __init__(self, scenario: Scenario, model: str, token_tracker: TokenTracker = None):
        self.scenario = scenario
        self.model = model
        self.history: list[dict] = []
        self.tokens = token_tracker

    def _track(self, resp: LLMResponse):
        if self.tokens:
            self.tokens.add(resp)

    async def open_scene(self) -> dict:
        """Generate the opening narration (round 0)."""
        user_msg = (
            f"Scenar: {self.scenario.title}\n"
            f"{self.scenario.description}\n\n"
            f"Ucastnici vyjednavani:\n"
        )
        for npc in self.scenario.npcs:
            user_msg += f"- {npc.name} ({npc.faction})\n"

        user_msg += (
            f"\nMaximalni pocet kol: {self.scenario.max_rounds}\n\n"
            "Zacni vyjednavani. Popis scenu — kde se nachazime, jaka je atmosfera, "
            "kdo sedi kde. Pak dej kazdemu NPC jeho prvni situaci.\n\n"
            "DULEZITE: Odpovez POUZE validnim JSON objektem. Zadny text pred ani za JSON."
        )

        resp = await chat(self.model, self.scenario.narrator_prompt,
                          [{"role": "user", "content": user_msg}])
        self._track(resp)
        result = parse_json_response(resp.content)
        if not result:
            result = {
                "narration": resp.content,
                "npc_situations": {},
                "tension": 1,
                "scenario_ended": False,
                "end_reason": None
            }

        result = _clean_narrator_result(result)
        self.history.append({"role": "narrator", "round": 0, "data": result, "raw": resp.content})
        return result

    async def next_round(self, round_num: int, npc_actions: list[dict]) -> dict:
        """Generate narration for the next round based on NPC actions."""
        actions_text = "Akce NPC v predchozim kole:\n\n"
        for action in npc_actions:
            actions_text += (
                f"**{action['name']}** ({action['faction']}):\n"
                f"  Interni uvaha: {action.get('thinking', '?')}\n"
                f"  Akce: {action.get('action', '?')}\n"
                f"  Rekl/a: \"{action.get('dialogue', '...')}\"\n"
                f"  Emoce: {action.get('emotion', '?')}\n\n"
            )

        user_msg = (
            f"Kolo {round_num}/{self.scenario.max_rounds}\n\n"
            f"{actions_text}\n"
            "Na zaklade techto akci popis novou situaci. "
            "Jak se atmosfera zmenila? Jake jsou dusledky?\n\n"
            "DULEZITE: Odpovez POUZE validnim JSON objektem. Zadny text pred ani za JSON."
        )

        messages = []
        context_rounds = self.history[-4:]
        for entry in context_rounds:
            if entry["role"] == "narrator":
                messages.append({"role": "assistant", "content": json.dumps(entry["data"], ensure_ascii=False)})
            else:
                messages.append({"role": "user", "content": entry.get("raw", "")})

        messages.append({"role": "user", "content": user_msg})

        resp = await chat(self.model, self.scenario.narrator_prompt, messages)
        self._track(resp)
        result = parse_json_response(resp.content)
        if not result:
            result = {
                "narration": resp.content,
                "npc_situations": {},
                "tension": round_num,
                "scenario_ended": False,
                "end_reason": None
            }

        result = _clean_narrator_result(result)
        self.history.append({"role": "narrator", "round": round_num, "data": result, "raw": resp.content})
        return result

    async def write_epilogue(self, npc_actions: list[dict]) -> str:
        """Generate final summary after the scenario ends."""
        actions_text = ""
        for action in npc_actions:
            actions_text += f"- {action['name']} ({action['faction']}): {action.get('action', '?')}\n"

        user_msg = (
            "Scenar skoncil.\n\n"
            f"Posledni akce:\n{actions_text}\n\n"
            "Napis finalni shrnuti vyjednavani jako epilog. "
            "Kdo vyhral? Jaka dohoda (ne)vznikla? Jake jsou dusledky pro kazdou stranu? "
            "Pis literarnim stylem, 3-5 odstavcu. Odpovez CISTYM TEXTEM, ne JSON."
        )

        messages = [{"role": "user", "content": user_msg}]
        resp = await chat(self.model, self.scenario.narrator_prompt, messages)
        self._track(resp)
        return resp.content
