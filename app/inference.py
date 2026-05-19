from __future__ import annotations

from typing import Any

import httpx

from app.config import Settings


class InferenceHubError(Exception):
    """Inference Hub returned an error or an unexpected response."""


class InferenceHubClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._agent_deployment = "agents.do-ai.run" in settings.inference_hub_base_url

    @staticmethod
    def parse_command_text(raw_text: str, default_agent: str) -> tuple[str, str]:
        text = raw_text.strip()
        if "::" not in text:
            return default_agent, text

        maybe_agent, maybe_prompt = text.split("::", 1)
        return (maybe_agent.strip() or default_agent, maybe_prompt.strip())

    def complete(self, agent: str, prompt: str) -> str:
        url = f"{self._settings.inference_hub_base_url.rstrip('/')}/chat/completions"
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        if not self._agent_deployment:
            messages.insert(0, {"role": "system", "content": self._settings.inference_hub_system_prompt})

        payload: dict[str, Any] = {"temperature": 0.2, "messages": messages}
        if not self._agent_deployment:
            payload["model"] = agent

        headers = {
            "Authorization": f"Bearer {self._settings.inference_hub_model_access_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:600]
            raise InferenceHubError(f"request failed: {detail}") from exc
        except httpx.HTTPError as exc:
            raise InferenceHubError(str(exc)) from exc

        choices = data.get("choices") or []
        if not choices:
            raise InferenceHubError("response contained no choices")

        message = choices[0].get("message", {})
        content = message.get("content", "")
        if (not isinstance(content, str) or not content.strip()) and message.get("reasoning_content"):
            content = message.get("reasoning_content", "")

        return self._normalize_content(content)

    @staticmethod
    def _normalize_content(content: Any) -> str:
        match content:
            case str() as text:
                return text.strip()
            case list() as parts:
                chunks = [
                    str(part.get("text", ""))
                    for part in parts
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                return "".join(chunks).strip()
            case _:
                return str(content).strip()
