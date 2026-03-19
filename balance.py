# balance.py

import re
from bs4 import BeautifulSoup
from typing import Optional
from FunPayAPI import types, Account


class BalanceService:
    def __init__(self, account: Account, fallback_lot_id: Optional[int] = None):
        """
        account — авторизованный объект Account
        fallback_lot_id — запасной вариант через лот
        """
        self.account = account
        self.fallback_lot_id = fallback_lot_id

    def _parse_amount(self, text: str) -> float:
        text = text.replace("\xa0", " ").replace(",", ".")
        num = re.sub(r"[^\d.]", "", text)
        return float(num) if num else 0.0

    def _get_from_account_page(self) -> types.Balance:
        resp = self.account.method(
            "get",
            "account/balance",
            {"accept": "text/html,*/*"},
            {},
            raise_not_200=True
        )

        soup = BeautifulSoup(resp.text, "html.parser")

        elements = soup.select("span.balances-value")

        if len(elements) < 3:
            raise RuntimeError("Не удалось найти баланс на странице")

        rub = self._parse_amount(elements[0].get_text(strip=True))
        usd = self._parse_amount(elements[1].get_text(strip=True))
        eur = self._parse_amount(elements[2].get_text(strip=True))

        return types.Balance(rub, rub, usd, usd, eur, eur)

    def get(self) -> types.Balance:
        """
        Основной метод получения баланса
        """
        try:
            return self._get_from_account_page()
        except Exception:
            if self.fallback_lot_id is None:
                raise
            return self.account.get_balance(lot_id=self.fallback_lot_id)