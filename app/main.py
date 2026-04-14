import hashlib
import hmac
import os
import time

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

load_dotenv()

app = FastAPI(title="Slackbot + DigitalOcean Inference Hub")

# =====================
# Environment variables
# =====================

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")
INFERENCE_HUB_MODEL_ACCESS_KEY = os.getenv(
    "INFERENCE_HUB_MODEL_ACCESS_KEY", os.getenv("MODEL_ACCESS_KEY")
)
INFERENCE_HUB_BASE_URL = os.getenv("INFERENCE_HUB_BASE_URL", "https://inference.do-ai.run/v1")
INFERENCE_HUB_DEFAULT_AGENT = os.getenv("INFERENCE_HUB_DEFAULT_AGENT")
INFERENCE_HUB_SYSTEM_PROMPT = os.getenv(
    "INFERENCE_HUB_SYSTEM_PROMPT",
    "You are a helpful assistant for a Slack workspace. Keep responses concise and actionable.",
)
SLACK_COMMAND_NAME = os.getenv("SLACK_COMMAND_NAME", "/grammar")

if not SLACK_SIGNING_SECRET:
    raise RuntimeError("SLACK_SIGNING_SECRET not set")

if not INFERENCE_HUB_MODEL_ACCESS_KEY:
    raise RuntimeError("INFERENCE_HUB_MODEL_ACCESS_KEY (or MODEL_ACCESS_KEY) not set")

if not INFERENCE_HUB_DEFAULT_AGENT:
    raise RuntimeError("INFERENCE_HUB_DEFAULT_AGENT not set")


def verify_slack_request(*, raw_body: bytes, timestamp: str, slack_signature: str) -> None:
    try:
        req_ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid request timestamp") from exc

    now = int(time.time())

    # Prevent replay attacks (5-minute window)
    if abs(now - req_ts) > 60 * 5:
        raise HTTPException(status_code=401, detail="Stale request")

    sig_basestring = b"v0:" + timestamp.encode() + b":" + raw_body
    computed_signature = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        sig_basestring,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(computed_signature, slack_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


def parse_agent_and_prompt(raw_text: str) -> tuple[str, str]:
    """
    Allows optional override syntax:
    /grammar <agent-id>::<prompt text>
    """
    text = raw_text.strip()
    if "::" not in text:
        return INFERENCE_HUB_DEFAULT_AGENT, text

    maybe_agent, maybe_prompt = text.split("::", 1)
    agent = maybe_agent.strip() or INFERENCE_HUB_DEFAULT_AGENT
    prompt = maybe_prompt.strip()
    return agent, prompt


def normalize_model_output(content: object) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
        return "".join(chunks).strip()

    return str(content).strip()


async def query_inference_hub(*, agent: str, prompt: str) -> str:
    url = f"{INFERENCE_HUB_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": agent,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": INFERENCE_HUB_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {
        "Authorization": f"Bearer {INFERENCE_HUB_MODEL_ACCESS_KEY}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(url, json=payload, headers=headers)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:600]
            raise RuntimeError(f"Inference Hub request failed: {detail}") from exc

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("Inference Hub returned no choices")

    message = choices[0].get("message", {})
    content = message.get("content", "")
    return normalize_model_output(content)


async def send_slack_response(response_url: str, text: str) -> None:
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            response_url,
            json={
                "response_type": "ephemeral",
                "text": text,
            },
        )


async def process_agent_request_async(raw_text: str, response_url: str) -> None:
    try:
        agent, prompt = parse_agent_and_prompt(raw_text)
        if not prompt:
            output = (
                f"⚠️ Prompt is empty. Usage: "
                f"`{SLACK_COMMAND_NAME} your question` or "
                f"`{SLACK_COMMAND_NAME} agent-id::your question`."
            )
        else:
            output = await query_inference_hub(agent=agent, prompt=prompt)
    except Exception as exc:  # noqa: BLE001 - user-facing error path
        output = f"❌ Error while contacting Inference Hub: {exc}"

    await send_slack_response(response_url, output)


@app.get("/healthz")
async def healthcheck() -> dict[str, bool]:
    return {"ok": True}


@app.post("/slack/commands")
async def slack_commands(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
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
    user_text = str(form.get("text", "")).strip()
    response_url = str(form.get("response_url", "")).strip()

    if not response_url:
        raise HTTPException(status_code=400, detail="Missing response_url in Slack payload")

    if not user_text:
        return {
            "response_type": "ephemeral",
            "text": (
                f"⚠️ Usage: `{SLACK_COMMAND_NAME} your question` "
                f"(or `{SLACK_COMMAND_NAME} agent-id::your question` "
                "to target a specific agent)."
            ),
        }

    background_tasks.add_task(process_agent_request_async, user_text, response_url)

    # Immediate acknowledgement (< 3 seconds) while the real response is processed.
    return {
        "response_type": "ephemeral",
        "text": "🤖 Thinking...",
    }
