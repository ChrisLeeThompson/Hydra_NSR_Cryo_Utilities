"""Application-wide logging configuration."""
import logging


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger for the application."""
    
    logging.basicConfig(
        format="%(asctime)s:\t%(levelname)s:\t%(name)s\t%(funcName)s:\t%(message)s",
        level=level,
        force=True,
    )