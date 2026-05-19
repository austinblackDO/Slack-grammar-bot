from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode import SocketModeHandler

from app.bot import GrammarBot
from app.config import ConfigurationError, Settings
from app.health import start_health_server

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("slack-grammar-bot")


def main() -> None:
    try:
        settings = Settings.load()
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from exc

    start_health_server(settings.port)

    slack_app = GrammarBot(settings).create_app()
    logger.info("starting Slack Socket Mode handler for %s", settings.slack_command_name)
    SocketModeHandler(slack_app, settings.slack_app_token).start()


if __name__ == "__main__":
    main()
