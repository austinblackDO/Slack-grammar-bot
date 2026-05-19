from __future__ import annotations

import logging

from slack_bolt import App

from app.config import Settings
from app.inference import InferenceHubClient, InferenceHubError

logger = logging.getLogger("slack-grammar-bot")

_MAX_PREVIEW_LEN = 1500


class GrammarBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.inference = InferenceHubClient(settings)

    def create_app(self) -> App:
        app = App(token=self.settings.slack_bot_token)
        command = self.settings.slack_command_name

        def acknowledge(ack, body) -> None:
            if not (body.get("text") or "").strip():
                ack(
                    {
                        "response_type": "ephemeral",
                        "text": (
                            f"Usage: `{command} your question` "
                            f"(or `{command} agent-id::your question` to target a specific agent)."
                        ),
                    }
                )
                return
            ack({"response_type": "ephemeral", "text": "Thinking..."})

        def run_command(body, respond) -> None:
            raw_text = (body.get("text") or "").strip()
            if not raw_text:
                return

            try:
                agent, prompt = self.inference.parse_command_text(
                    raw_text, self.settings.inference_hub_default_agent
                )
                if not prompt:
                    text = (
                        f"Prompt is empty. Usage: `{command} your question` or "
                        f"`{command} agent-id::your question`."
                    )
                    respond(response_type="ephemeral", text=text)
                    return

                reply = self.inference.complete(agent, prompt)
            except InferenceHubError as exc:
                logger.exception("inference hub error")
                reply = f"Error while contacting Inference Hub: {exc}"
            except Exception:
                logger.exception("unexpected error handling slash command")
                reply = "Something went wrong while processing your request."

            respond(
                response_type=self.settings.slack_reply_visibility,
                text=self._format_reply(raw_text, reply),
                replace_original=False,
            )

        app.command(command, lazy=[run_command])(acknowledge)
        return app

    @staticmethod
    def _format_reply(user_text: str, reply: str) -> str:
        preview = user_text.strip()
        if len(preview) > _MAX_PREVIEW_LEN:
            preview = preview[: _MAX_PREVIEW_LEN - 3] + "..."
        return f"*Your message*\n```{preview}```\n\n*Reply*\n{reply}"
