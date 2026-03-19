# storage.py

import sqlite3

DB_NAME = "rent_bot.sqlite3"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS goods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lot_id INTEGER NOT NULL DEFAULT 0,
        title TEXT NOT NULL,
        login TEXT NOT NULL,
        password TEXT NOT NULL,
        note TEXT DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS rentals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT UNIQUE NOT NULL,
        lot_id INTEGER NOT NULL DEFAULT 0,
        chat_id TEXT NOT NULL,
        buyer_id INTEGER,
        buyer_username TEXT,
        good_id INTEGER NOT NULL,
        code TEXT NOT NULL,
        start_ts INTEGER NOT NULL,
        paid_end_ts INTEGER NOT NULL,
        grace_end_ts INTEGER NOT NULL,
        warned_10m INTEGER NOT NULL DEFAULT 0,
        ended_msg_sent INTEGER NOT NULL DEFAULT 0,
        closed INTEGER NOT NULL DEFAULT 0,
        bonus_applied INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(good_id) REFERENCES goods(id)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS extensions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rental_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        hours_added INTEGER NOT NULL,
        created_ts INTEGER NOT NULL,
        FOREIGN KEY(rental_id) REFERENCES rentals(id)
    )
    """)

    conn.commit()
    conn.close()


def add_good(lot_id: int, title: str, login: str, password: str, note: str = ""):
    conn = get_connection()
    conn.execute("""
        INSERT INTO goods(lot_id, title, login, password, note)
        VALUES (?, ?, ?, ?, ?)
    """, (lot_id, title, login, password, note))
    conn.commit()
    conn.close()


def get_good_by_lot_id(lot_id: int):
    conn = get_connection()
    row = conn.execute("""
        SELECT *
        FROM goods
        WHERE lot_id = ?
          AND is_active = 1
          AND id NOT IN (
                SELECT good_id
                FROM rentals
                WHERE closed = 0
          )
        LIMIT 1
    """, (lot_id,)).fetchone()
    conn.close()
    return row


def get_good_by_marker(marker: str):
    conn = get_connection()
    row = conn.execute("""
        SELECT *
        FROM goods
        WHERE is_active = 1
          AND title LIKE ?
          AND id NOT IN (
                SELECT good_id
                FROM rentals
                WHERE closed = 0
          )
        LIMIT 1
    """, (f"%{marker}%",)).fetchone()
    conn.close()
    return row


def count_free_goods():
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM goods g
        WHERE g.is_active = 1
          AND g.id NOT IN (
                SELECT good_id
                FROM rentals
                WHERE closed = 0
          )
    """).fetchone()
    conn.close()
    return int(row["cnt"])


def get_active_rental_by_buyer(buyer_id: int):
    conn = get_connection()
    row = conn.execute("""
        SELECT r.*, g.login, g.password, g.note, g.title, g.lot_id AS good_lot_id
        FROM rentals r
        JOIN goods g ON g.id = r.good_id
        WHERE r.closed = 0
          AND r.buyer_id = ?
        ORDER BY r.id DESC
        LIMIT 1
    """, (buyer_id,)).fetchone()
    conn.close()
    return row


def create_rental(order_id: str, lot_id: int, chat_id: str, buyer_id: int | None,
                  buyer_username: str | None, good_id: int, code: str,
                  start_ts: int, paid_end_ts: int, grace_end_ts: int):
    conn = get_connection()
    conn.execute("""
        INSERT INTO rentals(
            order_id, lot_id, chat_id, buyer_id, buyer_username, good_id,
            code, start_ts, paid_end_ts, grace_end_ts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        order_id, lot_id, str(chat_id), buyer_id, buyer_username,
        good_id, code, start_ts, paid_end_ts, grace_end_ts
    ))
    conn.commit()
    conn.close()


def get_rental_by_order_id(order_id: str):
    conn = get_connection()
    row = conn.execute("""
        SELECT *
        FROM rentals
        WHERE order_id = ?
        LIMIT 1
    """, (order_id,)).fetchone()
    conn.close()
    return row


def list_active_rentals():
    conn = get_connection()
    rows = conn.execute("""
        SELECT r.*, g.login, g.password, g.note, g.title, g.lot_id AS good_lot_id
        FROM rentals r
        JOIN goods g ON g.id = r.good_id
        WHERE r.closed = 0
        ORDER BY r.paid_end_ts ASC
    """).fetchall()
    conn.close()
    return rows


def mark_warned(order_id: str):
    conn = get_connection()
    conn.execute("""
        UPDATE rentals
        SET warned_10m = 1
        WHERE order_id = ?
    """, (order_id,))
    conn.commit()
    conn.close()


def mark_ended_msg(order_id: str):
    conn = get_connection()
    conn.execute("""
        UPDATE rentals
        SET ended_msg_sent = 1
        WHERE order_id = ?
    """, (order_id,))
    conn.commit()
    conn.close()


def close_rental(order_id: str):
    conn = get_connection()
    conn.execute("""
        UPDATE rentals
        SET closed = 1
        WHERE order_id = ?
    """, (order_id,))
    conn.commit()
    conn.close()


def extend_rental(order_id: str, add_seconds: int):
    conn = get_connection()
    conn.execute("""
        UPDATE rentals
        SET paid_end_ts = paid_end_ts + ?,
            grace_end_ts = grace_end_ts + ?,
            warned_10m = 0,
            ended_msg_sent = 0
        WHERE order_id = ?
          AND closed = 0
    """, (add_seconds, add_seconds, order_id))
    conn.commit()
    conn.close()


def set_bonus_applied(order_id: str):
    conn = get_connection()
    conn.execute("""
        UPDATE rentals
        SET bonus_applied = 1
        WHERE order_id = ?
    """, (order_id,))
    conn.commit()
    conn.close()


def add_extension(rental_id: int, source: str, hours_added: int, created_ts: int):
    conn = get_connection()
    conn.execute("""
        INSERT INTO extensions(rental_id, source, hours_added, created_ts)
        VALUES (?, ?, ?, ?)
    """, (rental_id, source, hours_added, created_ts))
    conn.commit()
    conn.close()