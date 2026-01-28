import os
import time
import hmac
import hashlib
import asyncio
import requests
import json

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from gradient import AsyncGradient

load_dotenv()

app = FastAPI()

# =====================
# Environment variables
# =====================

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
GRADIENT_MODEL_ACCESS_KEY = os.getenv("GRADIENT_MODEL_ACCESS_KEY")

if not SLACK_SIGNING_SECRET:
    raise RuntimeError("SLACK_SIGNING_SECRET not set")

if not SLACK_BOT_TOKEN:
    raise RuntimeError("SLACK_BOT_TOKEN not set")

if not GRADIENT_MODEL_ACCESS_KEY:
    raise RuntimeError("GRADIENT_MODEL_ACCESS_KEY not set")

# =====================
# Gradient client
# =====================

gradient_client = AsyncGradient(
    model_access_key=GRADIENT_MODEL_ACCESS_KEY
)

# =====================
# LLM helper (ONLY place that calls the model)
# =====================

async def rewrite_text(text: str, instructions: str = "") -> str:
    system_prompt = (
        "You are a strict grammar correction assistant.\n\n"
        "Your task:\n"
        "- Fix grammar, spelling, punctuation, and sentence boundaries.\n"
        "- Improve clarity ONLY when grammar is incorrect or ambiguous.\n\n"
        "Hard rules (must follow):\n"
        "- Do NOT rewrite sentences for style.\n"
        "- Do NOT change tone or intent.\n"
        "- Do NOT infer names or entities from misspellings.\n\n"
        "Return ONLY the corrected text."
    )

    if instructions:
        system_prompt += f"\n\nAdditional user instructions:\n{instructions}"

    response = await gradient_client.chat.completions.create(
        model="openai-gpt-4o",
        temperature=0.0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    )

    return response.choices[0].message.content.strip()

# =====================
# Slack signature verification
# =====================

def verify_slack_request(*, raw_body: bytes, timestamp: str, slack_signature: str):
    now = int(time.time())
    req_ts = int(timestamp)

    if abs(now - req_ts) > 60 * 5:
        raise HTTPException(status_code=401, detail="Stale request")

    sig_basestring = b"v0:" + timestamp.encode() + b":" + raw_body
    computed_signature = (
        "v0="
        + hmac.new(
            SLACK_SIGNING_SECRET.encode(),
            sig_basestring,
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(computed_signature, slack_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

# =====================
# Background grammar processing
# =====================

async def process_grammar_async(text: str, response_url: str, instructions: str):
    try:
        corrected_text = await rewrite_text(text, instructions)
    except Exception as e:
        corrected_text = f"❌ Error while processing grammar: {e}"

    payload = {
        "response_type": "ephemeral",
        "text": corrected_text,
    }

    requests.post(response_url, json=payload)

# =====================
# Slash command: /grammar
# =====================

@app.post("/slack/commands")
async def slack_commands(request: Request):
    raw_body = await request.body()

    timestamp = request.headers.get("X-Slack-Request-Timestamp")
    slack_signature = request.headers.get("X-Slack-Signature")

    if not timestamp or not slack_signature:
        raise HTTPException(status_code=400, detail="Missing Slack headers")

    verify_slack_request(
        raw_body=raw_body,
        timestamp=timestamp,
        slack_signature=slack_signature,
    )

    form = await request.form()
    response_url = form.get("response_url")

    raw_input = form.get("text", "").strip()

    if ":" in raw_input:
        instructions, user_text = raw_input.split(":", 1)
        instructions = instructions.strip()
        user_text = user_text.strip()
    else:
        instructions = ""
        user_text = raw_input

    asyncio.create_task(
        process_grammar_async(user_text, response_url, instructions)
    )

    return {
        "response_type": "ephemeral",
        "text": "✍️ Fixing grammar…"
    }

# =====================
# Message shortcut → Modal (optional UX)
# =====================

@app.post("/slack/interactions")
async def slack_interactions(request: Request):
    raw_body = await request.body()

    verify_slack_request(
        raw_body=raw_body,
        timestamp=request.headers.get("X-Slack-Request-Timestamp"),
        slack_signature=request.headers.get("X-Slack-Signature"),
    )

    payload = json.loads((await request.form()).get("payload"))

    if payload.get("type") == "message_action" and payload.get("callback_id") == "rephrase_message":
        trigger_id = payload["trigger_id"]
        original_text = payload["message"]["text"]

        rewritten_text = await rewrite_text(original_text)

        modal = {
            "type": "modal",
            "title": {"type": "plain_text", "text": "Rephrase with AI"},
            "close": {"type": "plain_text", "text": "Close"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*AI-corrected version:*"
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": rewritten_text
                    }
                }
            ]
        }

        requests.post(
            "https://slack.com/api/views.open",
            headers={
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "trigger_id": trigger_id,
                "view": modal,
            },
        )

    return {}
