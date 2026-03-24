# steam_relogin_worker.py

import asyncio
import logging
import sys

from pysteamauth.auth import Steam


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
LOGGER = logging.getLogger(__name__)


def normalize_base64(value: str) -> str:
    value = value.strip()
    return value + "=" * (-len(value) % 4)


async def relogin(login: str, password: str, shared_secret: str) -> None:
    secret = normalize_base64(shared_secret)

    steam = Steam(
        login=login,
        password=password,
        shared_secret=secret,
    )

    await steam.login_to_steam()
    await steam.request("https://steamcommunity.com")


def main():
    if len(sys.argv) != 4:
        print("Usage: steam_relogin_worker.py <login> <password> <shared_secret>")
        sys.exit(2)

    login = sys.argv[1]
    password = sys.argv[2]
    shared_secret = sys.argv[3]

    try:
        asyncio.run(relogin(login, password, shared_secret))
        print("OK")
        sys.exit(0)
    except Exception as e:
        LOGGER.exception("Steam relogin failed: %s", e)
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()