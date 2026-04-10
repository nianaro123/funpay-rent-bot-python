# admin_bot.py

import logging
import re
import time

from FunPayAPI import Account
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from settings import (
    TELEGRAM_ADMIN_BOT_TOKEN,
    TELEGRAM_ADMIN_USER_ID,
    GOLDEN_KEY,
    USER_AGENT,
)
from storage import (
    init_db,
    add_good,
    delete_good,
    list_goods,
    set_good_active,
    list_active_rentals,
    count_free_goods,
    update_good,
    get_good_by_id,
    get_confirmed_income_total,
    get_confirmed_income_by_good,
    get_rental_by_order_id,
    get_rental_with_good_by_order_id,
    extend_rental,
    add_extension,
    close_rental,
    get_auto_raise_enabled,
    set_auto_raise_enabled,
    get_auto_raise_interval_sec,
    set_auto_raise_interval_sec,
)
from lot_manager import LotManager
from steam_session_worker import trigger_steam_sign_out_async

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

LOGGER = logging.getLogger(__name__)

ADD_LOT_LINK, ADD_LOGIN, ADD_PASSWORD, ADD_NOTE, ADD_SHARED_SECRET = range(5)
EDIT_GOOD_ID, EDIT_LOT_LINK, EDIT_LOGIN, EDIT_PASSWORD, EDIT_NOTE, EDIT_SHARED_SECRET = range(5, 11)
CLOSE_RENT_ROW = 11
AUTO_RAISE_MENU, AUTO_RAISE_INTERVAL_INPUT = range(12, 14)

FUNPAY_ACC = None

BTN_ADD_GOOD = "➕ Add Good"
BTN_EDIT_GOOD = "✏️ Edit Good"
BTN_LIST_GOODS = "📦 List Goods"
BTN_ACTIVE_RENTALS = "📊 Active Rentals"
BTN_FREE_GOODS = "🟢 Free Goods"
BTN_STATS = "💰 Stats"
BTN_CLOSE_RENTAL = "⛔ Close Rental"
BTN_UPDATE_TITLES = "🔄 Update Titles"
BTN_AUTO_RAISE = "🚀 Auto Raise Lots"
BTN_AUTO_RAISE_ENABLE = "✅ Включить"
BTN_AUTO_RAISE_DISABLE = "⛔ Отключить"
BTN_AUTO_RAISE_SET_TIME = "⏱ Задать время"
BTN_AUTO_RAISE_BACK = "⬅️ Назад"

UNSAFE_CHAT_CHARS_RE = re.compile(r"[\u0000-\u001f\u007f\u200b\u200c\u200d\u2060\u2063\u2064\ufeff]")
SAFE_FUNPAY_CHAT_CHARS_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё\s\.\,\!\?\:\;\-\+\#\(\)\/]")
MULTISPACE_RE = re.compile(r"\s+")
MAX_FUNPAY_MESSAGE_LEN = 450


def get_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BTN_ADD_GOOD, BTN_EDIT_GOOD],
            [BTN_LIST_GOODS, BTN_ACTIVE_RENTALS],
            [BTN_FREE_GOODS, BTN_STATS],
            [BTN_CLOSE_RENTAL, BTN_UPDATE_TITLES],
            [BTN_AUTO_RAISE],
        ],
        resize_keyboard=True,
    )


def get_auto_raise_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [BTN_AUTO_RAISE_ENABLE, BTN_AUTO_RAISE_DISABLE],
            [BTN_AUTO_RAISE_SET_TIME, BTN_AUTO_RAISE_BACK],
        ],
        resize_keyboard=True,
    )


def format_auto_raise_status() -> str:
    enabled = get_auto_raise_enabled()
    interval_sec = get_auto_raise_interval_sec()
    interval_min = max(1, interval_sec // 60)
    status = "включено" if enabled else "отключено"
    return (
        "🚀 Настройки автоподнятия лотов:\n"
        f"Статус: {status}\n"
        f"Интервал: {interval_min} мин.\n\n"
        "Выберите действие:"
    )


def parse_lot_id_from_input(value: str) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None

    if raw.isdigit():
        return int(raw)

    match = re.search(r"[?&]id=(\d+)", raw)
    if match:
        return int(match.group(1))

    return None


def fetch_lot_title(lot_id: int) -> str:
    if FUNPAY_ACC is None:
        raise RuntimeError("FunPay account is not initialized in admin_bot.py")

    manager = LotManager(FUNPAY_ACC)
    ru, en = manager.get_summary_fields(lot_id)

    title = (ru or "").strip() or (en or "").strip()
    if not title:
        raise RuntimeError("Failed to fetch lot title from FunPay")

    return title


def init_funpay_account():
    global FUNPAY_ACC
    try:
        FUNPAY_ACC = Account(GOLDEN_KEY, user_agent=USER_AGENT).get()
        LOGGER.info("FunPay account initialized in admin_bot.py")
    except Exception as e:
        FUNPAY_ACC = None
        LOGGER.exception("Failed to init FunPay account in admin_bot.py: %s", e)


def _sanitize_chat_message(text: str) -> str:
    safe_text = UNSAFE_CHAT_CHARS_RE.sub("", text or "")
    safe_text = safe_text.strip()
    return safe_text


def _to_funpay_plain_text(text: str) -> str:
    text = _sanitize_chat_message(text)
    text = SAFE_FUNPAY_CHAT_CHARS_RE.sub(" ", text)
    text = MULTISPACE_RE.sub(" ", text).strip()
    return text[:MAX_FUNPAY_MESSAGE_LEN].strip()


def _send_buyer_message_with_fallback(chat_id: int | str, text: str) -> bool:
    if FUNPAY_ACC is None:
        return False

    original = (text or "").strip()
    sanitized = _sanitize_chat_message(original)
    plain = _to_funpay_plain_text(sanitized)
    fallback = "Продавец вручную продлил аренду. Проверьте оставшееся время в чате заказа."

    attempts = [
        ("original", original),
        ("sanitized", sanitized),
        ("plain", plain),
        ("fallback", fallback),
    ]

    for attempt_name, attempt_text in attempts:
        if not attempt_text:
            continue
        try:
            FUNPAY_ACC.send_message(chat_id, attempt_text)
            if attempt_name != "original":
                LOGGER.warning(
                    "Buyer message sent via %s attempt for chat_id=%s",
                    attempt_name,
                    chat_id,
                )
            return True
        except Exception as e:
            LOGGER.warning(
                "Buyer message send failed (%s) for chat_id=%s: %s",
                attempt_name,
                chat_id,
                e,
            )
            continue

    return False


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == TELEGRAM_ADMIN_USER_ID)


async def admin_only(update: Update) -> bool:
    if not is_admin(update):
        if update.message:
            await update.message.reply_text("Нет доступа.")
        return False
    return True


def format_remaining_time(rental) -> str:
    now = int(time.time())
    paid_end_ts = int(rental["paid_end_ts"])
    grace_end_ts = int(rental["grace_end_ts"])

    if now < paid_end_ts:
        remaining = paid_end_ts - now
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        if hours > 0:
            return f"{hours} ч. {minutes} мин."
        return f"{minutes} мин."

    if now < grace_end_ts:
        remaining = grace_end_ts - now
        minutes = remaining // 60
        seconds = remaining % 60
        if minutes > 0:
            return f"оплачено истекло, буфер {minutes} мин. {seconds} сек."
        return f"оплачено истекло, буфер {seconds} сек."

    return "ожидает закрытия"


def get_rentals_snapshot():
    rentals = list_active_rentals()
    return list(rentals)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    text = (
        "Админ-бот запущен.\n\n"
        "Кнопки:\n"
        f"{BTN_ADD_GOOD} — добавить товар\n"
        f"{BTN_EDIT_GOOD} — редактировать товар\n"
        f"{BTN_LIST_GOODS} — список товаров\n"
        f"{BTN_ACTIVE_RENTALS} — активные аренды\n"
        f"{BTN_FREE_GOODS} — число свободных товаров\n"
        f"{BTN_STATS} — статистика за всё время\n"
        f"{BTN_CLOSE_RENTAL} — вручную закрыть аренду\n\n"
        f"{BTN_UPDATE_TITLES} — обновить title всех лотов из FunPay\n\n"
        f"{BTN_AUTO_RAISE} — меню автоподнятия лотов\n\n"
        "Команды:\n"
        "/goods — список товаров\n"
        "/free — число свободных товаров\n"
        "/rentals — активные аренды + оставшееся время\n"
        "/extendrent ORDER_ID HOURS — вручную продлить аренду клиенту\n"
        "/extendrentrow N HOURS — вручную продлить аренду по номеру строки из /rentals\n"
        "/closerent ORDER_ID — вручную закрыть аренду по номеру заказа\n"
        "/closerentrow N — вручную закрыть аренду по номеру строки из /rentals\n"
        "/addgood — пошаговое добавление товара\n"
        "/editgood — пошаговое редактирование товара\n"
        "/disablegood good_id\n"
        "/enablegood good_id\n"
        "/delgood good_id\n"
        "/updatetitles — подтянуть актуальные title всех лотов из FunPay\n"
        "/autoraise — меню автоподнятия лотов\n"
        "/stats day|week|month|all — доход по подтверждённым заказам\n"
        "/cancel — отменить текущий мастер"
    )
    await update.message.reply_text(text, reply_markup=get_main_keyboard())


async def admin_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    text = (update.message.text or "").strip()

    if text == BTN_ADD_GOOD:
        return await addgood_start(update, context)

    if text == BTN_EDIT_GOOD:
        return await editgood_start(update, context)

    if text == BTN_LIST_GOODS:
        return await goods_cmd(update, context)

    if text == BTN_ACTIVE_RENTALS:
        return await rentals_cmd(update, context)

    if text == BTN_FREE_GOODS:
        return await free_cmd(update, context)

    if text == BTN_STATS:
        context.args = ["all"]
        return await stats_cmd(update, context)

    if text == BTN_CLOSE_RENTAL:
        return await closerent_start(update, context)

    if text == BTN_UPDATE_TITLES:
        return await updatetitles_cmd(update, context)

    if text == BTN_AUTO_RAISE:
        return await autoraise_menu_start(update, context)


async def addgood_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await addgood_start(update, context)


async def editgood_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await editgood_start(update, context)


async def free_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await free_cmd(update, context)


async def stats_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.args = ["all"]
    return await stats_cmd(update, context)


async def updatetitles_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await updatetitles_cmd(update, context)


async def autoraise_menu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    await update.message.reply_text(
        format_auto_raise_status(),
        reply_markup=get_auto_raise_keyboard(),
    )
    return AUTO_RAISE_MENU


async def autoraise_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await autoraise_menu_start(update, context)


async def autoraise_enable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    set_auto_raise_enabled(True)
    await update.message.reply_text(
        f"✅ Автоподнятие включено.\n\n{format_auto_raise_status()}",
        reply_markup=get_auto_raise_keyboard(),
    )
    return AUTO_RAISE_MENU


async def autoraise_disable(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    set_auto_raise_enabled(False)
    await update.message.reply_text(
        f"⛔ Автоподнятие отключено.\n\n{format_auto_raise_status()}",
        reply_markup=get_auto_raise_keyboard(),
    )
    return AUTO_RAISE_MENU


async def autoraise_set_time_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    await update.message.reply_text(
        "Введите интервал автоподнятия в минутах.\n"
        "По умолчанию: 120.\n"
        "Пример: 45",
        reply_markup=get_auto_raise_keyboard(),
    )
    return AUTO_RAISE_INTERVAL_INPUT


async def autoraise_set_time_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    raw = (update.message.text or "").strip()
    try:
        minutes = int(raw)
    except ValueError:
        await update.message.reply_text("Введите целое число минут. Например: 120")
        return AUTO_RAISE_INTERVAL_INPUT

    if minutes <= 0:
        await update.message.reply_text("Интервал должен быть больше 0 минут.")
        return AUTO_RAISE_INTERVAL_INPUT

    set_auto_raise_interval_sec(minutes * 60)
    await update.message.reply_text(
        f"✅ Интервал автоподнятия установлен: {minutes} мин.\n\n{format_auto_raise_status()}",
        reply_markup=get_auto_raise_keyboard(),
    )
    return AUTO_RAISE_MENU


async def autoraise_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    await update.message.reply_text("Возврат в главное меню.", reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Операция отменена.", reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def goods_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    goods = list_goods()
    if not goods:
        await update.message.reply_text("Товаров в базе нет.")
        return

    lines = ["📦 Товары в базе:"]
    for i, g in enumerate(goods, start=1):
        status = "ЗАНЯТ" if g["is_busy"] else ("АКТИВЕН" if g["is_active"] else "ОТКЛЮЧЕН")
        has_secret = "yes" if g["shared_secret"] else "no"
        lines.append(
            f"\n{i}. good_id={g['id']}\n"
            f"Статус: {status}\n"
            f"lot_id: {g['lot_id']}\n"
            f"title: {g['title']}\n"
            f"login: {g['login']}\n"
            f"shared_secret: {has_secret}"
        )

    await update.message.reply_text("\n".join(lines))


async def free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    free_count = count_free_goods()
    await update.message.reply_text(f"Свободных товаров: {free_count}")


async def rentals_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    rentals = get_rentals_snapshot()
    if not rentals:
        await update.message.reply_text("Активных аренд нет.")
        return

    lines = ["🧾 Активные аренды:"]
    for i, r in enumerate(rentals, start=1):
        remaining_text = format_remaining_time(r)
        buyer_name = r["buyer_username"] if r["buyer_username"] else "unknown"
        lines.append(
            f"\n{i}. Заказ #{r['order_id']}\n"
            f"Клиент: {buyer_name}\n"
            f"buyer_id: {r['buyer_id']}\n"
            f"good_id: {r['good_id']}\n"
            f"lot_id: {r['good_lot_id']}\n"
            f"title: {r['title']}\n"
            f"Осталось: {remaining_text}"
        )

    await update.message.reply_text("\n".join(lines))


async def _close_rental_internal(order_id: str) -> tuple[bool, str]:
    rental = get_rental_by_order_id(order_id)
    if not rental:
        return False, f"❌ Заказ #{order_id} не найден в rentals."

    if rental["closed"]:
        return False, f"❌ Заказ #{order_id} уже закрыт."

    rental_snapshot = get_rental_with_good_by_order_id(order_id)

    try:
        lot_id = 0
        if rental_snapshot and rental_snapshot["good_lot_id"]:
            lot_id = int(rental_snapshot["good_lot_id"])
        elif rental["lot_id"]:
            lot_id = int(rental["lot_id"])

        if lot_id and FUNPAY_ACC is not None:
            manager = LotManager(FUNPAY_ACC)
            manager.set_lot_free(lot_id)
            LOGGER.info("Лот %s переведён в статус 'Свободен!' при ручном закрытии заказа %s", lot_id, order_id)
    except Exception as e:
        LOGGER.exception("Не удалось вернуть лот в статус 'Свободен!' для order_id=%s: %s", order_id, e)

    try:
        close_rental(order_id)
        LOGGER.info("Аренда вручную закрыта, order_id=%s", order_id)
    except Exception as e:
        LOGGER.exception("Не удалось закрыть аренду вручную, order_id=%s: %s", order_id, e)
        return False, f"❌ Не удалось закрыть заказ #{order_id}."

    buyer_msg_sent = False
    if FUNPAY_ACC is not None and rental_snapshot:
        try:
            FUNPAY_ACC.send_message(
                rental_snapshot["chat_id"],
                f"⛔ Аренда по заказу #{order_id} была завершена продавцом вручную.\n"
                f"Аккаунт возвращён в пул. Если это ошибка — напишите /admin"
            )
            buyer_msg_sent = True
        except Exception as e:
            LOGGER.exception("Не удалось отправить сообщение клиенту для order_id=%s: %s", order_id, e)

    sign_out_started = False
    try:
        if rental_snapshot:
            sign_out_started = trigger_steam_sign_out_async(rental_snapshot, reason="admin_manual_close")
    except Exception as e:
        LOGGER.exception("Не удалось запустить Steam sign-out для order_id=%s: %s", order_id, e)

    text = (
        f"✅ Заказ #{order_id} закрыт вручную.\n"
        f"Сообщение клиенту: {'отправлено' if buyer_msg_sent else 'не отправлено'}\n"
        f"Steam sign-out: {'запущен' if sign_out_started else 'не запущен'}"
    )
    return True, text


async def closerent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "Формат: /closerent ORDER_ID\n"
            "Пример: /closerent ANU383LY"
        )
        return

    order_id = context.args[0].strip().upper()
    _, text = await _close_rental_internal(order_id)
    await update.message.reply_text(text)


async def closerentrow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if len(context.args) != 1:
        await update.message.reply_text(
            "Формат: /closerentrow N\n"
            "Сначала вызови /rentals, потом используй номер строки.\n"
            "Пример: /closerentrow 2"
        )
        return

    try:
        row_num = int(context.args[0])
    except ValueError:
        await update.message.reply_text("N должен быть числом.")
        return

    if row_num <= 0:
        await update.message.reply_text("N должен быть больше 0.")
        return

    rentals = get_rentals_snapshot()
    if not rentals:
        await update.message.reply_text("Активных аренд нет.")
        return

    if row_num > len(rentals):
        await update.message.reply_text(
            f"❌ Строки {row_num} нет. Сейчас активных аренд: {len(rentals)}."
        )
        return

    rental = rentals[row_num - 1]
    order_id = rental["order_id"]

    _, text = await _close_rental_internal(order_id)
    await update.message.reply_text(f"Строка {row_num}:\n{text}")


async def closerent_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    rentals = get_rentals_snapshot()
    if not rentals:
        await update.message.reply_text("Активных аренд нет.", reply_markup=get_main_keyboard())
        return ConversationHandler.END

    lines = [
        "Введите номер строки аренды из списка /rentals, которую нужно закрыть.",
        "",
        "Пример: 1",
        "Или /cancel для отмены.",
    ]
    await update.message.reply_text("\n".join(lines))
    return CLOSE_RENT_ROW


async def closerent_row_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    try:
        row_num = int(text)
    except ValueError:
        await update.message.reply_text("Введите номер строки числом. Например: 1")
        return CLOSE_RENT_ROW

    if row_num <= 0:
        await update.message.reply_text("Номер строки должен быть больше 0.")
        return CLOSE_RENT_ROW

    rentals = get_rentals_snapshot()
    if not rentals:
        await update.message.reply_text("Активных аренд нет.", reply_markup=get_main_keyboard())
        return ConversationHandler.END

    if row_num > len(rentals):
        await update.message.reply_text(
            f"❌ Строки {row_num} нет. Сейчас активных аренд: {len(rentals)}."
        )
        return CLOSE_RENT_ROW

    rental = rentals[row_num - 1]
    order_id = rental["order_id"]

    _, result_text = await _close_rental_internal(order_id)

    await update.message.reply_text(
        f"Строка {row_num}:\n{result_text}",
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def extendrent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Формат: /extendrent ORDER_ID HOURS\n"
            "Пример: /extendrent DD6RLVKJ 2"
        )
        return

    order_id = context.args[0].strip().upper()

    try:
        hours = int(context.args[1])
    except ValueError:
        await update.message.reply_text("HOURS должно быть числом.")
        return

    if hours <= 0:
        await update.message.reply_text("Количество часов должно быть больше 0.")
        return

    rental = get_rental_by_order_id(order_id)
    if not rental:
        await update.message.reply_text(f"❌ Заказ #{order_id} не найден в rentals.")
        return

    if rental["closed"]:
        await update.message.reply_text(f"❌ Заказ #{order_id} уже закрыт.")
        return

    try:
        add_seconds = hours * 3600
        extend_rental(order_id, add_seconds)
        add_extension(rental["id"], "admin_manual", hours, int(time.time()))
    except Exception as e:
        LOGGER.exception("Failed to extend rental manually for order_id=%s: %s", order_id, e)
        await update.message.reply_text(f"❌ Не удалось продлить заказ #{order_id}.")
        return

    updated_rental = get_rental_by_order_id(order_id)
    remaining_text = format_remaining_time(updated_rental)

    buyer_msg_sent = _send_buyer_message_with_fallback(
        updated_rental["chat_id"],
        (
            f"✅ Продавец вручную продлил вашу аренду по заказу #{order_id} на {hours} ч.\n"
            f"⏱ Текущее оставшееся время: {remaining_text}"
        ),
    )

    text = (
        f"✅ Заказ #{order_id} продлён на {hours} ч.\n"
        f"Текущее время: {remaining_text}\n"
        f"Сообщение клиенту: {'отправлено' if buyer_msg_sent else 'не отправлено'}"
    )
    await update.message.reply_text(text)


async def extendrentrow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Формат: /extendrentrow N HOURS\n"
            "Сначала вызови /rentals, потом используй номер строки.\n"
            "Пример: /extendrentrow 2 3"
        )
        return

    try:
        row_num = int(context.args[0])
        hours = int(context.args[1])
    except ValueError:
        await update.message.reply_text("N и HOURS должны быть числами.")
        return

    if row_num <= 0:
        await update.message.reply_text("N должен быть больше 0.")
        return

    if hours <= 0:
        await update.message.reply_text("Количество часов должно быть больше 0.")
        return

    rentals = get_rentals_snapshot()
    if not rentals:
        await update.message.reply_text("Активных аренд нет.")
        return

    if row_num > len(rentals):
        await update.message.reply_text(
            f"❌ Строки {row_num} нет. Сейчас активных аренд: {len(rentals)}."
        )
        return

    rental = rentals[row_num - 1]
    order_id = rental["order_id"]

    try:
        add_seconds = hours * 3600
        extend_rental(order_id, add_seconds)
        add_extension(rental["id"], "admin_manual_row", hours, int(time.time()))
    except Exception as e:
        LOGGER.exception("Failed to extend rental manually by row for order_id=%s: %s", order_id, e)
        await update.message.reply_text(f"❌ Не удалось продлить заказ #{order_id}.")
        return

    updated_rental = get_rental_by_order_id(order_id)
    remaining_text = format_remaining_time(updated_rental)

    buyer_msg_sent = _send_buyer_message_with_fallback(
        updated_rental["chat_id"],
        (
            f"✅ Продавец вручную продлил вашу аренду по заказу #{order_id} на {hours} ч.\n"
            f"⏱ Текущее оставшееся время: {remaining_text}"
        ),
    )

    text = (
        f"✅ Строка {row_num} / заказ #{order_id} продлён на {hours} ч.\n"
        f"Текущее время: {remaining_text}\n"
        f"Сообщение клиенту: {'отправлено' if buyer_msg_sent else 'не отправлено'}"
    )
    await update.message.reply_text(text)


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    period = "day"
    if getattr(context, "args", None):
        period = context.args[0].lower()

    now = int(time.time())

    if period == "day":
        start_ts = now - 24 * 60 * 60
        title = "за сутки"
    elif period == "week":
        start_ts = now - 7 * 24 * 60 * 60
        title = "за неделю"
    elif period == "month":
        start_ts = now - 30 * 24 * 60 * 60
        title = "за месяц"
    elif period == "all":
        start_ts = None
        title = "за всё время"
    else:
        await update.message.reply_text("Используй: /stats day | week | month | all")
        return

    total = get_confirmed_income_total(start_ts)
    by_good = get_confirmed_income_by_good(start_ts)

    lines = [
        f"📊 Статистика {title}",
        f"Подтверждённых заказов: {total['orders_count']}",
        f"Доход: {float(total['total_rub']):.2f} RUB",
        "",
        "По аккаунтам:"
    ]

    if not by_good:
        lines.append("Нет подтверждённых заказов за этот период.")
    else:
        for row in by_good:
            lines.append(
                f"good_id={row['good_id']} | {row['login_snapshot']} | "
                f"{float(row['total_rub']):.2f} RUB | заказов: {row['orders_count']}"
            )

    await update.message.reply_text("\n".join(lines))


# ---------- ADDGOOD WIZARD ----------

async def addgood_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text(
        "Отправьте ссылку на лот FunPay или просто lot_id.\n"
        "Пример: https://funpay.com/lots/offer?id=61816431"
    )
    return ADD_LOT_LINK


async def addgood_lot_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lot_input = update.message.text.strip()
    lot_id = parse_lot_id_from_input(lot_input)
    if not lot_id:
        await update.message.reply_text(
            "Не удалось определить lot_id. Отправьте ссылку вида https://funpay.com/lots/offer?id=123456 или просто число."
        )
        return ADD_LOT_LINK

    try:
        title = fetch_lot_title(lot_id)
    except Exception as e:
        LOGGER.exception("Failed to fetch title for lot_id=%s: %s", lot_id, e)
        await update.message.reply_text(
            "❌ Не удалось получить title лота с FunPay. Проверь ссылку/ID и доступ к аккаунту."
        )
        return ADD_LOT_LINK

    context.user_data["lot_id"] = lot_id
    context.user_data["title"] = title
    await update.message.reply_text(
        f"Лот найден.\nlot_id: {lot_id}\ntitle: {title}\n\nВведите login:"
    )
    return ADD_LOGIN


async def addgood_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["login"] = update.message.text.strip()
    await update.message.reply_text("Введите password:")
    return ADD_PASSWORD


async def addgood_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["password"] = update.message.text.strip()
    await update.message.reply_text("Введите note или /skip:")
    return ADD_NOTE


async def addgood_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["note"] = update.message.text.strip()
    await update.message.reply_text("Введите shared_secret или /skip:")
    return ADD_SHARED_SECRET


async def addgood_note_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["note"] = ""
    await update.message.reply_text("Введите shared_secret или /skip:")
    return ADD_SHARED_SECRET


async def finish_addgood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lot_id = context.user_data["lot_id"]
    title = context.user_data["title"]

    good_id = add_good(
        lot_id=lot_id,
        title=title,
        login=context.user_data["login"],
        password=context.user_data["password"],
        note=context.user_data.get("note", ""),
        shared_secret=context.user_data.get("shared_secret", ""),
    )

    context.user_data.clear()
    await update.message.reply_text(
        f"✅ Товар добавлен.\n"
        f"good_id={good_id}\n"
        f"lot_id={lot_id}\n"
        f"title={title}",
        reply_markup=get_main_keyboard(),
    )
    return ConversationHandler.END


async def addgood_shared_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["shared_secret"] = update.message.text.strip()
    return await finish_addgood(update, context)


async def addgood_shared_secret_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["shared_secret"] = ""
    return await finish_addgood(update, context)


# ---------- EDITGOOD WIZARD ----------

async def editgood_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text("Введите good_id товара для редактирования:")
    return EDIT_GOOD_ID


async def editgood_good_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        good_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("good_id должен быть числом. Введите good_id:")
        return EDIT_GOOD_ID

    good = get_good_by_id(good_id)
    if not good:
        await update.message.reply_text("Товар не найден. Введите корректный good_id:")
        return EDIT_GOOD_ID

    context.user_data["good_id"] = good_id
    context.user_data["good_current"] = dict(good)

    await update.message.reply_text(
        f"Текущий lot_id: {good['lot_id']}\n"
        f"Текущий title в базе: {good['title']}\n\n"
        "Отправьте новую ссылку на лот / новый lot_id или /skip:"
    )
    return EDIT_LOT_LINK


async def editgood_lot_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lot_input = update.message.text.strip()
    lot_id = parse_lot_id_from_input(lot_input)
    if not lot_id:
        await update.message.reply_text(
            "Не удалось определить lot_id. Отправьте ссылку вида https://funpay.com/lots/offer?id=123456 или просто число, либо /skip."
        )
        return EDIT_LOT_LINK

    try:
        title = fetch_lot_title(lot_id)
    except Exception as e:
        LOGGER.exception("Failed to fetch title for lot_id=%s during editgood: %s", lot_id, e)
        await update.message.reply_text(
            "❌ Не удалось получить title лота с FunPay. Проверь ссылку/ID и доступ к аккаунту."
        )
        return EDIT_LOT_LINK

    context.user_data["lot_id"] = lot_id
    context.user_data["title"] = title
    await update.message.reply_text(
        f"Новый lot_id: {lot_id}\n"
        f"Новый title из FunPay: {title}\n\n"
        f"Текущий login: {context.user_data['good_current']['login']}\n"
        "Введите новый login или /skip:"
    )
    return EDIT_LOGIN


async def editgood_lot_link_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["lot_id"] = None
    context.user_data["title"] = None
    await update.message.reply_text(
        f"Текущий login: {context.user_data['good_current']['login']}\n"
        "Введите новый login или /skip:"
    )
    return EDIT_LOGIN


async def editgood_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["login"] = update.message.text.strip()
    await update.message.reply_text("Введите новый password или /skip:")
    return EDIT_PASSWORD


async def editgood_login_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["login"] = None
    await update.message.reply_text("Введите новый password или /skip:")
    return EDIT_PASSWORD


async def editgood_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["password"] = update.message.text.strip()
    current_note = context.user_data["good_current"]["note"]
    await update.message.reply_text(
        f"Текущий note: {current_note if current_note else '(пусто)'}\n"
        "Введите новый note или /skip:"
    )
    return EDIT_NOTE


async def editgood_password_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["password"] = None
    current_note = context.user_data["good_current"]["note"]
    await update.message.reply_text(
        f"Текущий note: {current_note if current_note else '(пусто)'}\n"
        "Введите новый note или /skip:"
    )
    return EDIT_NOTE


async def editgood_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["note"] = update.message.text.strip()
    current_secret = context.user_data["good_current"]["shared_secret"]
    await update.message.reply_text(
        f"Текущий shared_secret: {'задан' if current_secret else 'не задан'}\n"
        "Введите новый shared_secret или /skip:"
    )
    return EDIT_SHARED_SECRET


async def editgood_note_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["note"] = None
    current_secret = context.user_data["good_current"]["shared_secret"]
    await update.message.reply_text(
        f"Текущий shared_secret: {'задан' if current_secret else 'не задан'}\n"
        "Введите новый shared_secret или /skip:"
    )
    return EDIT_SHARED_SECRET


async def finish_editgood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok = update_good(
        good_id=context.user_data["good_id"],
        lot_id=context.user_data.get("lot_id"),
        title=context.user_data.get("title"),
        login=context.user_data.get("login"),
        password=context.user_data.get("password"),
        note=context.user_data.get("note"),
        shared_secret=context.user_data.get("shared_secret"),
    )

    context.user_data.clear()
    if ok:
        await update.message.reply_text("✅ Товар обновлён.", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text("❌ Не удалось обновить товар.", reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def editgood_shared_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["shared_secret"] = update.message.text.strip()
    return await finish_editgood(update, context)


async def editgood_shared_secret_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["shared_secret"] = None
    return await finish_editgood(update, context)


# ---------- SIMPLE COMMANDS ----------

async def disablegood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if not context.args:
        await update.message.reply_text("Формат: /disablegood good_id")
        return

    try:
        good_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("good_id должен быть числом.")
        return

    ok = set_good_active(good_id, 0)
    if ok:
        await update.message.reply_text(f"✅ Товар good_id={good_id} отключён.")
    else:
        await update.message.reply_text(f"❌ Товар good_id={good_id} не найден.")


async def enablegood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if not context.args:
        await update.message.reply_text("Формат: /enablegood good_id")
        return

    try:
        good_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("good_id должен быть числом.")
        return

    ok = set_good_active(good_id, 1)
    if ok:
        await update.message.reply_text(f"✅ Товар good_id={good_id} включён.")
    else:
        await update.message.reply_text(f"❌ Товар good_id={good_id} не найден.")


async def delgood_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if not context.args:
        await update.message.reply_text("Формат: /delgood good_id")
        return

    try:
        good_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("good_id должен быть числом.")
        return

    ok = delete_good(good_id)
    if ok:
        await update.message.reply_text(f"✅ Товар good_id={good_id} удалён.")
    else:
        await update.message.reply_text(
            f"❌ Нельзя удалить good_id={good_id}: товар занят или не найден."
        )


async def updatetitles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if FUNPAY_ACC is None:
        await update.message.reply_text("❌ FunPay аккаунт не инициализирован. Перезапусти бота.")
        return

    goods = list_goods()
    if not goods:
        await update.message.reply_text("Товаров в базе нет.")
        return

    updated_count = 0
    unchanged_count = 0
    failed_count = 0

    lines = ["🔄 Обновление title лотов:"]

    for g in goods:
        good_id = g["id"]
        lot_id = g["lot_id"]
        old_title = (g["title"] or "").strip()

        try:
            new_title = fetch_lot_title(lot_id)
        except Exception as e:
            failed_count += 1
            LOGGER.exception("Failed to refresh title for good_id=%s lot_id=%s: %s", good_id, lot_id, e)
            lines.append(f"❌ good_id={good_id}, lot_id={lot_id}: не удалось получить title.")
            continue

        if new_title == old_title:
            unchanged_count += 1
            lines.append(f"➖ good_id={good_id}, lot_id={lot_id}: без изменений.")
            continue

        ok = update_good(good_id=good_id, title=new_title)
        if not ok:
            failed_count += 1
            lines.append(f"❌ good_id={good_id}, lot_id={lot_id}: не удалось обновить в БД.")
            continue

        updated_count += 1
        lines.append(
            f"✅ good_id={good_id}, lot_id={lot_id}: title обновлён.\n"
            f"Было: {old_title}\n"
            f"Стало: {new_title}"
        )

    lines.append(
        "\nИтог:\n"
        f"Обновлено: {updated_count}\n"
        f"Без изменений: {unchanged_count}\n"
        f"Ошибок: {failed_count}"
    )
    await update.message.reply_text("\n".join(lines))


def main():
    init_db()
    init_funpay_account()

    app = Application.builder().token(TELEGRAM_ADMIN_BOT_TOKEN).build()

    addgood_conv = ConversationHandler(
        entry_points=[
            CommandHandler("addgood", addgood_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_ADD_GOOD)}$"), addgood_button),
        ],
        states={
            ADD_LOT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, addgood_lot_link)],
            ADD_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, addgood_login)],
            ADD_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, addgood_password)],
            ADD_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addgood_note),
                CommandHandler("skip", addgood_note_skip),
            ],
            ADD_SHARED_SECRET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, addgood_shared_secret),
                CommandHandler("skip", addgood_shared_secret_skip),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    editgood_conv = ConversationHandler(
        entry_points=[
            CommandHandler("editgood", editgood_start),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_EDIT_GOOD)}$"), editgood_button),
        ],
        states={
            EDIT_GOOD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_good_id)],
            EDIT_LOT_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_lot_link),
                CommandHandler("skip", editgood_lot_link_skip),
            ],
            EDIT_LOGIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_login),
                CommandHandler("skip", editgood_login_skip),
            ],
            EDIT_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_password),
                CommandHandler("skip", editgood_password_skip),
            ],
            EDIT_NOTE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_note),
                CommandHandler("skip", editgood_note_skip),
            ],
            EDIT_SHARED_SECRET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_shared_secret),
                CommandHandler("skip", editgood_shared_secret_skip),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    closerent_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{re.escape(BTN_CLOSE_RENTAL)}$"), closerent_start),
        ],
        states={
            CLOSE_RENT_ROW: [MessageHandler(filters.TEXT & ~filters.COMMAND, closerent_row_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    autoraise_conv = ConversationHandler(
        entry_points=[
            CommandHandler("autoraise", autoraise_cmd),
            MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE)}$"), autoraise_menu_start),
        ],
        states={
            AUTO_RAISE_MENU: [
                MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE_ENABLE)}$"), autoraise_enable),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE_DISABLE)}$"), autoraise_disable),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE_SET_TIME)}$"), autoraise_set_time_prompt),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE_BACK)}$"), autoraise_back),
            ],
            AUTO_RAISE_INTERVAL_INPUT: [
                MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE_ENABLE)}$"), autoraise_enable),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE_DISABLE)}$"), autoraise_disable),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE_SET_TIME)}$"), autoraise_set_time_prompt),
                MessageHandler(filters.Regex(f"^{re.escape(BTN_AUTO_RAISE_BACK)}$"), autoraise_back),
                MessageHandler(filters.TEXT & ~filters.COMMAND, autoraise_set_time_input),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_cmd)],
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("goods", goods_cmd))
    app.add_handler(CommandHandler("free", free_cmd))
    app.add_handler(CommandHandler("rentals", rentals_cmd))
    app.add_handler(CommandHandler("extendrent", extendrent_cmd))
    app.add_handler(CommandHandler("extendrentrow", extendrentrow_cmd))
    app.add_handler(CommandHandler("closerent", closerent_cmd))
    app.add_handler(CommandHandler("closerentrow", closerentrow_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("updatetitles", updatetitles_cmd))
    app.add_handler(addgood_conv)
    app.add_handler(editgood_conv)
    app.add_handler(closerent_conv)
    app.add_handler(autoraise_conv)
    app.add_handler(CommandHandler("disablegood", disablegood_cmd))
    app.add_handler(CommandHandler("enablegood", enablegood_cmd))
    app.add_handler(CommandHandler("delgood", delgood_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_LIST_GOODS)}$"), goods_cmd))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_ACTIVE_RENTALS)}$"), rentals_cmd))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_FREE_GOODS)}$"), free_button))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_STATS)}$"), stats_button))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BTN_UPDATE_TITLES)}$"), updatetitles_button))

    print("Telegram admin bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
