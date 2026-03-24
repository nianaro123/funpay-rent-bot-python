# steam_session_guard.py

import logging
import subprocess
from pathlib import Path


LOGGER = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STEAM_PYTHON = BASE_DIR / "steam_env" / "Scripts" / "python.exe"
STEAM_WORKER = BASE_DIR / "steam_relogin_worker.py"


def force_relogin_account(login: str, password: str, shared_secret: str) -> bool:
    if not login or not password or not shared_secret:
        LOGGER.warning("force_relogin_account skipped: missing credentials")
        return False

    if not STEAM_PYTHON.exists():
        LOGGER.error("steam_env python not found: %s", STEAM_PYTHON)
        return False

    if not STEAM_WORKER.exists():
        LOGGER.error("steam_relogin_worker.py not found: %s", STEAM_WORKER)
        return False

    try:
        result = subprocess.run(
            [
                str(STEAM_PYTHON),
                str(STEAM_WORKER),
                login,
                password,
                shared_secret,
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )

        if result.returncode == 0:
            LOGGER.info("Steam relogin success for account %s", login)
            return True

        LOGGER.error(
            "Steam relogin failed for %s. code=%s stdout=%s stderr=%s",
            login,
            result.returncode,
            result.stdout,
            result.stderr,
        )
        return False

    except subprocess.TimeoutExpired:
        LOGGER.exception("Steam relogin timeout for account %s", login)
        return False
    except Exception as e:
        LOGGER.exception("Steam relogin process failed for %s: %s", login, e)
        return False