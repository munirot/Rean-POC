# Staff chat: choosing / hosting the LLM

The staff chat talks to any **OpenAI-compatible** `/chat/completions` endpoint, so
the model is swappable with env vars — no code change. The default is a local
Ollama so student data stays on-prem.

## Pointing at a model

Set these in `face-service/.env` (see [`app/config.py`](../face-service/app/config.py)):

| Env | Meaning | Default |
|-----|---------|---------|
| `CHAT_BASE_URL` | OpenAI-compatible base URL (must end at the `/v1`-style root) | `http://localhost:11434/v1` (Ollama) |
| `CHAT_MODEL`    | Model name as the endpoint knows it | `gemma4:31b-cloud` |
| `CHAT_API_KEY`  | Bearer token (Ollama ignores it) | `ollama` |
| `CHAT_ENABLED`  | Master on/off | `true` |

Examples of a self-hosted endpoint: another Ollama, **vLLM**, **TGI**, or a
**LiteLLM** proxy — all expose `/v1/chat/completions`. Restart the service after
changing these (uvicorn isn't run with `--reload`).

## Conversation memory is sized to the model's context window

Each turn, the server replays recent history to the model. The amount is a **token
budget derived from the model's context window**:

```
history budget = CHAT_MODEL_CONTEXT − CHAT_RESERVE_TOKENS   (also capped at CHAT_CONTEXT_MSGS turns)
```

| Env | Meaning | Default |
|-----|---------|---------|
| `CHAT_MODEL_CONTEXT` | Your model's **real** context window, in tokens | `8192` |
| `CHAT_RESERVE_TOKENS` | Headroom for system prompt + tool schemas + fetched data + the reply | `4000` |
| `CHAT_CONTEXT_MSGS` | Hard cap on number of prior turns | `20` |

Set `CHAT_MODEL_CONTEXT` to the **truth** for your hosted model. The budget then
adapts automatically — larger memory on a big-context model, safely smaller on a
tight one — and can never silently over-run the window.

### ⚠️ The self-hosting trap: silent truncation (Ollama)

Self-hosted runtimes often default to a **small** context window and quietly drop
the oldest tokens when you exceed it — so memory degrades with no error.

Ollama is the common case: over the OpenAI `/v1` endpoint the context length comes
from the model's Modelfile default (historically **2048–4096**) or the
`OLLAMA_CONTEXT_LENGTH` server env — **our request can't set it per call**. So if
`CHAT_MODEL_CONTEXT` says 8192 but Ollama is serving a 4096 window, the extra
history is discarded before the model sees it.

**Fix — raise the server-side window to at least `CHAT_MODEL_CONTEXT`:**

```bash
# option A: server-wide, when starting Ollama
OLLAMA_CONTEXT_LENGTH=8192 ollama serve
```
```dockerfile
# option B: bake into the model
# Modelfile
FROM your-model
PARAMETER num_ctx 8192
```
Then `ollama create your-model -f Modelfile` and point `CHAT_MODEL` at it.

vLLM and TGI expose the model's full context by default, so they don't hit this
trap — but the window is still finite; keep `CHAT_MODEL_CONTEXT` honest.

### Rule of thumb

Keep `CHAT_MODEL_CONTEXT` ≤ the window your server actually serves, and leave
`CHAT_RESERVE_TOKENS` roomy enough for the system prompt, tool schemas, the biggest
data blob a tool returns (compose caps data at ~6k chars ≈ 1.5k tokens), and the
reply. Bigger windows cost VRAM and latency when self-hosted, not per-token billing.
