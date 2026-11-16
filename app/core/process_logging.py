"""Independent application logging for API and Scheduler processes."""

import logging
import os


def configure_process_logging(component: str, variable: str, default: str = "INFO") -> None:
    level = os.environ.get(variable, default).upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError(f"{variable} must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
    logging.basicConfig(
        level=level,
        format=f"%(asctime)s [Everunfold {component}] %(levelname)s %(message)s",
    )
