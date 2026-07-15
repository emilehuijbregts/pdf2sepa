"""Entry point voor de PDF2SEPA desktop applicatie."""

import logging
import sys
from logging.handlers import RotatingFileHandler

from logic.runtime_paths import (
    app_root,
    configure_tesseract_runtime,
    data_dir,
    deps_dir,
    install_root,
    log_dir,
    tesseract_path,
)
from version import __version__

# Make sure local vendored deps are available (for dev runs).
_deps = deps_dir()
if _deps.exists() and str(_deps) not in sys.path:
    sys.path.insert(0, str(_deps))


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger("pdf2sepa")
    try:
        log_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    log_file = log_dir() / "pdf2sepa.log"

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
    return logger


def main() -> None:
    configure_tesseract_runtime()
    logger = _configure_logging()
    logger.info("PDF2SEPA %s", __version__)
    logger.info("App root: %s", app_root())
    logger.info("Data path (canonical): %s", data_dir())
    logger.info("Log path: %s", log_dir())
    logger.info("Tesseract path: %s", tesseract_path())

    if sys.platform.startswith("win"):
        from logic.auto_update import apply_pending_updater_refresh

        apply_pending_updater_refresh(install_root())

    from main_window import main as run_desktop

    run_desktop()


if __name__ == "__main__":
    main()
