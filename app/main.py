import os
import time
import hmac
import hashlib
import asyncio
import requests

from fastapi import FastAPI, Request, HTTPException
from dotenv import load_dotenv
from gradient import AsyncGradient

load_dotenv()

app = FastAPI()

# =====================
# Environment variables
# =====================

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
GRADIENT_MODEL_ACCESS_KEY = os.getenv("GRADIENT_MODEL_ACCESS_KEY")

if not SLACK_SIGNING_SECRET:
    raise RuntimeError("SLACK_SIGNING_SECRET not set")

if not GRADIENT_MODEL_ACCESS_KEY:
    raise RuntimeError("GRADIENT_MODEL_ACCESS_KEY not set")

# =====================
# Gradient client
# =====================

gradient_client = AsyncGradient(
    model_access_key=GRADIENT_MODEL_ACCESS_KEY
)

# =====================
# Grammar correction helper
# =====================

async def correct_grammar(text: str) -> str:
    system_prompt = (
        "You are a strict grammar correction assistant.\n\n"
        "Your task:\n"
        "- Fix spelling, grammar, punctuation, and sentence boundaries.\n"
        "- Understand the user's intent only to preserve meaning and tone.\n\n"
        "Hard rules:\n"
        "- Do NOT rewrite sentences for style.\n"
        "- Do NOT add, remove, or infer information.\n"
        "- Do NOT change tone or intent.\n"
        "- Do NOT answer questions or explain anything.\n\n"
        "If the text is already grammatically correct, return it unchanged.\n"
        "Return ONLY the corrected text."
    )

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
# Slack request verification
# =====================

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

# =====================
# Background processing
# =====================

async def process_grammar_async(text: str, response_url: str):
    try:
        corrected_text = await correct_grammar(text)
    except Exception as e:
        corrected_text = f"❌ Error while processing grammar: {e}"

    requests.post(
        response_url,
        json={
            "response_type": "ephemeral",
            "text": corrected_text,
        },
    )

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
    user_text = form.get("text", "").strip()
    response_url = form.get("response_url")

    if not user_text:
        return {
            "response_type": "ephemeral",
            "text": "⚠️ Please provide text to check grammar.",
        }

    # Run grammar correction asynchronously
    asyncio.create_task(
        process_grammar_async(user_text, response_url)
    )

    # Immediate ACK (< 3 seconds)
    return {
        "response_type": "ephemeral",
        "text": "... ✍️"
    }
