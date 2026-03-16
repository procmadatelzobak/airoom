"""OpenRouter LLM client with rate limiting and retry logic."""

import asyncio
import json
import os
import time
from openai import AsyncOpenAI

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
BASE_URL = "https://openrouter.ai/api/v1"

_last_request_time = 0.0
_lock = asyncio.Lock()
MIN_DELAY = 3.5  # seconds between requests


async def _rate_limit():
    """Enforce minimum delay between requests."""
    global _last_request_time
    async with _lock:
        now = time.time()
        wait = MIN_DELAY - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_time = time.time()


async def chat(model: str, system_prompt: str, messages: list[dict],
               temperature: float = 0.8, max_retries: int = 3) -> str:
    """Send a chat completion request to OpenRouter.

    Args:
        model: OpenRouter model ID
        system_prompt: System message content
        messages: List of {"role": "user"|"assistant", "content": "..."} dicts
        temperature: Sampling temperature
        max_retries: Number of retries on failure

    Returns:
        The assistant's response text.
    """
    client = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url=BASE_URL)

    full_messages = [{"role": "system", "content": system_prompt}] + messages

    for attempt in range(max_retries):
        await _rate_limit()
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=full_messages,
                temperature=temperature,
                max_tokens=2000,
            )
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait = (attempt + 1) * 5
            print(f"  LLM error ({model}): {e}, retrying in {wait}s...")
            await asyncio.sleep(wait)

    return ""


def parse_json_response(text: str) -> dict | None:
    """Extract JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last line if they are fences
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
