#steam_session_worker.py
import json
import logging
import os
import shutil
import subprocess
import threading

from settings import (
    STEAM_SIGN_OUT_ENABLED,
    STEAM_SIGN_OUT_NODE_BIN,
    STEAM_SIGN_OUT_WORKER_PATH,
    STEAM_SIGN_OUT_TIMEOUT_SEC,
)
from tg_notify import send_admin_notification

LOGGER = logging.getLogger(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_running_orders: set[str] = set()
_running_lock = threading.Lock()


def _resolve_worker_path() -> str:
    if os.path.isabs(STEAM_SIGN_OUT_WORKER_PATH):
        return STEAM_SIGN_OUT_WORKER_PATH
    return os.path.join(BASE_DIR, STEAM_SIGN_OUT_WORKER_PATH)


def _resolve_node_bin() -> str:
    raw = (STEAM_SIGN_OUT_NODE_BIN or "node").strip()
    if raw == "node":
        return shutil.which("node") or "node"
    return raw


def _extract_result_json(stdout: str, stderr: str) -> dict | None:
    combined = "\n".join([stdout or "", stderr or ""])
    for line in reversed(combined.splitlines()):
        if not line.startswith("RESULT_JSON="):
            continue
        payload = line.split("=", 1)[1].strip()
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {
                "ok": False,
                "error": "Не удалось распарсить RESULT_JSON от node-воркера",
            }
    return None


def run_steam_sign_out(login: str, password: str, shared_secret: str = "") -> dict:
    if not STEAM_SIGN_OUT_ENABLED:
        return {
            "ok": False,
            "skipped": True,
            "error": "Steam sign-out выключен в config.py",
        }

    worker_path = _resolve_worker_path()
    if not os.path.exists(worker_path):
        return {
            "ok": False,
            "error": f"Не найден JS-воркер: {worker_path}",
        }

    node_bin = _resolve_node_bin()
    cmd = [node_bin, worker_path, login, password, shared_secret or ""]

    try:
        completed = subprocess.run(
            cmd,
            cwd=os.path.dirname(worker_path),
            capture_output=True,
            text=True,
            timeout=STEAM_SIGN_OUT_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "error": f"Не найден Node.js: {node_bin}",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"JS-воркер не завершился за {STEAM_SIGN_OUT_TIMEOUT_SEC} сек.",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"Ошибка запуска JS-воркера: {e}",
        }

    result = _extract_result_json(completed.stdout, completed.stderr) or {}
    if not result:
        result = {
            "ok": completed.returncode == 0,
            "error": "Node-воркер завершился без RESULT_JSON",
        }

    result.setdefault("ok", completed.returncode == 0)
    result["returncode"] = completed.returncode
    return result


def _notify_admin(rental, reason: str, result: dict) -> None:
    order_id = rental["order_id"]
    login = rental["login"]
    web_signout = result.get("web_signout") or {}
    kick_info = result.get("kick_playing_session") or {}

    lines = [
        "🔒 Steam sign-out worker завершил работу",
        f"Причина: {reason}",
        f"Заказ: #{order_id}",
        f"Логин аккаунта: {login}",
        f"Итог: {'успешно' if result.get('ok') else 'ошибка'}",
    ]

    if kick_info:
        lines.append(
            "kickPlayingSession: "
            f"attempted={kick_info.get('attempted')} "
            f"kicked={kick_info.get('kicked')} "
            f"playingApp={kick_info.get('playingApp')}"
        )

    if web_signout:
        lines.append(
            "webSignout: "
            f"ok={web_signout.get('ok')} "
            f"deauth={web_signout.get('deauthorize_status')} "
            f"logout={web_signout.get('logout_status')} "
            f"attempts={web_signout.get('attempts_used')}"
        )

    if result.get("error"):
        lines.append(f"Ошибка: {result['error']}")

    send_admin_notification("\n".join(lines))


def _run_async(rental, reason: str) -> None:
    order_id = str(rental["order_id"])
    try:
        login = (rental["login"] or "").strip()
        password = (rental["password"] or "").strip()
        shared_secret = (rental["shared_secret"] or "").strip()

        if not login or not password:
            LOGGER.warning("Steam sign-out пропущен для order_id=%s: пустой login/password", order_id)
            return

        LOGGER.info(
            "Запускаю Steam sign-out worker для order_id=%s, login=%s, reason=%s",
            order_id,
            login,
            reason,
        )
        result = run_steam_sign_out(login, password, shared_secret)
        LOGGER.info("Steam sign-out result for order_id=%s: %s", order_id, result)
        _notify_admin(rental, reason, result)
    except Exception:
        LOGGER.exception("Ошибка в async Steam sign-out для order_id=%s", order_id)
    finally:
        with _running_lock:
            _running_orders.discard(order_id)


def trigger_steam_sign_out_async(rental, reason: str) -> bool:
    if not STEAM_SIGN_OUT_ENABLED:
        LOGGER.info("Steam sign-out выключен, order_id=%s", rental["order_id"])
        return False

    order_id = str(rental["order_id"])
    with _running_lock:
        if order_id in _running_orders:
            LOGGER.info("Steam sign-out уже запущен для order_id=%s", order_id)
            return False
        _running_orders.add(order_id)

    thread = threading.Thread(
        target=_run_async,
        args=(rental, reason),
        daemon=True,
        name=f"steam-signout-{order_id}",
    )
    thread.start()
    return True
