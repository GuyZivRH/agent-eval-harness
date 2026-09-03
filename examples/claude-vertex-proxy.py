#!/usr/bin/env python3
"""OpenAI-compatible HTTP proxy: Claude via Google Vertex AI.

Used by the AEH + OpenShell + OpenClaw e2e flow so sandboxes can call
https://inference.local/v1 while the host talks to Vertex on :8000.

Supports chat completions **with tools** (OpenAI function-calling ↔ Anthropic
tool_use), which Phase 2 Crabline evals require for OpenClaw `exec`.

  .eval-venv/bin/python examples/claude-vertex-proxy.py

Requires: anthropic[vertex], fastapi, uvicorn, and ADC
(GOOGLE_APPLICATION_CREDENTIALS or `gcloud auth application-default login`).
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from typing import Any, AsyncGenerator, Optional

import anthropic
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="claude-vertex-proxy")

# Use ANTHROPIC_VERTEX_REGION only — do not read CLOUD_ML_REGION.
# Many machines set CLOUD_ML_REGION=global for other GCP tools; Vertex Claude
# requires a regional endpoint (us-east5) and IAM-denies predict on global.
client = anthropic.AnthropicVertex(
    project_id=os.environ.get(
        "ANTHROPIC_VERTEX_PROJECT_ID", "itpc-gcp-eco-eng-claude"
    ),
    region=os.environ.get("ANTHROPIC_VERTEX_REGION", "us-east5"),
)

# Names OpenClaw / OpenShell send → Vertex model IDs
# Opus aliases: this Vertex project has claude-opus-4-6 (4.8 may 429 on quota;
# older @dated opus publisher IDs 404 here).
MODEL_MAP = {
    "claude-sonnet-4": "claude-sonnet-4-5@20250929",
    "claude-sonnet": "claude-sonnet-4-5@20250929",
    "claude": "claude-sonnet-4-5@20250929",
    "claude-haiku-4-5": "claude-haiku-4-5@20251001",
    "claude-haiku": "claude-haiku-4-5@20251001",
    "haiku": "claude-haiku-4-5@20251001",
    "claude-opus-4": "claude-opus-4-6",
    "claude-opus-4-6": "claude-opus-4-6",
    "claude-opus-4-1": "claude-opus-4-6",
    "claude-opus": "claude-opus-4-6",
    "opus": "claude-opus-4-6",
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


def _openai_tools_to_anthropic(tools: Optional[list]) -> list[dict]:
    """OpenAI tools[] → Anthropic tools[]."""
    out: list[dict] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
            fn = tool["function"]
            out.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description") or "",
                    "input_schema": fn.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        elif tool.get("name"):
            # Already Anthropic-ish / flat
            out.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description") or "",
                    "input_schema": tool.get("input_schema")
                    or tool.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
    return out


def _openai_tool_choice_to_anthropic(tool_choice: Any) -> Any:
    if tool_choice is None:
        return anthropic.NOT_GIVEN
    if tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "auto"}  # Anthropic has no none; omit tools instead upstream
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            name = (tool_choice.get("function") or {}).get("name")
            if name:
                return {"type": "tool", "name": name}
        if tool_choice.get("type") in ("auto", "any", "tool"):
            return tool_choice
    return {"type": "auto"}


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text") or "")
                elif "text" in block:
                    parts.append(str(block.get("text") or ""))
        return "".join(parts)
    return str(content)


def _openai_messages_to_anthropic(messages: list) -> tuple[Optional[str], list[dict]]:
    """Convert OpenAI chat messages (incl. tool_calls / role=tool) to Anthropic."""
    system_msg: Optional[str] = None
    anthropic_messages: list[dict] = []

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "system":
            text = _content_to_text(content)
            system_msg = f"{system_msg}\n{text}" if system_msg else text
            continue

        if role == "tool":
            # OpenAI tool result → Anthropic user tool_result block
            tool_call_id = msg.get("tool_call_id") or msg.get("id") or ""
            result_content = _content_to_text(content)
            block = {
                "type": "tool_result",
                "tool_use_id": tool_call_id,
                "content": result_content,
            }
            if anthropic_messages and anthropic_messages[-1]["role"] == "user":
                prev = anthropic_messages[-1]["content"]
                if isinstance(prev, list):
                    prev.append(block)
                else:
                    anthropic_messages[-1]["content"] = [
                        {"type": "text", "text": str(prev)},
                        block,
                    ]
            else:
                anthropic_messages.append({"role": "user", "content": [block]})
            continue

        if role == "assistant":
            blocks: list[dict] = []
            text = _content_to_text(content)
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args_raw = fn.get("arguments", "{}")
                try:
                    args = (
                        json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    )
                except json.JSONDecodeError:
                    args = {"_raw": args_raw}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:8]}",
                        "name": fn.get("name") or tc.get("name") or "",
                        "input": args if isinstance(args, dict) else {"value": args},
                    }
                )
            if not blocks:
                blocks = [{"type": "text", "text": ""}]
            anthropic_messages.append({"role": "assistant", "content": blocks})
            continue

        if role == "user":
            text = _content_to_text(content)
            anthropic_messages.append({"role": "user", "content": text})
            continue

        # Fallback: treat as user text
        anthropic_messages.append(
            {"role": "user", "content": _content_to_text(content)}
        )

    return system_msg, anthropic_messages


def _anthropic_response_to_openai(response: Any, model: str) -> dict:
    """Map Anthropic message → OpenAI chat.completion (incl. tool_calls)."""
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in response.content or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "tool_use":
            tool_calls.append(
                {
                    "id": getattr(block, "id", f"call_{uuid.uuid4().hex[:8]}"),
                    "type": "function",
                    "function": {
                        "name": getattr(block, "name", ""),
                        "arguments": json.dumps(
                            getattr(block, "input", {}) or {}, ensure_ascii=False
                        ),
                    },
                }
            )

    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    finish = "stop"
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish = "tool_calls"
        if message["content"] == "":
            message["content"] = None

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "tool_use":
        finish = "tool_calls"
    elif stop_reason == "max_tokens":
        finish = "length"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:8]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        },
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
    openai_tools = body.get("tools")
    tool_choice = body.get("tool_choice")

    system_msg, anthropic_messages = _openai_messages_to_anthropic(messages)
    anthropic_tools = _openai_tools_to_anthropic(openai_tools)
    anthropic_tool_choice = (
        _openai_tool_choice_to_anthropic(tool_choice)
        if anthropic_tools
        else anthropic.NOT_GIVEN
    )

    create_kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": anthropic_messages,
        "system": system_msg if system_msg else anthropic.NOT_GIVEN,
    }
    if anthropic_tools:
        create_kwargs["tools"] = anthropic_tools
        create_kwargs["tool_choice"] = anthropic_tool_choice

    try:
        # When tools are present, prefer a full (non-stream) Anthropic call and
        # optionally re-emit as SSE — streaming tool_use bridging is lossy.
        if stream and not anthropic_tools:
            return StreamingResponse(
                _stream_response(model, anthropic_messages, system_msg, max_tokens),
                media_type="text/event-stream",
            )

        response = client.messages.create(**create_kwargs)
        openai_body = _anthropic_response_to_openai(response, model)

        if stream:
            return StreamingResponse(
                _completion_as_stream(openai_body),
                media_type="text/event-stream",
            )
        return openai_body
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr, flush=True)
        return JSONResponse(
            status_code=500,
            content={"error": {"message": str(e), "type": "api_error"}},
        )


async def _completion_as_stream(completion: dict) -> AsyncGenerator[str, None]:
    """Emit a non-stream OpenAI completion as chat.completion.chunk SSE."""
    cid = completion.get("id", f"chatcmpl-{uuid.uuid4().hex[:8]}")
    model = completion.get("model", "")
    created = completion.get("created", int(time.time()))
    message = (completion.get("choices") or [{}])[0].get("message") or {}
    finish = (completion.get("choices") or [{}])[0].get("finish_reason") or "stop"

    delta: dict[str, Any] = {"role": "assistant"}
    if message.get("content"):
        delta["content"] = message["content"]
    if message.get("tool_calls"):
        delta["tool_calls"] = []
        for i, tc in enumerate(message["tool_calls"]):
            delta["tool_calls"].append(
                {
                    "index": i,
                    "id": tc.get("id"),
                    "type": "function",
                    "function": tc.get("function"),
                }
            )

    first = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    yield f"data: {json.dumps(first)}\n\n"

    final = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
        "usage": completion.get("usage"),
    }
    yield f"data: {json.dumps(final)}\n\n"
    yield "data: [DONE]\n\n"


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
    print("Tool calling: OpenAI tools ↔ Anthropic tool_use enabled", flush=True)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        timeout_keep_alive=120,
    )
