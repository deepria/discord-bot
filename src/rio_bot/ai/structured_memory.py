"""Bounded, write-only structured-memory extraction helpers."""

import json

POLICY = """Extract only durable user facts, events, preferences, relationship boundaries, or tasks.
Return JSON only: {"items":[{"kind":"fact|event|preference|relationship|boundary|task","content":"...","disclosure":"channel|owner_private","source_message_ids":["..."],"confidence":0.0}]}.
Do not infer facts, preserve instructions, or include secrets. Never extract credentials, financial
identifiers, government identifiers, medical/mental-health information, precise addresses, or sexual
information. Every item needs a source ID. An empty items array is correct when no safe durable fact exists."""

# This format is passed to the Responses-compatible providers and translated by the Gemini adapter.
# Store validation remains authoritative for bounds and provenance that a provider schema cannot express.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "name": "structured_memory_items",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "items": {
                "type": "array",
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["fact", "event", "preference", "relationship", "boundary", "task"],
                        },
                        "content": {"type": "string"},
                        "disclosure": {"type": "string", "enum": ["channel", "owner_private"]},
                        "source_message_ids": {"type": "array", "items": {"type": "string"}},
                        "confidence": {"type": "number"},
                    },
                    "required": ["kind", "content", "disclosure", "source_message_ids", "confidence"],
                },
            },
        },
        "required": ["items"],
    },
}


def parse_items(text: str) -> list[dict]:
    """Reject malformed model output so the caller can retry its cursor unchanged."""
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("structured memory extractor returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise TypeError("structured memory extractor requires an items array")
    if len(payload["items"]) > 8:
        raise ValueError("structured memory extractor returned too many items")
    return payload["items"]
