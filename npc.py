"""NPC logic — each NPC has its own memory and perspective."""

import json
from llm_client import chat, parse_json_response, TokenTracker
from scenario import NPCDef


def _clean_npc_result(result: dict) -> dict:
    """Clean escaped newlines and other formatting issues from NPC response."""
    for field in ["thinking", "action", "dialogue", "emotion"]:
        val = result.get(field, "")
        if isinstance(val, str):
            result[field] = val.replace("\\n", "\n").replace("\\\"", "\"").strip()
    return result


class NPC:
    def __init__(self, definition: NPCDef, model: str, token_tracker: TokenTracker = None):
        self.definition = definition
        self.model = model
        self.id = definition.id
        self.name = definition.name
        self.faction = definition.faction
        self.memory: list[dict] = []
        self.tokens = token_tracker

    async def act(self, situation: dict, round_num: int, public_actions: list[dict]) -> dict:
        """Decide NPC's action based on its situation and what it knows."""
        user_msg = f"Kolo {round_num}.\n\n"

        if public_actions:
            user_msg += "Co se stalo v predchozim kole (co jsi videl/a a slysel/a):\n"
            for pa in public_actions:
                if pa["id"] != self.id:
                    user_msg += f"- {pa['name']} ({pa['faction']}): {pa.get('dialogue', '...')}\n"
                    if pa.get("action"):
                        user_msg += f"  (Akce: {pa['action']})\n"
            user_msg += "\n"

        user_msg += f"Aktualni situace (co vnimas):\n{situation.get('you_see', 'Nic zvlastniho.')}\n\n"

        if situation.get("options"):
            user_msg += "Nabizene moznosti:\n"
            for i, opt in enumerate(situation["options"], 1):
                user_msg += f"  {i}. {opt}\n"
            user_msg += "(Muzes zvolit jednu z moznosti nebo udelat neco uplne jineho.)\n\n"

        if situation.get("pressure"):
            user_msg += f"Tlak: {situation['pressure']}\n\n"

        user_msg += "Co udelas? Odpovez POUZE validnim JSON objektem. Zadny text pred ani za JSON."

        messages = []
        for mem in self.memory[-6:]:
            messages.append({"role": "user", "content": mem["situation_text"]})
            messages.append({"role": "assistant", "content": json.dumps(mem["response"], ensure_ascii=False)})

        messages.append({"role": "user", "content": user_msg})

        resp = await chat(self.model, self.definition.system_prompt, messages)
        if self.tokens:
            self.tokens.add(resp)
        result = parse_json_response(resp.content)

        if not result:
            # LLM didn't return valid JSON — use raw text as dialogue
            raw = resp.content[:300] if resp.content else "..."
            result = {
                "thinking": "...",
                "action": raw,
                "dialogue": raw,
                "emotion": "nejistota"
            }

        result = _clean_npc_result(result)

        self.memory.append({
            "round": round_num,
            "situation_text": user_msg,
            "response": result,
        })

        return {
            "id": self.id,
            "name": self.name,
            "faction": self.faction,
            **result
        }
