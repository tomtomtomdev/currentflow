"""One place that turns the DAL's `net-error` lines (dal/netlog.py) into a durable log.

Nothing in the DAL configures logging — `log_net_error` just calls `logger.log(...)`,
so lines vanish on the running process's stderr. `configure_logging()` attaches a rotating
`FileHandler` (plus a stderr `StreamHandler`) once, at the app entrypoints, so a `net-error`
survives to be read back later.

Local-only, single-operator posture (CLAUDE.md / spec §10): the log lives under
`LOG_DIR` (git-ignored) on the operator's machine and is never republished. The
`net-error` formatter is already redacted at the seam — paths + coarse outcomes only,
never a body, token, OTP, or exception message — so persisting these lines adds no new
secret exposure.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from currentflow import config

_configured = False


def configure_logging(*, level: int = logging.WARNING) -> None:
    """Idempotently attach a rotating file handler + stderr handler to the root logger.

    Safe to call from every entrypoint; only the first call installs handlers. `level`
    defaults to WARNING so both `net-error` levels (WARNING transient / ERROR terminal)
    are captured without pulling in third-party debug chatter.
    """
    global _configured
    if _configured:
        return

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    _configured = True
