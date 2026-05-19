# Slack Grammar Bot (Socket Mode + Inference Hub)

Python service that connects to Slack with **Socket Mode** (outbound WebSocket), handles a slash command, sends the user prompt to **DigitalOcean Inference Hub** (`.../chat/completions`), and replies asynchronously through Slack's `response_url`.

Socket Mode means local and DOCC deployments do **not** need a public Slack request URL, Cloudflare tunnel, or externally resolvable DNS for Slack callbacks. The container still exposes `GET /healthz` on `PORT` for readiness checks.

---

## Features

| Area | Behavior |
|------|----------|
| **Slack** | Uses `SLACK_APP_TOKEN` (`xapp-...`) and `SLACK_BOT_TOKEN` (`xoxb-...`) over Socket Mode. Immediate ephemeral ack (`Thinking...`), then delayed reply via Slack's response URL. |
| **Inference Hub** | OpenAI-style chat payload; optional `agent-id::prompt` override. Serverless URLs send `model`; GenAI Agent hosts (`agents.do-ai.run`) omit top-level `model` per deployment URL. |
| **Operations** | `GET /healthz` always returns `{"ok":true}` on the local container port. No inbound Slack route is required. |

---

## Repository Layout

| Path | Purpose |
|------|---------|
| `app/main.py` | Entry point: loads settings, starts health server and Socket Mode. |
| `app/bot.py` | Slack slash-command handlers (Bolt lazy listeners). |
| `app/inference.py` | Inference Hub HTTP client. |
| `app/config.py` | Environment-backed `Settings` and validation. |
| `app/health.py` | Background `/healthz` server for DOCC readiness. |
| `requirements.txt` | Python dependencies (`slack-bolt`, `httpx`, `python-dotenv`). |
| `Dockerfile` | Python 3.11 runtime, starts `python -m app.main`. |
| `Makefile` | Local helpers for install, run, health check, and Docker. |
| `docc/manifest.json` | DOCC deployment manifest with Socket Mode secrets from Vault. |
| `docc/vault-puff-secrets.example.json` | Example Vault payload (copy to `vault-puff-secrets.json`, gitignored). |
| `.env.example` | Local env template. |

---

## Slack App Setup

1. Enable **Socket Mode** in the Slack app.
2. Create an **app-level token** with `connections:write`; put it in `SLACK_APP_TOKEN`.
3. Add a bot token with scopes:
   - `commands`
   - `chat:write` (recommended for replies)
4. Create the slash command (default docs use `/grammar`).
5. Install or reinstall the app to your workspace.

With Socket Mode enabled, this app receives the slash command over Slack's outbound WebSocket connection. You do not need Cloudflare Tunnel for local testing.

---

## Environment Variables

Copy the example file and edit:

```bash
cp .env.example .env
```

Required:

| Variable | Description |
|----------|-------------|
| `SLACK_APP_TOKEN` | Slack app-level token (`xapp-...`) with `connections:write`. |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) with slash command permissions. |
| `INFERENCE_HUB_MODEL_ACCESS_KEY` | Inference Hub Bearer token. Alias: `MODEL_ACCESS_KEY`. |
| `INFERENCE_HUB_DEFAULT_AGENT` | Default model/agent id when the user does not use `agent-id::...`. |

Optional:

| Variable | Default / notes |
|----------|------------------|
| `PORT` | `8080`; used only for local/DOCC health checks. |
| `INFERENCE_HUB_BASE_URL` | `https://inference.do-ai.run/v1`. For hosted GenAI agents, use `https://YOUR_AGENT.agents.do-ai.run/api/v1`. |
| `INFERENCE_HUB_SYSTEM_PROMPT` | Built-in Slack-oriented system message if unset. |
| `SLACK_COMMAND_NAME` | `/grammar`; must match the Slack slash command. |
| `SLACK_REPLY_VISIBILITY` | `ephemeral` (default) or `in_channel` for the delayed reply. |
| `LOG_LEVEL` | `INFO` by default. |

---

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
make run
```

In another terminal:

```bash
make healthz
```

Expected: `{"ok":true}`.

Then run the slash command in Slack:

```text
/grammar summarize this incident report
/grammar anthropic/claude-3-5-sonnet::draft a release note from this text
```

---

## Docker

```bash
docker build -t slack-grammar-bot:local .
docker run --rm -p 8080:8080 --env-file .env slack-grammar-bot:local
curl -s http://localhost:8080/healthz
```

---

## DOCC and Turtle Vault

Populate Vault using the example payload:

```bash
cp docc/vault-puff-secrets.example.json docc/vault-puff-secrets.json
# Edit docc/vault-puff-secrets.json (never commit real secrets)

vault kv put -mount=secret-versioned solutions/slack-grammar-bot/puff @docc/vault-puff-secrets.json
```

`docc/manifest.json` intentionally avoids public TLS domains and proxy ports for Slack. Slack traffic is outbound Socket Mode; the exposed container port is only for `GET /healthz`.

---

## Verify End-to-End

1. Start the service locally or in DOCC.
2. Confirm `/healthz` returns `{"ok":true}`.
3. In Slack, run `/grammar hello`.
4. Confirm immediate ephemeral `Thinking...`.
5. Confirm the delayed reply includes your original text and the model response.

Inference Hub errors are surfaced in Slack and also logged by the process.
