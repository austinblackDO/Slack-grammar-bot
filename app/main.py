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
# Delayed response only: "ephemeral" (default) or "in_channel" (visible in channel history for all).
_slack_vis = os.getenv("SLACK_REPLY_VISIBILITY", "ephemeral").strip().lower()
SLACK_REPLY_VISIBILITY = _slack_vis if _slack_vis in ("ephemeral", "in_channel") else "ephemeral"

def _runtime_config_errors() -> list[str]:
    """Collect missing config so the process can start (e.g. DOCC readiness) before Vault sync."""
    errors: list[str] = []
    if not SLACK_SIGNING_SECRET:
        errors.append("SLACK_SIGNING_SECRET not set")
    if not INFERENCE_HUB_MODEL_ACCESS_KEY:
        errors.append("INFERENCE_HUB_MODEL_ACCESS_KEY (or MODEL_ACCESS_KEY) not set")
    if not INFERENCE_HUB_DEFAULT_AGENT:
        errors.append("INFERENCE_HUB_DEFAULT_AGENT not set")
    return errors


def require_slack_runtime_config() -> None:
    """Fail Slack routes until required env is present (secrets may arrive after first deploy)."""
    missing = _runtime_config_errors()
    if missing:
        raise HTTPException(
            status_code=503,
            detail="Service misconfigured: " + "; ".join(missing),
        )


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


def _is_genai_agent_deployment(base_url: str) -> bool:
    """GenAI Agent deployments use /api/v1/chat/completions and omit top-level model."""
    return "agents.do-ai.run" in base_url


async def query_inference_hub(*, agent: str, prompt: str) -> str:
    url = f"{INFERENCE_HUB_BASE_URL.rstrip('/')}/chat/completions"
    payload: dict[str, object] = {
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": INFERENCE_HUB_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    # Serverless Inference Hub uses OpenAI-style "model"; hosted agents are fixed per URL.
    if not _is_genai_agent_deployment(INFERENCE_HUB_BASE_URL):
        payload["model"] = agent
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
    if (not isinstance(content, str) or not content.strip()) and message.get("reasoning_content"):
        content = message.get("reasoning_content", "")
    return normalize_model_output(content)


def _format_user_plus_reply(*, user_raw: str, reply: str) -> str:
    """Keep the user's slash text visible alongside the model reply (Slack mrkdwn)."""
    preview = user_raw.strip()
    if len(preview) > 1500:
        preview = preview[:1497] + "..."
    return f"*Your message*\n```{preview}```\n\n*Reply*\n{reply}"


async def send_slack_response(
    response_url: str,
    text: str,
    *,
    response_type: str | None = None,
) -> None:
    rtype = response_type or SLACK_REPLY_VISIBILITY
    async with httpx.AsyncClient(timeout=20) as client:
        await client.post(
            response_url,
            json={
                "response_type": rtype,
                "text": text,
                # Do not replace the slash-command line / first ephemeral with only the reply.
                "replace_original": False,
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
            await send_slack_response(response_url, output, response_type="ephemeral")
            return

        model_text = await query_inference_hub(agent=agent, prompt=prompt)
        output = _format_user_plus_reply(user_raw=raw_text, reply=model_text)
    except Exception as exc:  # noqa: BLE001 - user-facing error path
        output = _format_user_plus_reply(
            user_raw=raw_text,
            reply=f"❌ Error while contacting Inference Hub: {exc}",
        )

    await send_slack_response(response_url, output)


@app.get("/healthz")
async def healthcheck() -> dict[str, bool]:
    return {"ok": True}


@app.post("/slack/commands")
async def slack_commands(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    require_slack_runtime_config()
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
