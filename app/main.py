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


# Secrets

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
GRADIENT_MODEL_ACCESS_KEY = os.getenv("GRADIENT_MODEL_ACCESS_KEY")

if not SLACK_SIGNING_SECRET:
    raise RuntimeError("SLACK_SIGNING_SECRET not set")

if not GRADIENT_MODEL_ACCESS_KEY:
    raise RuntimeError("GRADIENT_MODEL_ACCESS_KEY not set")


# Gradient async client

gradient_client = AsyncGradient(
    model_access_key=GRADIENT_MODEL_ACCESS_KEY
)

# Slack request verification

def verify_slack_request(*, raw_body: bytes, timestamp: str, slack_signature: str):
    now = int(time.time())
    req_ts = int(timestamp)

    # Prevent replay attacks (5 min window)
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

# Background grammar processing

async def process_grammar_async(text: str, response_url: str):
    try:
        response = await gradient_client.chat.completions.create(
            model="openai-gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional grammar assistant. "
                        "Fix grammar, spelling, and punctuation. "
                        "Preserve the original tone and intent. "
                        "Return only the corrected text."
                    ),
                },
                {
                    "role": "user",
                    "content": text,
                },
            ],
        )

        corrected_text = response.choices[0].message.content.strip()

    except Exception as e:
        corrected_text = f"❌ Error while processing grammar: {e}"

    payload = {
        "response_type": "ephemeral",
        "text": corrected_text,
    }

    requests.post(response_url, json=payload)

# Slack slash command endpoint

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
    text = form.get("text", "")
    response_url = form.get("response_url")

    # Fire background task (AI work happens async)
    asyncio.create_task(
        process_grammar_async(text, response_url)
    )

    # Immediate ACK (< 3 seconds)
    return {
        "response_type": "ephemeral",
        "text": "✍️"
    }

import json

@app.post("/slack/interactions")
async def slack_interactions(request: Request):
    raw_body = await request.body()

    verify_slack_request(
        raw_body=raw_body,
        timestamp=request.headers.get("X-Slack-Request-Timestamp"),
        slack_signature=request.headers.get("X-Slack-Signature"),
    )

    payload = json.loads((await request.form()).get("payload"))

    # Message shortcut
    if payload.get("type") == "message_action" and payload.get("callback_id") == "rephrase_message":
        trigger_id = payload["trigger_id"]
        original_text = payload["message"]["text"]

        # AI call (you already have this function)
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
                "Authorization": f"Bearer {os.getenv('SLACK_BOT_TOKEN')}",
                "Content-Type": "application/json",
            },
            json={
                "trigger_id": trigger_id,
                "view": modal,
            },
        )

        return {}
