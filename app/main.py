from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

import httpx
from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("slack-grammar-bot")


@dataclass(frozen=True)
class Config:
    slack_app_token: str | None
    slack_bot_token: str | None
    inference_hub_model_access_key: str | None
    inference_hub_base_url: str
    inference_hub_default_agent: str | None
    inference_hub_system_prompt: str
    slack_command_name: str
    slack_reply_visibility: str
    port: int

    @classmethod
    def from_env(cls) -> "Config":
        reply_visibility = os.getenv("SLACK_REPLY_VISIBILITY", "ephemeral").strip().lower()
        if reply_visibility not in ("ephemeral", "in_channel"):
            reply_visibility = "ephemeral"

        model_key = os.getenv("INFERENCE_HUB_MODEL_ACCESS_KEY") or os.getenv("MODEL_ACCESS_KEY")

        return cls(
            slack_app_token=os.getenv("SLACK_APP_TOKEN"),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN"),
            inference_hub_model_access_key=model_key,
            inference_hub_base_url=os.getenv("INFERENCE_HUB_BASE_URL", "https://inference.do-ai.run/v1"),
            inference_hub_default_agent=os.getenv("INFERENCE_HUB_DEFAULT_AGENT"),
            inference_hub_system_prompt=os.getenv(
                "INFERENCE_HUB_SYSTEM_PROMPT",
                "You are a helpful assistant for a Slack workspace. Keep responses concise and actionable.",
            ),
            slack_command_name=os.getenv("SLACK_COMMAND_NAME", "/grammar"),
            slack_reply_visibility=reply_visibility,
            port=int(os.getenv("PORT", "8080")),
        )

    def runtime_config_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.slack_app_token:
            errors.append("SLACK_APP_TOKEN not set")
        if not self.slack_bot_token:
            errors.append("SLACK_BOT_TOKEN not set")
        if not self.inference_hub_model_access_key:
            errors.append("INFERENCE_HUB_MODEL_ACCESS_KEY (or MODEL_ACCESS_KEY) not set")
        if not self.inference_hub_default_agent:
            errors.append("INFERENCE_HUB_DEFAULT_AGENT not set")
        return errors


def parse_agent_and_prompt(raw_text: str, default_agent: str) -> tuple[str, str]:
    text = raw_text.strip()
    if "::" not in text:
        return default_agent, text

    maybe_agent, maybe_prompt = text.split("::", 1)
    agent = maybe_agent.strip() or default_agent
    prompt = maybe_prompt.strip()
    return agent, prompt


def normalize_model_output(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text", "")))
        return "".join(chunks).strip()

    return str(content).strip()


def is_genai_agent_deployment(base_url: str) -> bool:
    return "agents.do-ai.run" in base_url


def query_inference_hub(*, cfg: Config, agent: str, prompt: str) -> str:
    url = f"{cfg.inference_hub_base_url.rstrip('/')}/chat/completions"
    is_agent_deployment = is_genai_agent_deployment(cfg.inference_hub_base_url)
    messages = [{"role": "user", "content": prompt}]
    if not is_agent_deployment:
        messages.insert(0, {"role": "system", "content": cfg.inference_hub_system_prompt})

    payload: dict[str, Any] = {
        "temperature": 0.2,
        "messages": messages,
    }
    if not is_agent_deployment:
        payload["model"] = agent

    headers = {
        "Authorization": f"Bearer {cfg.inference_hub_model_access_key}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=60) as client:
        response = client.post(url, json=payload, headers=headers)
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


def format_user_plus_reply(*, user_raw: str, reply: str) -> str:
    preview = user_raw.strip()
    if len(preview) > 1500:
        preview = preview[:1497] + "..."
    return f"*Your message*\n```{preview}```\n\n*Reply*\n{reply}"


def respond_to_slack(
    respond: Callable[[dict[str, Any]], Any],
    *,
    text: str,
    response_type: str,
) -> None:
    respond(
        {
            "response_type": response_type,
            "text": text,
            "replace_original": False,
        }
    )


def process_agent_request(cfg: Config, raw_text: str, respond: Callable[[dict[str, Any]], Any]) -> None:
    try:
        assert cfg.inference_hub_default_agent is not None
        agent, prompt = parse_agent_and_prompt(raw_text, cfg.inference_hub_default_agent)
        if not prompt:
            respond_to_slack(
                respond,
                text=(
                    f"Prompt is empty. Usage: `{cfg.slack_command_name} your question` or "
                    f"`{cfg.slack_command_name} agent-id::your question`."
                ),
                response_type="ephemeral",
            )
            return

        model_text = query_inference_hub(cfg=cfg, agent=agent, prompt=prompt)
        output = format_user_plus_reply(user_raw=raw_text, reply=model_text)
    except Exception as exc:  # noqa: BLE001 - Slack user-facing failure path
        logger.exception("failed to process Slack command")
        output = format_user_plus_reply(
            user_raw=raw_text,
            reply=f"Error while contacting Inference Hub: {exc}",
        )

    respond_to_slack(respond, text=output, response_type=cfg.slack_reply_visibility)


def build_slack_app(cfg: Config) -> App:
    assert cfg.slack_bot_token is not None
    app = App(token=cfg.slack_bot_token)

    @app.command(cfg.slack_command_name)
    def handle_grammar_command(ack: Callable[..., Any], body: dict[str, Any], respond: Callable[..., Any]) -> None:
        user_text = str(body.get("text", "")).strip()
        if not user_text:
            ack(
                {
                    "response_type": "ephemeral",
                    "text": (
                        f"Usage: `{cfg.slack_command_name} your question` "
                        f"(or `{cfg.slack_command_name} agent-id::your question` to target a specific agent)."
                    ),
                }
            )
            return

        ack({"response_type": "ephemeral", "text": "Thinking..."})
        threading.Thread(target=process_agent_request, args=(cfg, user_text, respond), daemon=True).start()

    return app


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        if self.path != "/healthz":
            self.send_response(404)
            self.end_headers()
            return

        body = b'{"ok":true}\n'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("health server: " + fmt, *args)


def start_health_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="health-server", daemon=True)
    thread.start()
    logger.info("health server listening on port %s", port)
    return server


def main() -> None:
    cfg = Config.from_env()
    start_health_server(cfg.port)

    missing = cfg.runtime_config_errors()
    if missing:
        raise SystemExit("Service misconfigured: " + "; ".join(missing))

    assert cfg.slack_app_token is not None
    slack_app = build_slack_app(cfg)
    logger.info("starting Slack Socket Mode handler for %s", cfg.slack_command_name)
    SocketModeHandler(slack_app, cfg.slack_app_token).start()


if __name__ == "__main__":
    main()
