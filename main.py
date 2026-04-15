"""Entry point voor de PDF2SEPA desktop applicatie."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_BASE = Path(__file__).resolve().parent
LOG_DIR = APP_BASE / "logs"

# Make sure local vendored deps are available (for dev runs).
DEPS = APP_BASE / ".deps"
if DEPS.exists() and str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))


def _configure_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / "pdf2sepa.log"

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.WARNING)

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def main() -> None:
    _configure_logging()
    from main_window import main as run_desktop

    run_desktop()


if __name__ == "__main__":
    main()
