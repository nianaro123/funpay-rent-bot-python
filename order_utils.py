# order_utils.py

import re
from bs4 import BeautifulSoup
from FunPayAPI import Account


def get_order_html(acc: Account, order_id: str) -> str:
    response = acc.method(
        "get",
        f"orders/{order_id}/",
        {"accept": "*/*"},
        {},
        raise_not_200=True
    )
    html = response.content.decode()

    with open("order_debug.html", "w", encoding="utf-8") as f:
        f.write(html)

    return html


def extract_hours_from_order_html(html: str) -> int | None:
    soup = BeautifulSoup(html, "html.parser")

    for div in soup.find_all("div", class_="param-item"):
        h5 = div.find("h5")
        if not h5:
            continue

        title = h5.get_text(strip=True).lower()
        if title == "количество":
            text_bold = div.find("div", class_="text-bold")
            if not text_bold:
                return None

            text = text_bold.get_text(" ", strip=True).lower()
            match = re.search(r"(\d+)\s*шт", text)
            if match:
                return int(match.group(1))
            return None

    return None


def extract_short_description_from_order_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")

    for div in soup.find_all("div", class_="param-item"):
        h5 = div.find("h5")
        if not h5:
            continue

        title = h5.get_text(strip=True).lower()
        if title == "краткое описание":
            content_div = div.find("div")
            if content_div:
                return content_div.get_text(" ", strip=True)

    return None


def extract_good_marker(short_description: str | None) -> str | None:
    if not short_description:
        return None

    match = re.search(r"#\d+\b", short_description)
    if match:
        return match.group(0)

    return None