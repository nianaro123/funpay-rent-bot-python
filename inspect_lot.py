from FunPayAPI import Account
from config import GOLDEN_KEY, USER_AGENT

LOT_ID = 62877335  # поставь свой настоящий lot_id

acc = Account(GOLDEN_KEY, user_agent=USER_AGENT).get()

response = acc.method(
    "get",
    f"lots/offerEdit?offer={LOT_ID}",
    {
        "accept": "*/*",
        "content-type": "application/json",
        "x-requested-with": "XMLHttpRequest",
    },
    {},
    raise_not_200=False,
)

print("STATUS:", response.status_code)
print("CONTENT-TYPE:", response.headers.get("content-type"))
print("TEXT PREVIEW:")
print(response.text[:2000])

with open("inspect_lot_response.html", "w", encoding="utf-8") as f:
    f.write(response.text)