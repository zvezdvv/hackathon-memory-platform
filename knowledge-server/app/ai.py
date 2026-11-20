"""Voice notes: transcription (ElevenLabs Scribe v2) and LLM structuring via OpenRouter."""

from __future__ import annotations

import json
import logging
import os

import httpx

log = logging.getLogger("knowledge.ai")

OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY = os.getenv("OPENROUTER_KEY", "")
TRANSCRIBE_MODEL = os.getenv("TRANSCRIBE_MODEL", "elevenlabs/scribe-v2")
STRUCTURE_MODEL = os.getenv("STRUCTURE_MODEL", "openai/gpt-6-luna")
# Empty = same language as the input (default). Set e.g. "English" to force a language.
NOTE_LANGUAGE = os.getenv("NOTE_LANGUAGE", "")


def enabled() -> bool:
    return bool(OPENROUTER_KEY) and OPENROUTER_KEY != "not-configured"


def _headers() -> dict:
    return {"Authorization": f"Bearer {OPENROUTER_KEY}"}


async def transcribe(data: bytes, filename: str, mime: str) -> str:
    """Speech to text with Scribe v2 (OpenAI-compatible transcription endpoint)."""
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{OPENROUTER_URL}/audio/transcriptions",
            headers=_headers(),
            files={"file": (filename, data, mime or "application/octet-stream")},
            data={"model": TRANSCRIBE_MODEL},
        )
    if r.status_code >= 300:
        raise RuntimeError(f"Transcription failed (HTTP {r.status_code}): {r.text[:300]}")
    return (r.json().get("text") or "").strip()


STRUCTURE_PROMPT = """You turn a raw voice note from a city administration employee into a clean,
structured knowledge-base entry. The transcript may contain filler words, repetitions and jumps.

Return ONLY a JSON object with these keys:
- "title": short, specific title (max 8 words)
- "summary": 2-5 bullet points with the essential knowledge (strings, no leading dash)
- "sections": list of {"heading": str, "points": [str]} grouping the content logically
  (e.g. "Situation", "What to do", "Contacts", "Deadlines"); keep every concrete fact
  (numbers, times, places, names, legal references); do not invent anything
- "people": names of persons mentioned (strings)
- "places": streets, buildings, areas mentioned (strings)
- "tags": 3-6 lowercase keywords
- "date": an ISO date (YYYY-MM-DD) only if the note explicitly refers to one, else ""
- "updates": IDs of existing documents this note explicitly corrects (e.g. "N06"), else []
- "language": ISO 639-1 code of the language you wrote the note in (e.g. "de", "en")
- "labels": {"summary": str, "transcript": str} – the words for "Summary" and
  "Original transcript" in that language

{language} Keep names, street names and legal references in their original form.{hints}"""


async def structure(transcript: str, title_hint: str = "", person_hint: str = "") -> dict:
    """Asks the LLM for a structured note with metadata."""
    hints = ""
    if title_hint:
        hints += f"\nThe author suggested this title: {title_hint!r}."
    if person_hint:
        hints += f"\nThe author of the note is: {person_hint!r}."
    language = (
        f"Write title, summary, sections and tags in {NOTE_LANGUAGE}."
        if NOTE_LANGUAGE
        else "Write title, summary, sections and tags in the SAME language as the transcript "
        "(German transcript → German note). Do not translate."
    )
    prompt = STRUCTURE_PROMPT.replace("{language}", language).replace("{hints}", hints)
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{OPENROUTER_URL}/chat/completions",
            headers=_headers(),
            json={
                "model": STRUCTURE_MODEL,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": transcript},
                ],
            },
        )
    if r.status_code >= 300:
        raise RuntimeError(f"Structuring failed (HTTP {r.status_code}): {r.text[:300]}")
    content = r.json()["choices"][0]["message"]["content"] or "{}"
    content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Structuring returned no valid JSON: {content[:200]}") from e
    return data if isinstance(data, dict) else {}
