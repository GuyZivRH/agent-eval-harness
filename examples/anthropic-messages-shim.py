#!/usr/bin/env python3
"""Translate Anthropic /v1/messages → OpenAI chat.completions on :8000."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

UPSTREAM = os.environ.get("UPSTREAM_OPENAI", "http://127.0.0.1:8000")
app = FastAPI()


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text") or "")
                elif "text" in block:
                    parts.append(str(block["text"]))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


@app.get("/health")
def health():
    return {"status": "ok", "upstream": UPSTREAM}


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    model = body.get("model") or "claude-sonnet-4"
    # Map Vertex publisher ids back to proxy aliases when possible
    if "@" in model or model.startswith("claude-sonnet-4-5"):
        model = "claude-sonnet-4"

    oai_messages = []
    system = body.get("system")
    if isinstance(system, list):
        system = _content_to_text(system)
    if system:
        oai_messages.append({"role": "system", "content": system})

    for msg in body.get("messages") or []:
        role = msg.get("role") or "user"
        oai_messages.append({"role": role, "content": _content_to_text(msg.get("content"))})

    payload = {
        "model": model,
        "messages": oai_messages,
        "max_tokens": body.get("max_tokens") or 1024,
        "temperature": body.get("temperature", 0),
    }
    # Ignore tools for judges — score.py structured output may use tools;
    # pass through if present (proxy supports tools).
    if body.get("tools"):
        # Convert Anthropic tools → OpenAI function tools
        tools = []
        for t in body["tools"]:
            tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description") or "",
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            })
        payload["tools"] = tools
        tc = body.get("tool_choice")
        if tc == "any" or (isinstance(tc, dict) and tc.get("type") == "any"):
            payload["tool_choice"] = "required"
        elif isinstance(tc, dict) and tc.get("type") == "tool":
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": tc.get("name")},
            }

    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(f"{UPSTREAM}/v1/chat/completions", json=payload)
        if r.status_code >= 400:
            return JSONResponse(status_code=r.status_code, content={"error": {"message": r.text}})
        data = r.json()

    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content_blocks = []
    if message.get("content"):
        content_blocks.append({"type": "text", "text": message["content"]})
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:
            args = {"raw": fn.get("arguments")}
        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or "toolu_1",
            "name": fn.get("name") or "unknown",
            "input": args,
        })
    if not content_blocks:
        content_blocks = [{"type": "text", "text": ""}]

    stop = choice.get("finish_reason") or "end_turn"
    stop_map = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
    usage = data.get("usage") or {}
    return {
        "id": data.get("id") or "msg_shim",
        "type": "message",
        "role": "assistant",
        "model": data.get("model") or model,
        "content": content_blocks,
        "stop_reason": stop_map.get(stop, stop),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens") or 0,
            "output_tokens": usage.get("completion_tokens") or 0,
        },
    }


if __name__ == "__main__":
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8001")), log_level="info")
