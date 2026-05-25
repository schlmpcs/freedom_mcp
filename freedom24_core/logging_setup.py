import logging
import sys


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        stream=sys.stderr,
        level=level,
        format="%(asctime)s [freedom24] %(levelname)s: %(message)s",
    )
