# Gumloop Proxy

Use [Gumloop's](https://gumloop.com) free-tier AI agents (GPT-5.5, Claude, Gemini, etc.) with **Claude Code** or any **Anthropic Messages API** compatible client — no paid API key required.

## How It Works

Gumloop's free tier gives 500 credits but locks API access behind a paywall. This proxy bypasses that restriction by bridging Gumloop's **web app WebSocket** to a local **Anthropic-compatible API** through a Chrome extension.

```
Claude Code → localhost:8082 (FastAPI proxy)
                     ↕ WebSocket localhost:8083
         Chrome Extension (in Gumloop tab)
                     ↕ auto-types in chat box
            Gumloop WebSocket + hCaptcha
```

### Architecture

1. **FastAPI Proxy** (`proxy/gumloop_bridge.py`) — Listens on `:8082`, accepts Anthropic Messages API format
2. **Chrome Extension** (`extension/`) — Intercepts Gumloop's WebSocket, auto-types messages, captures responses
3. **WebSocket Bridge** (`:8083`) — Internal bridge between proxy and extension

## Quick Start

### 1. Install Python Dependencies

```bash
cd gumloop-proxy
python -m venv .venv
source .venv/bin/activate    # Linux/Mac
# or: .venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

### 2. Start the Proxy

```bash
python proxy/gumloop_bridge.py
```

Proxy starts on:
- `http://127.0.0.1:8082` — Anthropic Messages API (for Claude Code)
- `ws://127.0.0.1:8083` — WebSocket bridge (for Chrome Extension)

### 3. Install Chrome Extension

1. Open `edge://extensions/` or `chrome://extensions/`
2. Enable **Developer mode**
3. Click **Load unpacked** → select the `extension/` folder
4. The "Gumloop Bridge" extension appears

### 4. Open Gumloop

1. Go to [https://www.gumloop.com/chat](https://www.gumloop.com/chat)
2. Log in with your account
3. Send a message (e.g., "hi") to the agent — this captures your JWT auth token
4. Check browser console (F12) for: `[Gumloop Bridge] ✅ Captured JWT`

### 5. Use with Claude Code

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8082
export ANTHROPIC_AUTH_TOKEN=***
claude
```

That's it! Claude Code now runs on Gumloop's AI models using your free credits.

## How The Extension Works

The extension uses an **auto-type approach**:

1. When Claude Code sends a request, the proxy forwards it to the extension
2. The extension **auto-types** the message into Gumloop's chat box (ProseMirror editor)
3. It **clicks the submit button** — Gumloop handles hCaptcha naturally
4. The extension **intercepts the WebSocket response** and streams it back
5. The proxy translates it to Anthropic Messages API format

This approach is necessary because:
- Gumloop's hCaptcha tokens are **single-use** — can't be reused
- The WebSocket protocol is **proprietary** — not OpenAI-compatible
- JWT tokens **expire** (1 hour) — extension auto-captures fresh ones

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/messages` | Anthropic Messages — streaming and non-streaming |
| `POST` | `/v1/messages/count_tokens` | Rough token estimate |
| `GET`  | `/v1/models` | Model list |
| `GET`  | `/health` | Health check + bridge status |

## Requirements

- Python 3.11+
- Chrome or Edge browser
- Active Gumloop account (free tier works)
- Gumloop tab must stay open while using the proxy

## Limitations

- **Gumloop tab must remain open** — the extension operates within the page context
- **~30 credits per request** — Gumloop charges credits per message
- **JWT expires after 1 hour** — re-chat with the agent to refresh
- **One request at a time** — Gumloop's WebSocket is single-threaded
- **Model is determined by Gumloop** — currently defaults to `gpt-5.5`

## File Structure

```
gumloop-proxy/
├── README.md
├── requirements.txt
├── run.sh                    # Quick start script
├── proxy/
│   └── gumloop_bridge.py     # FastAPI proxy server + WS bridge
└── extension/
    ├── manifest.json          # Chrome Extension MV3 config
    ├── background.js          # Service worker (injects to MAIN world)
    ├── content.js             # Content script (isolated world bridge)
    └── injected.js            # MAIN world interceptor (auto-types + captures)
```

## Disclaimer

This is a community tool, not affiliated with Gumloop. It accesses Gumloop's web interface programmatically. Review Gumloop's Terms of Service before using. Use responsibly to avoid account suspension.

## License

MIT
