import sqlite3
import time

conn = sqlite3.connect("rent_bot.sqlite3")
cur = conn.cursor()

cur.execute("SELECT order_id, paid_end_ts, grace_end_ts FROM rentals WHERE closed = 0")
rows = cur.fetchall()

now = int(time.time())

print("АКТИВНЫЕ АРЕНДЫ:\n")

for row in rows:
    order_id, paid, grace = row

    print(f"ORDER_ID: {order_id}")
    print(f"paid_end_ts: {paid} (осталось {paid - now} сек)")
    print(f"grace_end_ts: {grace} (осталось {grace - now} сек)")
    print("-" * 40)

conn.close()