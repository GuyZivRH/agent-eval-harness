#!/usr/bin/env python3
"""Minimal OpenAI-compatible mock LLM for AEH OpenShell smoke runs."""
from __future__ import annotations

import json
import time
import uuid
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {
                "id": "claude-sonnet-4",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "mock",
            }
        ],
    }


def _reply(messages: list) -> str:
    text = " ".join(
        str(m.get("content", "")) for m in messages if m.get("role") == "user"
    ).lower()
    if "capital of france" in text:
        return "Paris"
    if "15 + 27" in text or "15+27" in text:
        return "42"
    if "color is the sky" in text:
        return "Blue"
    if "hello" in text:
        return "Hello"
    return "ok"


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    model = body.get("model", "claude-sonnet-4")
    content = _reply(body.get("messages", []))
    return JSONResponse(
        {
            "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
