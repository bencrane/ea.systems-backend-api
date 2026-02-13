import json
import logging
from typing import Any

import httpx
import google.generativeai as genai
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.system import SystemDB
from app.config import settings

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Schema cache – keyed by slug, busted on deploy
# ---------------------------------------------------------------------------
_schema_cache: dict[str, dict] = {}


def invalidate_schema_cache(slug: str) -> None:
    """Remove cached schema for a slug. Called after deploy."""
    _schema_cache.pop(slug, None)


# ---------------------------------------------------------------------------
# OpenAPI helpers
# ---------------------------------------------------------------------------

def _resolve_refs(node: Any, root: dict, _depth: int = 0) -> Any:
    """Recursively resolve $ref pointers against the full OpenAPI spec."""
    if _depth > 30:
        return node
    if isinstance(node, dict):
        if "$ref" in node:
            ref_path = node["$ref"].lstrip("#/").split("/")
            resolved = root
            for part in ref_path:
                if not isinstance(resolved, dict) or part not in resolved:
                    logger.warning("Unresolvable $ref: %s", node["$ref"])
                    return node
                resolved = resolved[part]
            return _resolve_refs(resolved, root, _depth + 1)
        return {k: _resolve_refs(v, root, _depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_refs(item, root, _depth + 1) for item in node]
    return node


async def _fetch_input_schema(modal_url: str, slug: str) -> dict:
    """Fetch the OpenAPI spec from a Modal endpoint and extract the POST body schema."""
    if slug in _schema_cache:
        return _schema_cache[slug]

    openapi_url = f"{modal_url.rstrip('/')}/openapi.json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(openapi_url)
        resp.raise_for_status()
        spec = resp.json()

    # Find the first POST path with a JSON request body
    post_schema_raw = None
    for _path, methods in spec.get("paths", {}).items():
        if "post" in methods:
            rb = methods["post"].get("requestBody", {})
            json_ct = rb.get("content", {}).get("application/json", {})
            post_schema_raw = json_ct.get("schema")
            if post_schema_raw:
                break

    if not post_schema_raw:
        raise ValueError(f"No POST endpoint with JSON body found for '{slug}'")

    resolved = _resolve_refs(post_schema_raw, spec)

    # Strip client_id — the frontend injects it, the user should not be asked for it
    props = resolved.get("properties", {})
    props.pop("client_id", None)
    if "required" in resolved:
        resolved["required"] = [
            r for r in resolved["required"] if r != "client_id"
        ]

    _schema_cache[slug] = resolved
    return resolved


# ---------------------------------------------------------------------------
# Gemini prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """{chat_context}

Here is the JSON Schema describing the inputs you need to collect:

```json
{schema_json}
```

Rules:
1. Walk the user through the fields conversationally — one or two at a time. Never dump every field at once.
2. For enum fields, present the options as a clear numbered list and let the user pick.
3. For array fields (e.g. lists of URLs), let the user add items one at a time or paste several at once.
4. For optional fields, mention they are optional and ask if the user wants to provide a value.
5. For any field expecting a URL (photos, audio files, etc.), ask the user to upload the file and provide the resulting URL.
6. Keep your responses concise and friendly.
7. When ALL required fields are collected, display a clear summary of every value and ask the user to confirm.
8. If the user wants to change something, update the value and show the revised summary.
9. When the user confirms the summary, respond with **only** the following JSON block — no other text:

{{"ready": true, "payload": {{ ... }}}}

The payload must conform to the schema (correct types, enum values, etc.). Do NOT include client_id — it is added automatically."""


def _build_system_prompt(chat_context: str, schema: dict) -> str:
    default_context = "You are an intake assistant. Your job is to collect all required inputs from the user through a natural, conversational chat."
    return _SYSTEM_PROMPT.format(
        chat_context=chat_context or default_context,
        schema_json=json.dumps(schema, indent=2),
    )


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str = Field(..., pattern=r"^(user|model)$")
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_history: list[ChatMessage] = []
    client_id: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    response: str
    system_slug: str


class IntroResponse(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

async def handle_chat_message(
    db: AsyncSession,
    slug: str,
    payload: ChatRequest,
) -> ChatResponse:
    """Process a single chat turn for system intake."""

    # 1. Resolve system from DB
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == slug)
    )
    system = result.scalar_one_or_none()
    if not system:
        raise ValueError(f"System '{slug}' not found")
    if not system.modal_url:
        raise ValueError(f"System '{slug}' has not been deployed yet")

    # 2. Fetch & cache input schema from Modal's OpenAPI spec
    schema = await _fetch_input_schema(system.modal_url, slug)

    # 3. Configure Gemini
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        system_instruction=_build_system_prompt(system.chat_context, schema),
    )

    # 4. Build conversation history in Gemini's format
    gemini_history = [
        {"role": msg.role, "parts": [msg.content]}
        for msg in payload.conversation_history
    ]

    # 5. Send the latest user message
    chat = model.start_chat(history=gemini_history)
    response = chat.send_message(payload.message)

    return ChatResponse(
        response=response.text,
        system_slug=slug,
    )


async def handle_intro_message(
    db: AsyncSession,
    slug: str,
) -> IntroResponse:
    """Generate a welcome message for the system."""

    # 1. Resolve system from DB
    result = await db.execute(
        select(SystemDB).where(SystemDB.slug == slug)
    )
    system = result.scalar_one_or_none()
    if not system:
        raise ValueError(f"System '{slug}' not found")
    if not system.modal_url:
        raise ValueError(f"System '{slug}' has not been deployed yet")

    # 2. Fetch schema
    schema = await _fetch_input_schema(system.modal_url, slug)

    # 3. Configure Gemini
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(GEMINI_MODEL)

    # 4. Generate intro message
    prompt = f"""{system.chat_context or "You are an intake assistant."}

Here is the input schema for this system:
{json.dumps(schema, indent=2)}

Generate a single, friendly welcome message introducing yourself and what you help with.
Ask about the first required field to get started. Keep it concise (2-3 sentences max)."""

    response = model.generate_content(prompt)

    return IntroResponse(message=response.text)
