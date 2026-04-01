import sqlite3
import time

ORDER_ID = "NHM9S14P"

conn = sqlite3.connect("rent_bot.sqlite3")
cur = conn.cursor()

now = int(time.time())

cur.execute("""
UPDATE rentals
SET paid_end_ts = ?, grace_end_ts = ?
WHERE order_id = ?
""", (now + 30, now + 60, ORDER_ID))

conn.commit()
conn.close()

print("Готово! Через ~30 сек аренда закончится")