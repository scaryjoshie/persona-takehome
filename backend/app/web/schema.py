"""Export the browser protocol as JSON Schema: `uv run python -m app.web.schema > schema.json`."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from app.payloads import AnyPayload
from app.previews.fetch import Preview
from app.web.protocol import ClientMessage, ServerMessage


def schema() -> dict[str, object]:
    return {
        "client_message": TypeAdapter(ClientMessage).json_schema(mode="serialization"),
        "server_message": TypeAdapter(ServerMessage).json_schema(mode="serialization"),
        "payload": TypeAdapter(AnyPayload).json_schema(mode="serialization"),
        "preview": Preview.model_json_schema(mode="serialization"),
    }


if __name__ == "__main__":
    print(json.dumps(schema(), indent=2))
