from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigurationError(Exception):
    """Raised when required environment variables are missing or invalid."""


@dataclass
class Settings:
    slack_app_token: str
    slack_bot_token: str
    inference_hub_model_access_key: str
    inference_hub_base_url: str
    inference_hub_default_agent: str
    inference_hub_system_prompt: str
    slack_command_name: str
    slack_reply_visibility: str
    port: int

    @classmethod
    def from_env(cls) -> Settings:
        reply_visibility = os.getenv("SLACK_REPLY_VISIBILITY", "ephemeral").strip().lower()
        if reply_visibility not in ("ephemeral", "in_channel"):
            reply_visibility = "ephemeral"

        model_key = os.getenv("INFERENCE_HUB_MODEL_ACCESS_KEY") or os.getenv("MODEL_ACCESS_KEY")

        return cls(
            slack_app_token=os.getenv("SLACK_APP_TOKEN", ""),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            inference_hub_model_access_key=model_key or "",
            inference_hub_base_url=os.getenv("INFERENCE_HUB_BASE_URL", "https://inference.do-ai.run/v1"),
            inference_hub_default_agent=os.getenv("INFERENCE_HUB_DEFAULT_AGENT", ""),
            inference_hub_system_prompt=os.getenv(
                "INFERENCE_HUB_SYSTEM_PROMPT",
                "You are a helpful assistant for a Slack workspace. Keep responses concise and actionable.",
            ),
            slack_command_name=os.getenv("SLACK_COMMAND_NAME", "/grammar"),
            slack_reply_visibility=reply_visibility,
            port=int(os.getenv("PORT", "8080")),
        )

    def validate(self) -> None:
        missing: list[str] = []
        if not self.slack_app_token:
            missing.append("SLACK_APP_TOKEN")
        if not self.slack_bot_token:
            missing.append("SLACK_BOT_TOKEN")
        if not self.inference_hub_model_access_key:
            missing.append("INFERENCE_HUB_MODEL_ACCESS_KEY (or MODEL_ACCESS_KEY)")
        if not self.inference_hub_default_agent:
            missing.append("INFERENCE_HUB_DEFAULT_AGENT")
        if missing:
            raise ConfigurationError(f"Service misconfigured: {'; '.join(missing)} not set")

    @classmethod
    def load(cls) -> Settings:
        settings = cls.from_env()
        settings.validate()
        return settings
