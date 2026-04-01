#import_goods.py
import json
from storage import init_db, add_good

init_db()

with open("goods.json", "r", encoding="utf-8") as f:
    goods = json.load(f)

for item in goods:
    add_good(
        lot_id=item["lot_id"],
        title=item["title"],
        login=item["login"],
        password=item["password"],
        note=item.get("note", "")
    )

print("Импорт завершён.")