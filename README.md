# Slackbot for DigitalOcean Inference Hub

FastAPI Slack slash-command bot that routes prompts to DigitalOcean Inference Hub agents/models.

## What this bot does

- Verifies Slack request signatures
- Accepts slash command payloads at `POST /slack/commands`
- Sends prompt to DigitalOcean Inference Hub (`/v1/chat/completions`)
- Replies asynchronously to Slack via `response_url`
- Supports optional per-request agent override:
  - `/agent your question`
  - `/agent agent-id::your question`

## 1) Prerequisites

- Slack app with a slash command (example: `/agent`)
- DigitalOcean Inference Hub model access key
- A model/agent ID available in your Inference Hub account
- Python 3.10+

## 2) Environment variables

Copy `.env.example` to `.env` and fill values:

```bash
cp .env.example .env
```

Required:

- `SLACK_SIGNING_SECRET` — from Slack app Basic Information
- `INFERENCE_HUB_MODEL_ACCESS_KEY` — from DigitalOcean Inference Hub
- `INFERENCE_HUB_DEFAULT_AGENT` — default model/agent ID (for example `openai/gpt-4o`)

Optional:

- `INFERENCE_HUB_BASE_URL` (default: `https://inference.do-ai.run/v1`)
- `INFERENCE_HUB_SYSTEM_PROMPT`

## 3) Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Health endpoint:

```bash
curl http://localhost:8000/healthz
```

Expected:

```json
{"ok":true}
```

## 4) Slack setup

In your Slack app:

1. Create slash command `/agent`
2. Request URL:
   - local dev via tunnel: `https://<your-ngrok-domain>/slack/commands`
   - production: `https://<your-app-domain>/slack/commands`
3. Enable scopes (minimum):
   - `commands`
4. Install/reinstall app to workspace

Slash command examples:

- `/agent summarize this incident report`
- `/agent anthropic/claude-3-5-sonnet::draft a release note from this text`

## 5) Deploy on DigitalOcean App Platform

This repo includes:

- `Dockerfile`
- `do-app.yaml` app spec

### Option A: One-click from spec

```bash
doctl apps create --spec do-app.yaml
```

Then set app-level environment variables in App Platform UI (or in the spec if you prefer managed secrets).

### Option B: App Platform via GitHub UI

1. Push this repo to GitHub
2. Create App in DigitalOcean App Platform from repository
3. Choose Dockerfile build
4. Set runtime env vars:
   - `SLACK_SIGNING_SECRET` (secret)
   - `INFERENCE_HUB_MODEL_ACCESS_KEY` (secret)
   - `INFERENCE_HUB_DEFAULT_AGENT`
   - optional `INFERENCE_HUB_SYSTEM_PROMPT`
5. Deploy

Once deployed, copy your app URL into the Slack slash command Request URL and reinstall the Slack app.

## 6) Verify end-to-end

1. Run `/agent hello` in Slack
2. Confirm immediate ephemeral ack (`🤖 Thinking...`)
3. Confirm follow-up response from Inference Hub
4. Check App Platform logs if failures occur

## Notes

- Slack requires the command endpoint to respond within ~3 seconds. The app acks immediately and handles inference in background.
- Error text from Inference Hub is truncated and returned to Slack to simplify debugging.
