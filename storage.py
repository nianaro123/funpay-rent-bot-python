import re
import sqlite3
from pathlib import Path
from settings import DB_PATH, AUTO_RAISE_ENABLED, AUTO_RAISE_INTERVAL_SEC



def get_connection():
    db_path = Path(DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def extract_marker_from_title(title: str) -> str:
    if not title:
        return ""
    match = re.search(r"(#\d+)", title)
    return match.group(1) if match else ""


def ensure_goods_columns(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(goods)").fetchall()}

    if "shared_secret" not in columns:
        conn.execute("ALTER TABLE goods ADD COLUMN shared_secret TEXT DEFAULT ''")

    if "marker" not in columns:
        conn.execute("ALTER TABLE goods ADD COLUMN marker TEXT DEFAULT ''")

    rows = conn.execute("SELECT id, title, marker FROM goods").fetchall()
    for row in rows:
        extracted_marker = extract_marker_from_title(row["title"] or "")
        current_marker = (row["marker"] or "").strip()

        # ВАЖНО:
        # синхронизируем marker с title не только когда marker пустой,
        # но и когда он устарел после редактирования title.
        if extracted_marker and current_marker != extracted_marker:
            conn.execute(
                "UPDATE goods SET marker = ? WHERE id = ?",
                (extracted_marker, row["id"])
            )


def ensure_chat_state_columns(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(chat_state)").fetchall()}

    if "welcomed" not in columns:
        conn.execute("ALTER TABLE chat_state ADD COLUMN welcomed INTEGER NOT NULL DEFAULT 0")


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
        last_message_id TEXT,
        welcomed INTEGER NOT NULL DEFAULT 0
    )
    """)

    ensure_chat_state_columns(conn)

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

    conn.execute("""
    CREATE TABLE IF NOT EXISTS order_events (
        order_id TEXT PRIMARY KEY,
        good_id INTEGER,
        good_title_snapshot TEXT NOT NULL,
        login_snapshot TEXT NOT NULL,
        buyer_id INTEGER,
        buyer_username TEXT,
        marker TEXT,
        hours INTEGER NOT NULL DEFAULT 0,
        amount_rub REAL NOT NULL DEFAULT 0,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        created_ts INTEGER NOT NULL,
        confirmed_ts INTEGER,
        refunded_ts INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS bot_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    conn.execute("""
        INSERT OR IGNORE INTO bot_settings(key, value)
        VALUES ('auto_raise_enabled', ?)
    """, ("1" if AUTO_RAISE_ENABLED else "0",))
    conn.execute("""
        INSERT OR IGNORE INTO bot_settings(key, value)
        VALUES ('auto_raise_interval_sec', ?)
    """, (str(int(AUTO_RAISE_INTERVAL_SEC)),))

    conn.commit()
    conn.close()


def get_bot_setting(key: str, default: str | None = None) -> str | None:
    conn = get_connection()
    row = conn.execute("""
        SELECT value
        FROM bot_settings
        WHERE key = ?
        LIMIT 1
    """, (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_bot_setting(key: str, value: str) -> None:
    conn = get_connection()
    conn.execute("""
        INSERT INTO bot_settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, value))
    conn.commit()
    conn.close()


def get_auto_raise_enabled(default: bool = AUTO_RAISE_ENABLED) -> bool:
    raw = (get_bot_setting("auto_raise_enabled", "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def set_auto_raise_enabled(enabled: bool) -> None:
    set_bot_setting("auto_raise_enabled", "1" if enabled else "0")


def get_auto_raise_interval_sec(default: int = AUTO_RAISE_INTERVAL_SEC) -> int:
    raw = (get_bot_setting("auto_raise_interval_sec", str(int(default))) or "").strip()
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


def set_auto_raise_interval_sec(interval_sec: int) -> None:
    set_bot_setting("auto_raise_interval_sec", str(int(interval_sec)))


def add_good(
    lot_id: int,
    title: str,
    login: str,
    password: str,
    note: str = "",
    shared_secret: str = "",
    marker: str = "",
):
    if not marker:
        marker = extract_marker_from_title(title)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO goods(lot_id, title, login, password, note, shared_secret, marker)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (lot_id, title, login, password, note, shared_secret, marker))
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
    marker: str | None = None,
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

    # Если marker явно не передан:
    # - при изменении title пересчитываем marker из нового title
    # - если title не менялся, сохраняем текущий marker
    if marker is None:
        if title is not None and new_title != current["title"]:
            new_marker = extract_marker_from_title(new_title)
        else:
            new_marker = (current["marker"] or "").strip()

        if not new_marker:
            new_marker = extract_marker_from_title(new_title)
    else:
        new_marker = marker.strip()
        if not new_marker:
            new_marker = extract_marker_from_title(new_title)

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE goods
        SET lot_id = ?, title = ?, login = ?, password = ?, note = ?, shared_secret = ?, marker = ?
        WHERE id = ?
    """, (
        new_lot_id,
        new_title,
        new_login,
        new_password,
        new_note,
        new_shared_secret,
        new_marker,
        good_id
    ))
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
          AND marker = ?
          AND id NOT IN (
                SELECT good_id
                FROM rentals
                WHERE closed = 0
          )
        ORDER BY id ASC
        LIMIT 1
    """, (marker,)).fetchone()
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
        SELECT
            r.*,
            g.login,
            g.password,
            g.note,
            g.title,
            g.marker,
            g.shared_secret,
            g.lot_id AS good_lot_id
        FROM rentals r
        JOIN goods g ON g.id = r.good_id
        WHERE r.closed = 0
          AND r.buyer_id = ?
        ORDER BY r.id DESC
        LIMIT 1
    """, (buyer_id,)).fetchone()
    conn.close()
    return row


def get_active_rental_by_buyer_and_marker(buyer_id: int, marker: str):
    conn = get_connection()
    row = conn.execute("""
        SELECT
            r.*,
            g.login,
            g.password,
            g.note,
            g.title,
            g.marker,
            g.shared_secret,
            g.lot_id AS good_lot_id
        FROM rentals r
        JOIN goods g ON g.id = r.good_id
        WHERE r.closed = 0
          AND r.buyer_id = ?
          AND g.marker = ?
        ORDER BY r.id DESC
        LIMIT 1
    """, (buyer_id, marker)).fetchone()
    conn.close()
    return row


def list_active_rentals_by_buyer(buyer_id: int):
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            r.*,
            g.login,
            g.password,
            g.note,
            g.title,
            g.marker,
            g.shared_secret,
            g.lot_id AS good_lot_id
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


def get_rental_with_good_by_order_id(order_id: str):
    conn = get_connection()
    row = conn.execute("""
        SELECT
            r.*,
            g.login,
            g.password,
            g.note,
            g.title,
            g.marker,
            g.shared_secret,
            g.lot_id AS good_lot_id
        FROM rentals r
        JOIN goods g ON g.id = r.good_id
        WHERE r.order_id = ?
        LIMIT 1
    """, (order_id,)).fetchone()
    conn.close()
    return row


def list_active_rentals():
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            r.*,
            g.login,
            g.password,
            g.note,
            g.title,
            g.marker,
            g.shared_secret,
            g.lot_id AS good_lot_id
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


def set_bonus_applied(order_id: str) -> bool:
    conn = get_connection()
    cur = conn.execute("""
        UPDATE rentals
        SET bonus_applied = 1
        WHERE order_id = ?
          AND closed = 0
          AND bonus_applied = 0
    """, (order_id,))
    conn.commit()
    updated = cur.rowcount > 0
    conn.close()
    return updated


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
        ON CONFLICT(chat_id) DO UPDATE SET last_message_id = excluded.last_message_id
    """, (str(chat_id), str(message_id)))
    conn.commit()
    conn.close()


def is_chat_welcomed(chat_id: str) -> bool:
    conn = get_connection()
    row = conn.execute("""
        SELECT welcomed
        FROM chat_state
        WHERE chat_id = ?
    """, (str(chat_id),)).fetchone()
    conn.close()
    return bool(row["welcomed"]) if row else False


def mark_chat_welcomed(chat_id: str):
    conn = get_connection()
    conn.execute("""
        INSERT INTO chat_state (chat_id, welcomed)
        VALUES (?, 1)
        ON CONFLICT(chat_id) DO UPDATE SET welcomed = 1
    """, (str(chat_id),))
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
        ON CONFLICT(chat_id) DO UPDATE SET last_request_ts = excluded.last_request_ts
    """, (str(chat_id), int(ts)))
    conn.commit()
    conn.close()


def log_order_event(
    order_id: str,
    good_id: int | None,
    good_title_snapshot: str,
    login_snapshot: str,
    buyer_id: int | None,
    buyer_username: str | None,
    marker: str | None,
    hours: int,
    amount_rub: float,
    kind: str,
    status: str,
    created_ts: int,
):
    conn = get_connection()
    conn.execute("""
        INSERT OR REPLACE INTO order_events(
            order_id,
            good_id,
            good_title_snapshot,
            login_snapshot,
            buyer_id,
            buyer_username,
            marker,
            hours,
            amount_rub,
            kind,
            status,
            created_ts,
            confirmed_ts,
            refunded_ts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
    """, (
        order_id,
        good_id,
        good_title_snapshot,
        login_snapshot,
        buyer_id,
        buyer_username,
        marker,
        hours,
        amount_rub,
        kind,
        status,
        created_ts,
    ))
    conn.commit()
    conn.close()


def mark_order_confirmed(order_id: str, confirmed_ts: int):
    conn = get_connection()
    conn.execute("""
        UPDATE order_events
        SET status = 'confirmed',
            confirmed_ts = ?
        WHERE order_id = ?
    """, (confirmed_ts, order_id))
    conn.commit()
    conn.close()


def mark_order_refunded(order_id: str, refunded_ts: int):
    conn = get_connection()
    conn.execute("""
        UPDATE order_events
        SET status = 'refunded',
            refunded_ts = ?
        WHERE order_id = ?
    """, (refunded_ts, order_id))
    conn.commit()
    conn.close()


def get_confirmed_income_total(start_ts: int | None = None):
    conn = get_connection()

    if start_ts is None:
        row = conn.execute("""
            SELECT
                COUNT(*) AS orders_count,
                COALESCE(SUM(amount_rub), 0) AS total_rub
            FROM order_events
            WHERE status = 'confirmed'
        """).fetchone()
    else:
        row = conn.execute("""
            SELECT
                COUNT(*) AS orders_count,
                COALESCE(SUM(amount_rub), 0) AS total_rub
            FROM order_events
            WHERE status = 'confirmed'
              AND confirmed_ts >= ?
        """, (start_ts,)).fetchone()

    conn.close()
    return row


def get_confirmed_income_by_good(start_ts: int | None = None):
    conn = get_connection()

    if start_ts is None:
        rows = conn.execute("""
            SELECT
                good_id,
                login_snapshot,
                good_title_snapshot,
                marker,
                COUNT(*) AS orders_count,
                COALESCE(SUM(amount_rub), 0) AS total_rub
            FROM order_events
            WHERE status = 'confirmed'
            GROUP BY good_id, login_snapshot, good_title_snapshot, marker
            ORDER BY total_rub DESC
        """).fetchall()
    else:
        rows = conn.execute("""
            SELECT
                good_id,
                login_snapshot,
                good_title_snapshot,
                marker,
                COUNT(*) AS orders_count,
                COALESCE(SUM(amount_rub), 0) AS total_rub
            FROM order_events
            WHERE status = 'confirmed'
              AND confirmed_ts >= ?
            GROUP BY good_id, login_snapshot, good_title_snapshot, marker
            ORDER BY total_rub DESC
        """, (start_ts,)).fetchall()

    conn.close()
    return rows
