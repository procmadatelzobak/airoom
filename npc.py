"""NPC logic — each NPC has its own memory and perspective."""

import json
from llm_client import chat, parse_json_response
from scenario import NPCDef


class NPC:
    def __init__(self, definition: NPCDef, model: str):
        self.definition = definition
        self.model = model
        self.id = definition.id
        self.name = definition.name
        self.faction = definition.faction
        # NPC's own memory — what it has seen and done
        self.memory: list[dict] = []

    async def act(self, situation: dict, round_num: int, public_actions: list[dict]) -> dict:
        """Decide NPC's action based on its situation and what it knows.

        Args:
            situation: The narrator's description for this NPC (you_see, options, pressure)
            round_num: Current round number
            public_actions: Public actions from previous round (dialogue + action only, no thinking)
        """
        # Build what the NPC knows
        user_msg = f"Kolo {round_num}.\n\n"

        # What others said/did last round (public info only)
        if public_actions:
            user_msg += "Co se stalo v předchozím kole (co jsi viděl/a a slyšel/a):\n"
            for pa in public_actions:
                if pa["id"] != self.id:  # Don't repeat own actions
                    user_msg += f"- {pa['name']} ({pa['faction']}): {pa.get('dialogue', '...')}\n"
                    if pa.get("action"):
                        user_msg += f"  (Akce: {pa['action']})\n"
            user_msg += "\n"

        # Current situation from narrator
        user_msg += f"Aktuální situace (co vnímáš):\n{situation.get('you_see', 'Nic zvláštního.')}\n\n"

        if situation.get("options"):
            user_msg += "Nabízené možnosti:\n"
            for i, opt in enumerate(situation["options"], 1):
                user_msg += f"  {i}. {opt}\n"
            user_msg += "(Můžeš zvolit jednu z možností nebo udělat něco úplně jiného.)\n\n"

        if situation.get("pressure"):
            user_msg += f"Tlak: {situation['pressure']}\n\n"

        user_msg += "Co uděláš? Odpověz v JSON formátu."

        # Build conversation with NPC's memory
        messages = []
        # Include last few memories for context
        for mem in self.memory[-6:]:
            messages.append({"role": "user", "content": mem["situation_text"]})
            messages.append({"role": "assistant", "content": json.dumps(mem["response"], ensure_ascii=False)})

        messages.append({"role": "user", "content": user_msg})

        raw = await chat(self.model, self.definition.system_prompt, messages)
        result = parse_json_response(raw)

        if not result:
            result = {
                "thinking": "...",
                "action": raw[:200] if raw else "Mlčí.",
                "dialogue": raw[:200] if raw else "...",
                "emotion": "nejistota"
            }

        # Save to memory
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
