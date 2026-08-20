#!/usr/bin/env python3
"""OpenAI-compatible HTTP proxy: Claude via Google Vertex AI.

Used by the AEH + OpenShell + OpenClaw e2e flow so sandboxes can call
https://inference.local/v1 while the host talks to Vertex on :8000.

  python3 examples/claude-vertex-proxy.py

Requires: anthropic[vertex], fastapi, uvicorn, and ADC
(GOOGLE_APPLICATION_CREDENTIALS or `gcloud auth application-default login`).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import AsyncGenerator

import anthropic
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="claude-vertex-proxy")

client = anthropic.AnthropicVertex(
    project_id=os.environ.get(
        "ANTHROPIC_VERTEX_PROJECT_ID", "itpc-gcp-eco-eng-claude"
    ),
    region=os.environ.get("CLOUD_ML_REGION", "us-east5"),
)

# Names OpenClaw / OpenShell send → Vertex model IDs
MODEL_MAP = {
    "claude-sonnet-4": "claude-sonnet-4-5@20250929",
    "claude-sonnet": "claude-sonnet-4-5@20250929",
    "claude": "claude-sonnet-4-5@20250929",
}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": mid,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "anthropic",
            }
            for mid in MODEL_MAP
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": f"Invalid JSON: {e}"}},
        )

    requested = body.get("model", "claude-sonnet-4")
    model = MODEL_MAP.get(requested, requested)
    messages = body.get("messages", [])
    max_tokens = body.get("max_tokens", 4096)
    stream = body.get("stream", False)

    system_msg = None
    anthropic_messages = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if role == "system":
            system_msg = content
        else:
            anthropic_messages.append({"role": role, "content": content})

    try:
        if stream:
            return StreamingResponse(
                _stream_response(model, anthropic_messages, system_msg, max_tokens),
                media_type="text/event-stream",
            )

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=anthropic_messages,
            system=system_msg if system_msg else anthropic.NOT_GIVEN,
        )
        text = response.content[0].text if response.content else ""
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": (
                    response.usage.input_tokens + response.usage.output_tokens
                ),
            },
        }
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr, flush=True)
        return JSONResponse(
            status_code=500,
            content={"error": {"message": str(e), "type": "api_error"}},
        )


async def _stream_response(
    model: str, messages: list, system: str | None, max_tokens: int
) -> AsyncGenerator[str, None]:
    try:
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
            system=system if system else anthropic.NOT_GIVEN,
        ) as stream:
            for text in stream.text_stream:
                chunk = {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": text},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(chunk)}\n\n"

            final_msg = stream.get_final_message()
            usage = {
                "prompt_tokens": final_msg.usage.input_tokens,
                "completion_tokens": final_msg.usage.output_tokens,
                "total_tokens": (
                    final_msg.usage.input_tokens + final_msg.usage.output_tokens
                ),
            }

        final_chunk = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": usage,
        }
        yield f"data: {json.dumps(final_chunk)}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n"


if __name__ == "__main__":
    print("Starting Claude Vertex proxy on http://0.0.0.0:8000", flush=True)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        timeout_keep_alive=120,
    )
