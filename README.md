# Slackbot for DigitalOcean Inference Hub

FastAPI Slack slash-command bot that routes prompts to DigitalOcean Inference Hub agents/models.

## What this bot does

- Verifies Slack request signatures
- Accepts slash command payloads at `POST /slack/commands`
- Sends prompt to DigitalOcean Inference Hub (`/v1/chat/completions`)
- Replies asynchronously to Slack via `response_url`
- Supports optional per-request agent override:
  - `/grammar your question`
  - `/grammar agent-id::your question`

## 1) Prerequisites

- Slack app with a slash command (example: `/grammar`)
- DigitalOcean Inference Hub model access key
- A model/agent ID available in your Inference Hub account
- Python 3.10+

## 2) Environment variables

Copy `.env.example` to `.env` and fill values:

```bash
cp .env.example .env
```

Required (the app exits at import time if any are missing):

- `SLACK_SIGNING_SECRET` — Slack app → Basic Information → Signing Secret
- `INFERENCE_HUB_MODEL_ACCESS_KEY` — Inference Hub model access key (Bearer token for `/v1/chat/completions`). **Alias:** you may set `MODEL_ACCESS_KEY` instead; the code reads `INFERENCE_HUB_MODEL_ACCESS_KEY` first, then falls back to `MODEL_ACCESS_KEY`.
- `INFERENCE_HUB_DEFAULT_AGENT` — default model id when the user does not use `agent-id::...` (must be an id your account can call; confirm via `GET /v1/models` if needed)

Optional:

- `INFERENCE_HUB_BASE_URL` — default `https://inference.do-ai.run/v1`
- `INFERENCE_HUB_SYSTEM_PROMPT` — system message for every request; has a built-in default if unset
- `SLACK_COMMAND_NAME` — default `/grammar`; only used in usage text (your Slack app must still define the real slash command to match)
- `SLACK_REPLY_VISIBILITY` — `ephemeral` (default) or `in_channel`. The delayed reply **always echoes your original slash text**; set `in_channel` if you want that reply as a normal channel message everyone sees in history (the initial “Thinking…” stays ephemeral).

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

1. Create slash command `/grammar`
2. Request URL:
   - local dev via tunnel: `https://<your-ngrok-domain>/slack/commands`
   - production: `https://<your-app-domain>/slack/commands`
3. Enable scopes (minimum):
   - `commands`
4. Install/reinstall app to workspace

Slash command examples:

- `/grammar summarize this incident report`
- `/grammar anthropic/claude-3-5-sonnet::draft a release note from this text`

## 5) docc (DigitalOcean internal)

For deploying on **docc** (VPN), follow the internal guides—this repo’s `Dockerfile` is compatible with the usual flow: build for **`linux/amd64`**, tag and **push** to `docker.internal.digitalocean.com`, then **`docc deploy`** with a **`manifest.json`** (explicit image tag or digest; **not** `latest`). Use **`--secret-auth token,<token>`** (or your team’s auth) when the manifest references [Turtle Vault / `secrets`](https://docc-user-guide.internal.digitalocean.com/applications/secrets.html).

**Primary references (internal):**

- [Getting Started with docc](https://docc-getting-started.internal.digitalocean.com/) — [Setup / CLI](https://docc-getting-started.internal.digitalocean.com/part1/setup.html), [Containerizing](https://docc-getting-started.internal.digitalocean.com/part1/containerizing-the-application.html), [Deploying](https://docc-getting-started.internal.digitalocean.com/part1/deploying-the-application.html)
- [docc User Guide](https://docc-user-guide.internal.digitalocean.com/introduction.html) — [Installation](https://docc-user-guide.internal.digitalocean.com/installation.html), [Secrets](https://docc-user-guide.internal.digitalocean.com/applications/secrets.html)

**docc-oriented container checks:**

```bash
docker build -t docker.internal.digitalocean.com/$MY_NAME/slack-grammar-bot:v1 --platform linux/amd64 .
docker run --rm -p 8080:8080 --env-file .env docker.internal.digitalocean.com/$MY_NAME/slack-grammar-bot:v1
curl -s http://localhost:8080/healthz
```

Then `docker push …` and point your manifest’s container `image` at that tag before `docc deploy`.

## 6) Deploy on DigitalOcean App Platform (public cloud)

This repo also includes:

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

## 7) Verify end-to-end

1. Run `/grammar hello` in Slack
2. Confirm immediate ephemeral ack (`🤖 Thinking...`)
3. Confirm follow-up response from Inference Hub
4. Check App Platform or docc logs if failures occur

## Notes

- Slack requires the command endpoint to respond within ~3 seconds. The app acks immediately and handles inference in background.
- Error text from Inference Hub is truncated and returned to Slack to simplify debugging.
