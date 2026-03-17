"""OpenRouter LLM client with rate limiting, retry logic, and token tracking."""

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from openai import AsyncOpenAI

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
BASE_URL = "https://openrouter.ai/api/v1"

_last_request_time = 0.0
_lock = asyncio.Lock()
_delay = 3.5  # seconds between requests (mutable at runtime)


def set_delay(seconds: float):
    """Change the minimum delay between requests."""
    global _delay
    _delay = max(0.5, seconds)


def get_delay() -> float:
    return _delay


async def _rate_limit():
    """Enforce minimum delay between requests."""
    global _last_request_time
    async with _lock:
        now = time.time()
        wait = _delay - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_time = time.time()


@dataclass
class LLMResponse:
    """Response from an LLM call with token usage."""
    content: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""


@dataclass
class TokenTracker:
    """Accumulates token usage across a session."""
    total_prompt: int = 0
    total_completion: int = 0
    total_tokens: int = 0
    request_count: int = 0
    per_model: dict = field(default_factory=dict)

    def add(self, resp: LLMResponse):
        self.total_prompt += resp.prompt_tokens
        self.total_completion += resp.completion_tokens
        self.total_tokens += resp.total_tokens
        self.request_count += 1
        if resp.model not in self.per_model:
            self.per_model[resp.model] = {"prompt": 0, "completion": 0, "total": 0, "requests": 0}
        self.per_model[resp.model]["prompt"] += resp.prompt_tokens
        self.per_model[resp.model]["completion"] += resp.completion_tokens
        self.per_model[resp.model]["total"] += resp.total_tokens
        self.per_model[resp.model]["requests"] += 1

    def to_dict(self) -> dict:
        return {
            "total_prompt": self.total_prompt,
            "total_completion": self.total_completion,
            "total_tokens": self.total_tokens,
            "request_count": self.request_count,
            "per_model": self.per_model,
        }


async def _single_attempt(client, model: str, full_messages: list, temperature: float) -> LLMResponse:
    """Make a single API call."""
    await _rate_limit()
    response = await client.chat.completions.create(
        model=model,
        messages=full_messages,
        temperature=temperature,
        max_tokens=2000,
    )
    content = response.choices[0].message.content
    usage = response.usage
    return LLMResponse(
        content=content.strip() if content else "",
        prompt_tokens=usage.prompt_tokens if usage else 0,
        completion_tokens=usage.completion_tokens if usage else 0,
        total_tokens=usage.total_tokens if usage else 0,
        model=model,
    )


async def chat(model: str, system_prompt: str, messages: list[dict],
               temperature: float = 0.8, max_retries: int = 20,
               on_status=None) -> LLMResponse:
    """Send a chat completion request to OpenRouter.

    On 429, keeps retrying the SAME model with increasing backoff.
    This preserves model identity for comparison purposes.
    on_status: optional async callback(msg) for live status updates.
    """
    client = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url=BASE_URL)
    full_messages = [{"role": "system", "content": system_prompt}] + messages

    for attempt in range(max_retries):
        try:
            resp = await _single_attempt(client, model, full_messages, temperature)
            return resp
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str

            if attempt == max_retries - 1:
                raise

            if is_rate_limit:
                # Backoff: 15, 20, 25, 30... seconds
                wait = 15 + (attempt * 5)
                print(f"  429 on {model}, retry {attempt+1}/{max_retries} in {wait}s...")
                if on_status:
                    await on_status(f"Rate limit ({model.split('/')[-1]}), čekám {wait}s...")
            else:
                wait = (attempt + 1) * 5
                print(f"  LLM error ({model}): {e}, retrying in {wait}s...")
                if on_status:
                    await on_status(f"Chyba: {err_str[:80]}, retry za {wait}s...")

            await asyncio.sleep(wait)

    raise Exception(f"All {max_retries} attempts exhausted for {model}")


def parse_json_response(text: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return None
