"""
Gumloop Bridge Proxy Server
============================
Listens on:
  - :8082  → Anthropic Messages API (/v1/messages) for Claude Code
  - :8083  → WebSocket bridge for Chrome Extension

Flow:
  Claude Code → POST :8082/v1/messages
             → extract last user message
             → send via WS :8083 to Chrome Extension
             → Extension sends to Gumloop WebSocket
             → Stream response back as Anthropic SSE
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
import logging
from typing import Dict, Set

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
import websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gumloop-bridge")

# ---------------------------------------------------------------------------
# Bridge state: track pending requests waiting for Gumloop responses
# ---------------------------------------------------------------------------
class BridgeState:
    def __init__(self):
        self.extension_ws: websockets.WebSocketServerProtocol | None = None
        self.pending: Dict[str, asyncio.Future] = {}
        self.deltas: Dict[str, list[str]] = {}
        self.bridge_ready = False

    async def extension_connected(self, ws):
        self.extension_ws = ws
        self.bridge_ready = True
        log.info("Chrome Extension connected to bridge")

    async def extension_disconnected(self):
        self.extension_ws = None
        self.bridge_ready = False
        log.warning("Chrome Extension disconnected")

    async def send_to_extension(self, request_id: str, user_message: str):
        """Send a message request to the Chrome Extension via WS."""
        if not self.extension_ws:
            raise RuntimeError("Chrome Extension not connected. Open Gumloop and ensure extension is active.")
        msg = json.dumps({
            "type": "send-to-gumloop",
            "requestId": request_id,
            "payload": user_message,
        })
        await self.extension_ws.send(msg)
        log.info(f"Sent request {request_id} to extension ({len(user_message)} chars)")

    def create_future(self, request_id: str) -> asyncio.Future:
        fut = asyncio.get_event_loop().create_future()
        self.pending[request_id] = fut
        self.deltas[request_id] = []
        return fut

    def resolve(self, request_id: str, content: str, usage: dict | None = None, credits: float | None = None):
        self.deltas[request_id] = [content]  # final full content
        if request_id in self.pending and not self.pending[request_id].done():
            self.pending[request_id].set_result({
                "content": content,
                "usage": usage,
                "credits": credits,
                "error": None
            })

    def resolve_error(self, request_id: str, error: str):
        if request_id in self.pending and not self.pending[request_id].done():
            self.pending[request_id].set_result({
                "content": "",
                "usage": None,
                "credits": None,
                "error": error
            })

    def add_delta(self, request_id: str, delta: str):
        deltas = self.deltas.setdefault(request_id, [])
        deltas.append(delta)
        # Notify any streaming waiter
        # (We use an asyncio.Event-like pattern via future for completion,
        #  but deltas are collected by the streaming endpoint directly)


state = BridgeState()


# ---------------------------------------------------------------------------
# WebSocket server for Chrome Extension (port 8083)
# ---------------------------------------------------------------------------
async def extension_handler(websocket):
    await state.extension_connected(websocket)
    try:
        async for raw in websocket:
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "bridge-ready":
                log.info("Bridge ready signal received from extension")

            elif mtype == "gumloop-delta":
                rid = msg.get("requestId")
                delta = msg.get("delta", "")
                state.add_delta(rid, delta)

            elif mtype == "gumloop-response":
                rid = msg.get("requestId")
                content = msg.get("content", "")
                usage = msg.get("usage")
                credits = msg.get("credits")
                error = msg.get("error")
                if error:
                    log.error(f"Extension error for {rid}: {error}")
                    state.resolve_error(rid, error)
                else:
                    log.info(f"Response complete for {rid}: {len(content)} chars, usage={usage}")
                    state.resolve(rid, content, usage, credits)

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        await state.extension_disconnected()


async def start_ws_server():
    server = await websockets.serve(extension_handler, "127.0.0.1", 8083)
    log.info("WebSocket bridge listening on ws://127.0.0.1:8083")
    return server


# ---------------------------------------------------------------------------
# FastAPI app for Anthropic Messages API (port 8082)
# ---------------------------------------------------------------------------
app = FastAPI(title="Gumloop Bridge Proxy")


def extract_user_message(body: dict) -> str:
    """Extract the last user message from Anthropic Messages format.
    
    Optimized for coding: inject system prompt into user message,
    send only last 3 messages to save credits, and format tool results.
    """
    messages = body.get("messages", [])
    system = body.get("system", "")

    # Gumloop ignores separate system prompts, so merge into user message
    parts = []
    if system:
        if isinstance(system, list):
            system = "\n".join(b.get("text", "") for b in system if b.get("type") == "text")
        if system.strip():
            parts.append(f"Instructions: {system.strip()}\n")

    # Send only last 3 messages to save credits (Gumloop charges per message)
    recent = messages[-3:] if len(messages) > 3 else messages

    for msg in recent:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Anthropic content blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        # Format tool results for Gumloop agent
                        tool_content = block.get("content", "")
                        if isinstance(tool_content, list):
                            tool_content = "\n".join(
                                t.get("text", "") for t in tool_content if isinstance(t, dict)
                            )
                        text_parts.append(f"[Tool Output]\n{str(tool_content)[:2000]}")
                    elif block.get("type") == "tool_use":
                        # Format tool calls
                        name = block.get("name", "unknown")
                        inp = block.get("input", {})
                        text_parts.append(f"[Tool Call: {name}]\n{json.dumps(inp)[:500]}")
            content = "\n".join(text_parts)
        elif not isinstance(content, str):
            content = str(content)

        if role == "user":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"[Previous assistant response]: {content[:500]}")

    return "\n\n".join(parts)


def anthropic_sse_stream(request_id: str, model: str):
    """Generate Anthropic SSE events from Gumloop streaming response."""
    # message_start
    msg_id = f"msg_{uuid.uuid4().hex[:24]}"
    start_data = json.dumps({"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "model": model, "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": 0}}})
    yield f"event: message_start\ndata: {start_data}\n\n"

    # content_block_start
    cbs_data = json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    yield f"event: content_block_start\ndata: {cbs_data}\n\n"

    # Stream deltas as they arrive
    last_count = 0
    timeout = 600  # 10 minutes max for coding responses
    start = time.time()

    while time.time() - start < timeout:
        deltas = state.deltas.get(request_id, [])
        if len(deltas) > last_count:
            for i in range(last_count, len(deltas)):
                delta = deltas[i]
                # Check if this is the final resolution (single full content)
                if i == 0 and len(deltas) == 1:
                    # This is a final response (non-streaming from extension)
                    # Send as one delta
                    pass
                event_data = json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": delta}})
                yield f"event: content_block_delta\ndata: {event_data}\n\n"
            last_count = len(deltas)

        # Check if future is done
        if request_id in state.pending:
            fut = state.pending[request_id]
            if fut.done():
                result = fut.result()
                if result.get("error"):
                    error_data = json.dumps({"type": "error", "error": {"type": "upstream_error", "message": result["error"]}})
                    yield f"event: error\ndata: {error_data}\n\n"
                else:
                    usage = result.get("usage") or {}
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)

                    yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"

                    msg_delta = json.dumps({
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                        "usage": {"output_tokens": output_tokens}
                    })
                    yield f"event: message_delta\ndata: {msg_delta}\n\n"

                    yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
                break

        time.sleep(0.05)

    # Cleanup
    state.pending.pop(request_id, None)
    state.deltas.pop(request_id, None)


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    user_message = extract_user_message(body)
    stream = body.get("stream", False)
    model = body.get("model", "gumloop-agent")

    request_id = str(uuid.uuid4())
    log.info(f"POST /v1/messages → request_id={request_id}, stream={stream}, msg_len={len(user_message)}")

    if not state.bridge_ready:
        return JSONResponse(
            status_code=503,
            content={"type": "error", "error": {"type": "bridge_not_connected",
                "message": "Chrome Extension not connected. Open https://www.gumloop.com and ensure the Gumloop Bridge extension is active."}}
        )

    if stream:
        # Send request to extension first
        try:
            await state.send_to_extension(request_id, user_message)
        except Exception as e:
            return JSONResponse(status_code=502, content={"type": "error", "error": {"message": str(e)}})

        return StreamingResponse(
            anthropic_sse_stream(request_id, model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
        )
    else:
        # Non-streaming
        try:
            await state.send_to_extension(request_id, user_message)
        except Exception as e:
            return JSONResponse(status_code=502, content={"type": "error", "error": {"message": str(e)}})

        fut = state.create_future(request_id)
        # Replace with bridge future (the send already created it implicitly)
        if request_id not in state.pending:
            state.create_future(request_id)

        try:
            result = await asyncio.wait_for(state.pending[request_id], timeout=600)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=504, content={"type": "error", "error": {"message": "Timeout waiting for Gumloop response"}})

        if result.get("error"):
            return JSONResponse(status_code=502, content={"type": "error", "error": {"message": result["error"]}})

        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        response = {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": result["content"]}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": (result.get("usage") or {}).get("input_tokens", 0),
                "output_tokens": (result.get("usage") or {}).get("output_tokens", 0),
            }
        }
        state.pending.pop(request_id, None)
        state.deltas.pop(request_id, None)
        return JSONResponse(content=response)


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    body = await request.json()
    text = extract_user_message(body)
    # Rough estimate
    tokens = max(1, len(text) // 4)
    return JSONResponse(content={"input_tokens": tokens})


@app.get("/v1/models")
async def models():
    return JSONResponse(content={
        "data": [{"id": "gumloop-agent", "object": "model", "owned_by": "gumloop"}],
        "object": "list"
    })


@app.get("/health")
async def health():
    return JSONResponse(content={
        "status": "ok",
        "bridge_connected": state.bridge_ready,
        "pending_requests": len(state.pending),
    })


# ---------------------------------------------------------------------------
# Main: start both WS bridge and HTTP server
# ---------------------------------------------------------------------------
async def main():
    # Start WebSocket bridge server
    ws_server = await start_ws_server()

    # Start uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=8082, log_level="info")
    server = uvicorn.Server(config)

    log.info("=" * 60)
    log.info("Gumloop Bridge Proxy started")
    log.info("  HTTP API:  http://127.0.0.1:8082  (for Claude Code)")
    log.info("  WS Bridge: ws://127.0.0.1:8083    (for Chrome Extension)")
    log.info("=" * 60)

    await server.serve()
    ws_server.close()
    await ws_server.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
