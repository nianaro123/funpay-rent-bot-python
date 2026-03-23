# storage.py

import sqlite3

DB_PATH = "rent_bot.sqlite3"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_goods_columns(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(goods)").fetchall()}

    if "shared_secret" not in columns:
        conn.execute("ALTER TABLE goods ADD COLUMN shared_secret TEXT DEFAULT ''")


def init_db():
    conn = get_connection()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS goods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lot_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        login TEXT NOT NULL,
        password TEXT NOT NULL,
        note TEXT DEFAULT '',
        is_active INTEGER NOT NULL DEFAULT 1
    )
    """)

    ensure_goods_columns(conn)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS rentals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id TEXT UNIQUE NOT NULL,
        lot_id INTEGER NOT NULL,
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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS extensions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rental_id INTEGER NOT NULL,
        source TEXT NOT NULL,
        hours_added INTEGER NOT NULL,
        created_ts INTEGER NOT NULL,
        FOREIGN KEY(rental_id) REFERENCES rentals(id)
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS chat_state (
        chat_id TEXT PRIMARY KEY,
        last_message_id TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS admin_requests (
        chat_id TEXT PRIMARY KEY,
        last_request_ts INTEGER NOT NULL
    )
    """)

    conn.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_good
    ON rentals(good_id)
    WHERE closed = 0
    """)

    conn.commit()
    conn.close()


def add_good(
    lot_id: int,
    title: str,
    login: str,
    password: str,
    note: str = "",
    shared_secret: str = "",
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO goods(lot_id, title, login, password, note, shared_secret)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (lot_id, title, login, password, note, shared_secret))
    conn.commit()
    good_id = cur.lastrowid
    conn.close()
    return good_id


def list_goods():
    conn = get_connection()
    rows = conn.execute("""
        SELECT g.*,
               EXISTS(
                   SELECT 1
                   FROM rentals r
                   WHERE r.good_id = g.id AND r.closed = 0
               ) AS is_busy
        FROM goods g
        ORDER BY g.id ASC
    """).fetchall()
    conn.close()
    return rows


def get_good_by_id(good_id: int):
    conn = get_connection()
    row = conn.execute("""
        SELECT *
        FROM goods
        WHERE id = ?
        LIMIT 1
    """, (good_id,)).fetchone()
    conn.close()
    return row


def update_good(
    good_id: int,
    lot_id: int | None = None,
    title: str | None = None,
    login: str | None = None,
    password: str | None = None,
    note: str | None = None,
    shared_secret: str | None = None,
) -> bool:
    current = get_good_by_id(good_id)
    if not current:
        return False

    new_lot_id = current["lot_id"] if lot_id is None else lot_id
    new_title = current["title"] if title is None else title
    new_login = current["login"] if login is None else login
    new_password = current["password"] if password is None else password
    new_note = current["note"] if note is None else note
    new_shared_secret = current["shared_secret"] if shared_secret is None else shared_secret

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE goods
        SET lot_id = ?, title = ?, login = ?, password = ?, note = ?, shared_secret = ?
        WHERE id = ?
    """, (new_lot_id, new_title, new_login, new_password, new_note, new_shared_secret, good_id))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


def delete_good(good_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()

    busy = cur.execute("""
        SELECT 1
        FROM rentals
        WHERE good_id = ? AND closed = 0
        LIMIT 1
    """, (good_id,)).fetchone()

    if busy:
        conn.close()
        return False

    cur.execute("DELETE FROM goods WHERE id = ?", (good_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def set_good_active(good_id: int, is_active: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE goods
        SET is_active = ?
        WHERE id = ?
    """, (is_active, good_id))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


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
        SELECT r.*, g.login, g.password, g.note, g.title, g.shared_secret, g.lot_id AS good_lot_id
        FROM rentals r
        JOIN goods g ON g.id = r.good_id
        WHERE r.closed = 0
          AND r.buyer_id = ?
        ORDER BY r.id DESC
        LIMIT 1
    """, (buyer_id,)).fetchone()
    conn.close()
    return row


def get_active_rental_by_buyer_and_lot(buyer_id: int, lot_id: int):
    conn = get_connection()
    row = conn.execute("""
        SELECT r.*, g.login, g.password, g.note, g.title, g.shared_secret, g.lot_id AS good_lot_id
        FROM rentals r
        JOIN goods g ON g.id = r.good_id
        WHERE r.closed = 0
          AND r.buyer_id = ?
          AND r.lot_id = ?
        ORDER BY r.id DESC
        LIMIT 1
    """, (buyer_id, lot_id)).fetchone()
    conn.close()
    return row


def list_active_rentals_by_buyer(buyer_id: int):
    conn = get_connection()
    rows = conn.execute("""
        SELECT r.*, g.login, g.password, g.note, g.title, g.shared_secret, g.lot_id AS good_lot_id
        FROM rentals r
        JOIN goods g ON g.id = r.good_id
        WHERE r.closed = 0
          AND r.buyer_id = ?
        ORDER BY r.id DESC
    """, (buyer_id,)).fetchall()
    conn.close()
    return rows


def create_rental(order_id: str, lot_id: int, chat_id: str, buyer_id: int | None,
                  buyer_username: str | None, good_id: int, code: str,
                  start_ts: int, paid_end_ts: int, grace_end_ts: int):
    conn = get_connection()
    try:
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
    finally:
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
        SELECT r.*, g.login, g.password, g.note, g.title, g.shared_secret, g.lot_id AS good_lot_id
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


def get_last_message_id(chat_id: str):
    conn = get_connection()
    row = conn.execute("""
        SELECT last_message_id
        FROM chat_state
        WHERE chat_id = ?
    """, (str(chat_id),)).fetchone()
    conn.close()
    return row["last_message_id"] if row else None


def set_last_message_id(chat_id: str, message_id: str):
    conn = get_connection()
    conn.execute("""
        INSERT INTO chat_state (chat_id, last_message_id)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET last_message_id=excluded.last_message_id
    """, (str(chat_id), str(message_id)))
    conn.commit()
    conn.close()


def get_admin_request_ts(chat_id: str) -> int | None:
    conn = get_connection()
    row = conn.execute("""
        SELECT last_request_ts
        FROM admin_requests
        WHERE chat_id = ?
    """, (str(chat_id),)).fetchone()
    conn.close()
    return int(row["last_request_ts"]) if row else None


def set_admin_request_ts(chat_id: str, ts: int):
    conn = get_connection()
    conn.execute("""
        INSERT INTO admin_requests (chat_id, last_request_ts)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET last_request_ts=excluded.last_request_ts
    """, (str(chat_id), int(ts)))
    conn.commit()
    conn.close()