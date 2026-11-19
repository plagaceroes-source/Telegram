"""Telegram-бот: модерация заявок в группе + пост в канал."""
import html
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from pathlib import Path

from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from . import config, service

log = logging.getLogger("bot")
bot: Bot | None = None
dp = Dispatcher()

METHODS = {"bizum": "Bizum", "crypto": "Крипта", "mono": "Монобанк"}


def _e(s):
    return html.escape(str(s or "—"))


def card(o, note=""):
    price = f"\nЦена слота: <b>{o['price']:g} €</b>" if o.get("price") else f"\nЦена сейчас: <b>{service.next_price():g} €</b>"
    return (
        f"🆕 <b>Заявка #{o['id']}</b>\n"
        f"Слово: <code>{_e(o['word'])}</code>\n"
        f"Ссылка: {_e(o['url'])}\n"
        f"Подпись: {_e(o['business']) if o['business'] or o['url'] else 'аноним (только слово)'}\n"
        f"Контакт (видит только модерация): {_e(o['contact'])}"
        f"{price}\n"
        f"Заказ: {config.SITE_URL}/o/{o['token']}"
        + (f"\n\n{note}" if note else "")
    )


def kb(*rows):
    rows = [r if isinstance(r, list) else [r] for r in rows]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in r] for r in rows])


async def start():
    """Запуск long polling. Без токена бот отключён (сайт работает, модерации нет)."""
    global bot
    if not config.BOT_TOKEN:
        log.warning("BOT_TOKEN не задан — бот отключён")
        return
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot, handle_signals=False)


async def stop():
    if bot:
        await bot.session.close()


async def _send(text, markup=None, chat=None):
    if not bot:
        return None
    try:
        return await bot.send_message(chat or config.MOD_CHAT_ID, text, reply_markup=markup,
                                      disable_web_page_preview=True)
    except Exception:
        log.exception("send failed")


async def notify_new(o):
    rows = [[("✅ Одобрить", f"ok:{o['id']}"), ("❌ Отклонить", f"no:{o['id']}")]]
    path = Path(config.LOGO_DIR) / o["logo"] if o.get("logo") else None
    if bot and path and path.exists():
        rows.append([("🖼 Убрать лого (слово оставить)", f"nologo:{o['id']}")])
        try:
            await bot.send_photo(config.MOD_CHAT_ID, FSInputFile(path), caption=card(o, "🖼 С логотипом, проверь картинку"),
                                 reply_markup=kb(*rows))
            return
        except Exception:
            log.exception("send_photo failed")
    await _send(card(o), kb(*rows))


async def notify_claimed(o):
    await _send(
        f"💸 <b>Клиент нажал «Я оплатил»</b> — заявка #{o['id']}\n"
        f"Способ: <b>{METHODS.get(o['method'], o['method'])}</b>, сумма <b>{o['price']:g} €</b>\n"
        f"Слово: <code>{_e(o['word'])}</code> · {_e(o['contact'])}\n"
        f"Проверь поступление и подтверди.",
        kb([("💰 Оплата пришла", f"paid:{o['id']}"), ("🚫 Не пришла", f"nopay:{o['id']}")]))


async def notify_expired(o):
    await _send(f"⌛ Заявка #{o['id']} ({_e(o['word'])}): время на оплату вышло, очередь сброшена.")


async def announce(o):
    """Пост в канал при смене слова."""
    if not (bot and config.CHANNEL_ID):
        return
    nxt = service.next_price()
    text = (
        f"🔄 На сайте новое слово: <b>{_e(o['word'])}</b>\n"
        + (f"Купил: {_e(o['business'])}\n" if o.get("business") else "")
        + f"Заплатил: {o['price']:g} €. Чтобы заменить, надо {nxt:g} €.\n"
        f"{config.SITE_URL}"
    )
    try:
        await bot.send_message(config.CHANNEL_ID, text, disable_web_page_preview=True)
    except Exception:
        log.exception("channel post failed")


@dp.message(Command("id"))
async def on_id(m: Message):
    """Подсказка при настройке: id чата для MOD_CHAT_ID / CHANNEL_ID."""
    await m.reply(f"id этого чата: <code>{m.chat.id}</code>")


@dp.message(Command("reset"))
async def on_reset(m: Message):
    """Очистка перед запуском: только в группе модерации и с подтверждением."""
    if m.chat.id != config.MOD_CHAT_ID:
        return
    st = service.state()
    await m.reply(
        "⚠️ <b>Стереть всё?</b> Будут удалены все слова, история, заявки и логотипы. "
        f"Сейчас в истории слов: {st['total_words']}. Цена вернётся к {config.START_PRICE:g} €. Отменить нельзя.",
        reply_markup=kb([("🗑 Да, стереть всё", "reset:yes"), ("Отмена", "reset:no")]))


@dp.callback_query(F.data.startswith("reset:"))
async def on_reset_confirm(cb: CallbackQuery):
    if not _allowed(cb):
        return await cb.answer("Не здесь", show_alert=True)
    if cb.data == "reset:yes":
        w, o = service.reset_all()
        await _done(cb, f"🗑 Готово: удалено слов {w}, заявок {o}. Сайт чистый, цена {config.START_PRICE:g} €. ({_e(cb.from_user.full_name)})")
    else:
        await _done(cb, "Отменено, ничего не удалено.")


def _allowed(cb: CallbackQuery) -> bool:
    return bool(cb.message) and cb.message.chat.id == config.MOD_CHAT_ID


async def _done(cb: CallbackQuery, text):
    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=text, reply_markup=None)
        else:
            await cb.message.edit_text(text, reply_markup=None, disable_web_page_preview=True)
    except Exception:
        pass
    await cb.answer()


@dp.callback_query(F.data.startswith("nologo:"))
async def on_nologo(cb: CallbackQuery):
    if not _allowed(cb):
        return await cb.answer("Не здесь", show_alert=True)
    oid = int(cb.data[7:])
    try:
        o = service.drop_logo(oid)
    except service.OrderError as e:
        return await cb.answer(str(e), show_alert=True)
    keyboard = kb([("✅ Одобрить", f"ok:{oid}"), ("❌ Отклонить", f"no:{oid}")])
    note = f"🖼 Логотип убран ({_e(cb.from_user.full_name)}): в круге будет первая буква слова."
    try:
        if cb.message.photo:
            await cb.message.edit_caption(caption=card(o, note), reply_markup=keyboard)
        else:
            await cb.message.edit_text(card(o, note), reply_markup=keyboard, disable_web_page_preview=True)
    except Exception:
        pass
    await cb.answer("Лого убрано")


@dp.callback_query(F.data.startswith("ok:"))
async def on_ok(cb: CallbackQuery):
    if not _allowed(cb):
        return await cb.answer("Не здесь", show_alert=True)
    try:
        o = service.approve(int(cb.data[3:]))
    except service.OrderError as e:
        return await cb.answer(str(e), show_alert=True)
    mins = config.PAY_WINDOW // 60
    await _done(cb, card(o, f"✅ Одобрено ({_e(cb.from_user.full_name)}). У клиента {mins} мин на оплату."))


@dp.callback_query(F.data.startswith("no:"))
async def on_no(cb: CallbackQuery):
    if not _allowed(cb):
        return await cb.answer("Не здесь", show_alert=True)
    try:
        o = service.reject(int(cb.data[3:]))
    except service.OrderError as e:
        return await cb.answer(str(e), show_alert=True)
    await _done(cb, card(o, f"❌ Отклонено ({_e(cb.from_user.full_name)})"))


@dp.callback_query(F.data.startswith("paid:"))
async def on_paid(cb: CallbackQuery):
    if not _allowed(cb):
        return await cb.answer("Не здесь", show_alert=True)
    try:
        o = service.confirm_payment(int(cb.data[5:]))
    except service.OrderError as e:
        return await cb.answer(str(e), show_alert=True)
    await _done(cb, card(o, f"💰 Оплата подтверждена ({_e(cb.from_user.full_name)}). Слово на сайте!"))
    await announce(o)


@dp.callback_query(F.data.startswith("nopay:"))
async def on_nopay(cb: CallbackQuery):
    if not _allowed(cb):
        return await cb.answer("Не здесь", show_alert=True)
    try:
        o = service.deny_payment(int(cb.data[6:]))
    except service.OrderError as e:
        return await cb.answer(str(e), show_alert=True)
    await _done(cb, card(o, "🚫 Оплата не пришла, слот освобождён."))
