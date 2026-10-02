"""Development logs exclude biometric measurements."""
import logging
from logging.handlers import RotatingFileHandler

from stroke_key.utils.paths import data_directory


def configure_logging() -> None:
    handler = RotatingFileHandler(data_directory() / "strokekey.log",
                                  maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
