# admin_bot.py

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from config import TELEGRAM_ADMIN_BOT_TOKEN, TELEGRAM_ADMIN_USER_ID
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
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

ADD_LOT_ID, ADD_TITLE, ADD_LOGIN, ADD_PASSWORD, ADD_NOTE, ADD_SHARED_SECRET = range(6)
EDIT_GOOD_ID, EDIT_LOT_ID, EDIT_TITLE, EDIT_LOGIN, EDIT_PASSWORD, EDIT_NOTE, EDIT_SHARED_SECRET = range(6, 13)


def is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id == TELEGRAM_ADMIN_USER_ID)


async def admin_only(update: Update) -> bool:
    if not is_admin(update):
        if update.message:
            await update.message.reply_text("Нет доступа.")
        return False
    return True


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    text = (
        "Админ-бот запущен.\n\n"
        "Команды:\n"
        "/goods — список товаров\n"
        "/free — число свободных товаров\n"
        "/rentals — активные аренды\n"
        "/addgood — пошаговое добавление товара\n"
        "/editgood — пошаговое редактирование товара\n"
        "/disablegood good_id\n"
        "/enablegood good_id\n"
        "/delgood good_id\n"
        "/cancel — отменить текущий мастер"
    )
    await update.message.reply_text(text)


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END


async def goods_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    goods = list_goods()
    if not goods:
        await update.message.reply_text("Товаров в базе нет.")
        return

    lines = ["📦 Товары в базе:"]
    for g in goods:
        status = "ЗАНЯТ" if g["is_busy"] else ("АКТИВЕН" if g["is_active"] else "ОТКЛЮЧЕН")
        has_secret = "yes" if g["shared_secret"] else "no"
        lines.append(
            f"{g['id']}. [{status}] lot_id={g['lot_id']} | {g['title']} | "
            f"login={g['login']} | shared_secret={has_secret}"
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

    rentals = list_active_rentals()
    if not rentals:
        await update.message.reply_text("Активных аренд нет.")
        return

    lines = ["🧾 Активные аренды:"]
    for r in rentals:
        lines.append(
            f"Заказ #{r['order_id']} | buyer_id={r['buyer_id']} | "
            f"good_id={r['good_id']} | lot_id={r['good_lot_id']} | {r['title']}"
        )

    await update.message.reply_text("\n".join(lines))


# ---------- ADDGOOD WIZARD ----------

async def addgood_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END

    context.user_data.clear()
    await update.message.reply_text("Введите lot_id:")
    return ADD_LOT_ID


async def addgood_lot_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lot_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("lot_id должен быть числом. Введите lot_id:")
        return ADD_LOT_ID

    context.user_data["lot_id"] = lot_id
    await update.message.reply_text("Введите title:")
    return ADD_TITLE


async def addgood_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text("Введите login:")
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


async def addgood_shared_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["shared_secret"] = update.message.text.strip()

    good_id = add_good(
        lot_id=context.user_data["lot_id"],
        title=context.user_data["title"],
        login=context.user_data["login"],
        password=context.user_data["password"],
        note=context.user_data.get("note", ""),
        shared_secret=context.user_data.get("shared_secret", ""),
    )

    context.user_data.clear()
    await update.message.reply_text(f"✅ Товар добавлен. good_id={good_id}")
    return ConversationHandler.END


async def addgood_shared_secret_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["shared_secret"] = ""

    good_id = add_good(
        lot_id=context.user_data["lot_id"],
        title=context.user_data["title"],
        login=context.user_data["login"],
        password=context.user_data["password"],
        note=context.user_data.get("note", ""),
        shared_secret=context.user_data.get("shared_secret", ""),
    )

    context.user_data.clear()
    await update.message.reply_text(f"✅ Товар добавлен. good_id={good_id}")
    return ConversationHandler.END


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
        "Введите новый lot_id или /skip:"
    )
    return EDIT_LOT_ID


async def editgood_lot_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["lot_id"] = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("lot_id должен быть числом или /skip.")
        return EDIT_LOT_ID

    await update.message.reply_text(
        f"Текущий title: {context.user_data['good_current']['title']}\n"
        "Введите новый title или /skip:"
    )
    return EDIT_TITLE


async def editgood_lot_id_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["lot_id"] = None
    await update.message.reply_text(
        f"Текущий title: {context.user_data['good_current']['title']}\n"
        "Введите новый title или /skip:"
    )
    return EDIT_TITLE


async def editgood_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text(
        f"Текущий login: {context.user_data['good_current']['login']}\n"
        "Введите новый login или /skip:"
    )
    return EDIT_LOGIN


async def editgood_title_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
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


async def editgood_shared_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["shared_secret"] = update.message.text.strip()

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
        await update.message.reply_text("✅ Товар обновлён.")
    else:
        await update.message.reply_text("❌ Не удалось обновить товар.")
    return ConversationHandler.END


async def editgood_shared_secret_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["shared_secret"] = None

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
        await update.message.reply_text("✅ Товар обновлён.")
    else:
        await update.message.reply_text("❌ Не удалось обновить товар.")
    return ConversationHandler.END


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


def main():
    init_db()

    app = Application.builder().token(TELEGRAM_ADMIN_BOT_TOKEN).build()

    addgood_conv = ConversationHandler(
        entry_points=[CommandHandler("addgood", addgood_start)],
        states={
            ADD_LOT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, addgood_lot_id)],
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addgood_title)],
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
        entry_points=[CommandHandler("editgood", editgood_start)],
        states={
            EDIT_GOOD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_good_id)],
            EDIT_LOT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_lot_id),
                CommandHandler("skip", editgood_lot_id_skip),
            ],
            EDIT_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editgood_title),
                CommandHandler("skip", editgood_title_skip),
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

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("goods", goods_cmd))
    app.add_handler(CommandHandler("free", free_cmd))
    app.add_handler(CommandHandler("rentals", rentals_cmd))
    app.add_handler(addgood_conv)
    app.add_handler(editgood_conv)
    app.add_handler(CommandHandler("disablegood", disablegood_cmd))
    app.add_handler(CommandHandler("enablegood", enablegood_cmd))
    app.add_handler(CommandHandler("delgood", delgood_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    print("Telegram admin bot started")
    app.run_polling()


if __name__ == "__main__":
    main()