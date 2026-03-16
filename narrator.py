"""Narrator logic — builds prompts, parses responses, tracks story."""

import json
from llm_client import chat, parse_json_response, LLMResponse, TokenTracker
from scenario import Scenario


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
            f"Scénář: {self.scenario.title}\n"
            f"{self.scenario.description}\n\n"
            f"Účastníci vyjednávání:\n"
        )
        for npc in self.scenario.npcs:
            user_msg += f"- {npc.name} ({npc.faction})\n"

        user_msg += (
            f"\nMaximální počet kol: {self.scenario.max_rounds}\n\n"
            "Začni vyjednávání. Popiš scénu — kde se nacházíme, jaká je atmosféra, "
            "kdo sedí kde. Pak dej každému NPC jeho první situaci."
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

        self.history.append({"role": "narrator", "round": 0, "data": result, "raw": resp.content})
        return result

    async def next_round(self, round_num: int, npc_actions: list[dict]) -> dict:
        """Generate narration for the next round based on NPC actions."""
        actions_text = "Akce NPC v předchozím kole:\n\n"
        for action in npc_actions:
            actions_text += (
                f"**{action['name']}** ({action['faction']}):\n"
                f"  Interní úvaha: {action.get('thinking', '?')}\n"
                f"  Akce: {action.get('action', '?')}\n"
                f"  Řekl/a: \"{action.get('dialogue', '...')}\"\n"
                f"  Emoce: {action.get('emotion', '?')}\n\n"
            )

        user_msg = (
            f"Kolo {round_num}/{self.scenario.max_rounds}\n\n"
            f"{actions_text}\n"
            "Na základě těchto akcí popiš novou situaci. "
            "Jak se atmosféra změnila? Jaké jsou důsledky?"
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

        self.history.append({"role": "narrator", "round": round_num, "data": result, "raw": resp.content})
        return result

    async def write_epilogue(self, npc_actions: list[dict]) -> str:
        """Generate final summary after the scenario ends."""
        actions_text = ""
        for action in npc_actions:
            actions_text += f"- {action['name']} ({action['faction']}): {action.get('action', '?')}\n"

        user_msg = (
            "Scénář skončil.\n\n"
            f"Poslední akce:\n{actions_text}\n\n"
            "Napiš finální shrnutí vyjednávání jako epilog. "
            "Kdo vyhrál? Jaká dohoda (ne)vznikla? Jaké jsou důsledky pro každou stranu? "
            "Piš literárním stylem, 3-5 odstavců. Odpověz ČISTÝM TEXTEM, ne JSON."
        )

        messages = [{"role": "user", "content": user_msg}]
        resp = await chat(self.model, self.scenario.narrator_prompt, messages)
        self._track(resp)
        return resp.content
