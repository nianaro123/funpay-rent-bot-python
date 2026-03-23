# steam_guard.py

from steam_totp import generate_twofactor_code_for_time


def normalize_base64(value: str) -> str:
    value = value.strip()
    return value + "=" * (-len(value) % 4)


def generate_steam_guard_code(shared_secret: str | None) -> str | None:
    if not shared_secret:
        return None

    shared_secret = shared_secret.strip()
    if not shared_secret:
        return None

    normalized = normalize_base64(shared_secret)
    return generate_twofactor_code_for_time(shared_secret=normalized)