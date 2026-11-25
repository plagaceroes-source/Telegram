"""Кнопочное меню бота: все админ-команды доступны кнопками, печатать ничего не нужно.

Как это устроено:
- /menu (или /start, или кнопка «☰ Меню» внизу в личке) показывает главное меню с разделами;
- кнопка-команда без параметров выполняется сразу;
- команде с параметрами бот предлагает выбрать объект кнопкой (источник, канал, группа, ссылка)
  и/или просит прислать недостающий текст ответом на своё сообщение;
- выполняется всегда настоящая команда из news_agent/bot/commands.py: кнопка собирает
  «/команда аргументы» и прогоняет его через диспетчер как обычное сообщение, поэтому логика
  и права проверяются там же, а текстовые команды продолжают работать как раньше.
Ещё список команд зашит в синюю кнопку «Меню» рядом с полем ввода (setup_bot_menu)."""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    Message,
    ReplyKeyboardMarkup,
    Update,
)
from sqlalchemy import select

from news_agent.config import settings
from news_agent.db.models import GatedGroup, InviteLink, Source, TargetChannel
from news_agent.db.session import session_scope
from news_agent.reports.period import REPORT_TZ, previous_month

logger = logging.getLogger(__name__)

router = Router()

MENU_BUTTON_TEXT = "☰ Меню"
MAX_PICK_BUTTONS = 40

# Команды в синей кнопке «Меню» у поля ввода: (имя, описание ≤256 символов).
BOT_COMMANDS: list[tuple[str, str]] = [
    ("menu", "Кнопочное меню со всеми командами"),
    ("help", "Справка по командам"),
    ("list_sources", "Список источников"),
    ("add_source", "Добавить источник"),
    ("pause_source", "Поставить источник на паузу"),
    ("resume_source", "Вернуть источник в работу"),
    ("list_targets", "Список целевых каналов"),
    ("add_target", "Добавить целевой канал"),
    ("rename_target", "Задать название канала"),
    ("list_accounts", "Аккаунты-слушатели"),
    ("create_invite_link", "Создать именную инвайт-ссылку"),
    ("revoke_invite_link", "Деактивировать инвайт-ссылку"),
    ("list_gated_groups", "Группы с обязательной подпиской"),
    ("add_gated_group", "Включить обязательную подписку в группе"),
    ("rename_gated_group", "Переименовать группу"),
    ("pause_gated_group", "Выключить проверку подписки в группе"),
    ("resume_gated_group", "Включить проверку подписки в группе"),
    ("report", "Выслать отчёт за день или месяц"),
    ("report_week", "Недельный отчёт (пн–вс) с динамикой по дням"),
    ("report_mtd", "С начала месяца в сравнении с прошлым месяцем"),
    ("chatid", "Узнать chat_id текущего чата"),
]

# Разделы: ключ -> (заголовок, [(текст кнопки, callback_data)]).
# callback: m:run:<cmd> — выполнить сразу; m:pick:<cmd> — выбрать объект из списка;
# m:ask:<cmd> — попросить ввести аргументы; m:rep:<вариант> — отчёт.
SECTIONS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "src": (
        "📰 Источники",
        [
            ("📋 Список", "m:run:list_sources"),
            ("➕ Добавить", "m:ask:add_source"),
            ("⏸ Пауза", "m:pick:pause_source"),
            ("▶️ Возобновить", "m:pick:resume_source"),
        ],
    ),
    "tgt": (
        "📣 Целевые каналы",
        [
            ("📋 Список", "m:run:list_targets"),
            ("➕ Добавить", "m:ask:add_target"),
            ("✏️ Задать название", "m:pick:rename_target"),
        ],
    ),
    "grp": (
        "🛡 Группы с обязательной подпиской",
        [
            ("📋 Список", "m:run:list_gated_groups"),
            ("➕ Добавить", "m:ask:add_gated_group"),
            ("✏️ Переименовать", "m:pick:rename_gated_group"),
            ("⏸ Выключить проверку", "m:pick:pause_gated_group"),
            ("▶️ Включить проверку", "m:pick:resume_gated_group"),
        ],
    ),
    "lnk": (
        "🔗 Инвайт-ссылки",
        [
            ("➕ Создать ссылку", "m:pick:create_invite_link"),
            ("🚫 Отозвать ссылку", "m:pick:revoke_invite_link"),
        ],
    ),
    "rep": (
        "📊 Отчёты",
        [
            ("📅 С начала месяца (динамика)", "m:run:report_mtd"),
            ("📈 Прошлая неделя (пн–вс)", "m:run:report_week"),
            ("За вчера", "m:rep:yesterday"),
            ("За прошлый месяц", "m:rep:lastmonth"),
            ("Другой день / месяц", "m:ask:report"),
            ("Другая неделя", "m:ask:report_week"),
        ],
    ),
    "misc": (
        "⚙️ Прочее",
        [
            ("👤 Аккаунты-слушатели", "m:run:list_accounts"),
            ("🆔 chat_id этого чата", "m:run:chatid"),
            ("❓ Справка", "m:run:help"),
        ],
    ),
}

MAIN_ORDER = ["src", "tgt", "grp", "lnk", "rep", "misc"]

# Что просить у пользователя: команда -> (подсказка, плейсхолдер поля ввода).
ASK: dict[str, tuple[str, str]] = {
    "add_source": (
        "Пришлите ответом: <username> <язык или auto> <rewrite или as_is> <id аккаунта>\n"
        "Пример: alicante_olx ru rewrite 1",
        "username lang mode account_id",
    ),
    "add_target": (
        "Пришлите ответом: <username> <network_tag> <язык>\nПример: budvkurse_cb costa_blanca ru",
        "username network_tag lang",
    ),
    "add_gated_group": (
        "Пришлите ответом: <chat_id группы> <id канала> <название>\n"
        "Пример: -1001822683439 1 Барахолка Аликанте",
        "chat_id target_id название",
    ),
    "report": (
        "Пришлите дату отчёта: день ГГГГ-ММ-ДД (например, 2026-11-13) или месяц ГГГГ-ММ (2026-11).",
        "ГГГГ-ММ-ДД или ГГГГ-ММ",
    ),
    "report_week": (
        "Пришлите любую дату нужной недели, например 2026-11-16 — отчёт придёт за неделю пн–вс, в которую она входит.",
        "ГГГГ-ММ-ДД",
    ),
    # Команды, где объект выбирается кнопкой, а затем нужен только текст:
    "rename_target": ("Пришлите ответом новое название канала.", "название"),
    "rename_gated_group": ("Пришлите ответом новое название группы.", "название"),
    "create_invite_link": (
        "Пришлите ответом: <код до 32 символов> <описание источника>\n"
        "Пример: newsA_ig_story0912 News A / Instagram Stories / пост от 12.09",
        "код описание",
    ),
}


async def _load_choices(cmd: str) -> tuple[str, list[tuple[str, str]]]:
    """Варианты выбора для команды: (заголовок, [(текст кнопки, id)])."""
    async with session_scope() as session:
        if cmd in ("pause_source", "resume_source"):
            want_active = cmd == "pause_source"
            rows = (await session.execute(select(Source).order_by(Source.id))).scalars()
            items = [(f"#{s.id} @{s.username}", str(s.id)) for s in rows if bool(s.active) == want_active]
            return ("Какой источник поставить на паузу?" if want_active else "Какой источник вернуть в работу?"), items
        if cmd in ("rename_target", "create_invite_link"):
            rows = (await session.execute(select(TargetChannel).order_by(TargetChannel.id))).scalars()
            items = [(f"#{t.id} {t.title or '@' + t.username}", str(t.id)) for t in rows]
            return "Выберите канал:", items
        if cmd in ("pause_gated_group", "resume_gated_group", "rename_gated_group"):
            rows = (await session.execute(select(GatedGroup).order_by(GatedGroup.id))).scalars()
            groups = list(rows)
            if cmd == "pause_gated_group":
                groups = [g for g in groups if g.active]
            elif cmd == "resume_gated_group":
                groups = [g for g in groups if not g.active]
            return "Выберите группу:", [(f"#{g.id} {g.title}", str(g.id)) for g in groups]
        if cmd == "revoke_invite_link":
            rows = (
                await session.execute(select(InviteLink).where(InviteLink.revoked.is_(False)).order_by(InviteLink.id.desc()))
            ).scalars()
            return "Какую ссылку отозвать?", [(f"#{link.id} {link.name}", str(link.id)) for link in rows]
    return "", []


# После выбора объекта: сразу выполнить (do) или ещё спросить текст (ask).
PICK_THEN_ASK = {"rename_target", "rename_gated_group", "create_invite_link"}

# Ожидаем ответ пользователя на ForceReply: user_id -> (команда, префикс аргументов, id сообщения-вопроса).
_pending: dict[int, tuple[str, str, int]] = {}


def _allowed(user_id: int | None) -> bool:
    return user_id is not None and (not settings.approver_chat_ids or user_id in settings.approver_chat_ids)


def _message_allowed(message: Message) -> bool:
    return bool(message.from_user) and _allowed(message.from_user.id)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def main_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    keys = MAIN_ORDER
    for i in range(0, len(keys), 2):
        rows.append([_btn(SECTIONS[k][0], f"m:sec:{k}") for k in keys[i : i + 2]])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def section_keyboard(key: str) -> InlineKeyboardMarkup:
    _, buttons = SECTIONS[key]
    rows = [[_btn(text, data)] for text, data in buttons]
    rows.append([_btn("⬅️ В главное меню", "m:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _section_of(cmd: str) -> str:
    for key, (_, buttons) in SECTIONS.items():
        if any(data.endswith(f":{cmd}") for _, data in buttons):
            return key
    return "misc"


async def _run_command(callback: CallbackQuery, dp: Dispatcher, text: str) -> None:
    """Прогоняет «/команду аргументы» через диспетчер так, будто её прислал нажавший кнопку."""
    menu_message = callback.message
    if not isinstance(menu_message, Message):
        await callback.answer("Сообщение с меню устарело — откройте /menu заново.", show_alert=True)
        return
    fake = menu_message.model_copy(update={"from_user": callback.from_user, "text": text, "entities": None})
    fake = fake.as_(callback.bot)
    await dp.feed_update(callback.bot, Update(update_id=0, message=fake))


@router.message(Command("menu", "start"), _message_allowed)
async def cmd_menu(message: Message) -> None:
    if message.chat.type == "private":
        # Постоянная кнопка внизу экрана: открывает меню одним нажатием.
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=MENU_BUTTON_TEXT)]], resize_keyboard=True, is_persistent=True
        )
        await message.answer("Кнопка «☰ Меню» теперь всегда под рукой внизу.", reply_markup=keyboard)
    await message.answer("Что сделать?", reply_markup=main_keyboard())


@router.message(F.chat.type == "private", F.text == MENU_BUTTON_TEXT, _message_allowed)
async def on_menu_button(message: Message) -> None:
    await message.answer("Что сделать?", reply_markup=main_keyboard())


@router.callback_query(F.data.startswith("m:"))
async def on_menu_callback(callback: CallbackQuery, dispatcher: Dispatcher) -> None:
    if not _allowed(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    parts = callback.data.split(":")
    action = parts[1]
    message = callback.message if isinstance(callback.message, Message) else None

    if action == "home":
        if message:
            await message.edit_text("Что сделать?", reply_markup=main_keyboard())
        await callback.answer()

    elif action == "sec" and parts[2] in SECTIONS:
        if message:
            await message.edit_text(SECTIONS[parts[2]][0], reply_markup=section_keyboard(parts[2]))
        await callback.answer()

    elif action == "run":
        await callback.answer()
        await _run_command(callback, dispatcher, f"/{parts[2]}")

    elif action == "rep":
        await callback.answer()
        if parts[2] == "lastmonth":
            today = dt.datetime.now(REPORT_TZ).date()
            year, month = previous_month(today.year, today.month)
            await _run_command(callback, dispatcher, f"/report {year:04d}-{month:02d}")
        else:
            await _run_command(callback, dispatcher, "/report")

    elif action == "pick":
        cmd = parts[2]
        title, items = await _load_choices(cmd)
        back = _btn("⬅️ Назад", f"m:sec:{_section_of(cmd)}")
        if not items:
            if message:
                await message.edit_text("Подходящих вариантов пока нет.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back]]))
            await callback.answer()
            return
        verb = "pa" if cmd in PICK_THEN_ASK else "do"
        rows = [[_btn(label, f"m:{verb}:{cmd}:{ident}")] for label, ident in items[:MAX_PICK_BUTTONS]]
        if len(items) > MAX_PICK_BUTTONS:
            title += f" (показаны первые {MAX_PICK_BUTTONS} из {len(items)})"
        rows.append([back])
        if message:
            await message.edit_text(title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
        await callback.answer()

    elif action == "do":
        await callback.answer()
        await _run_command(callback, dispatcher, f"/{parts[2]} {parts[3]}")

    elif action in ("ask", "pa"):
        cmd = parts[2]
        prefix = parts[3] if action == "pa" else ""
        if cmd not in ASK:
            await callback.answer("Неизвестная команда", show_alert=True)
            return
        prompt, placeholder = ASK[cmd]
        await callback.answer()
        if message is None:
            return
        sent = await message.answer(prompt, reply_markup=ForceReply(input_field_placeholder=placeholder[:64]))
        _pending[callback.from_user.id] = (cmd, prefix, sent.message_id)

    else:
        await callback.answer("Неизвестная кнопка", show_alert=True)


def _has_pending_reply(message: Message) -> bool:
    if not message.from_user or message.from_user.id not in _pending or not message.text:
        return False
    if message.text.startswith("/"):
        return False
    _, _, prompt_id = _pending[message.from_user.id]
    replied = message.reply_to_message
    if replied is not None:
        return replied.message_id == prompt_id
    return message.chat.type == "private"


@router.message(F.text, _has_pending_reply, _message_allowed)
async def on_pending_reply(message: Message, dispatcher: Dispatcher) -> None:
    cmd, prefix, _ = _pending.pop(message.from_user.id)
    args = " ".join(part for part in (prefix, message.text.strip()) if part)
    fake = message.model_copy(update={"text": f"/{cmd} {args}", "entities": None, "reply_to_message": None})
    fake = fake.as_(message.bot)
    await dispatcher.feed_update(message.bot, Update(update_id=0, message=fake))


async def setup_bot_menu(bot: Bot) -> None:
    """Список команд в синей кнопке «Меню» у поля ввода. Ошибка не должна ронять бота."""
    try:
        await bot.set_my_commands([BotCommand(command=name, description=desc) for name, desc in BOT_COMMANDS])
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    except Exception:
        logger.exception("Не удалось настроить меню команд бота")
