import logging
import re
import sys


_SECRET_PATTERNS = [
    (re.compile(r"bot\d+:[A-Za-z0-9_-]+"), "bot<redacted>"),
    (re.compile(r"(TELEGRAM_BOT_TOKEN=)[^,\s]+"), r"\1<redacted>"),
    (re.compile(r"(MCP_BEARER_TOKEN=)[^,\s]+"), r"\1<redacted>"),
    (re.compile(r"(FREEDOM24_(?:PRIV|PUB)_KEY=)[^,\s]+"), r"\1<redacted>"),
]


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(RedactingFormatter("%(asctime)s [freedom24] %(levelname)s: %(message)s"))
    logging.basicConfig(level=level, handlers=[handler])
