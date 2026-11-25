"""Текстовые команды администрирования через бота (упрощённая админка, ТЗ этап 2).

Полноценный CRUD живёт в веб-админке (news_agent admin panel), но управлять
источниками/каналами прямо из Telegram удобно на старте и как запасной канал.
"""
from __future__ import annotations

import datetime as dt
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from news_agent.config import settings
from news_agent.db.models import GatedGroup, InviteLink, Source, TargetChannel, UserbotAccount
from news_agent.db.session import session_scope
from news_agent.reports.daily import send_daily_report
from news_agent.reports.month_to_date import send_month_to_date_report
from news_agent.reports.monthly import send_monthly_report
from news_agent.reports.period import REPORT_TZ, last_full_week_monday, week_monday
from news_agent.reports.weekly import send_weekly_report

logger = logging.getLogger(__name__)

router = Router()


def _is_approver(message: Message) -> bool:
    return bool(message.from_user) and (
        not settings.approver_chat_ids or message.from_user.id in settings.approver_chat_ids
    )


router.message.filter(F.text.startswith("/"), _is_approver)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.reply(
        "Все команды доступны кнопками: /menu\n\n"
        "Доступные команды:\n"
        "/add_source <username> <lang|auto> <rewrite|as_is> <account_id> — добавить источник\n"
        "/list_sources — список источников\n"
        "/pause_source <id> / /resume_source <id>\n"
        "/add_target <username> <network_tag> <lang> — добавить целевой канал\n"
        "/list_targets — список целевых каналов\n"
        "/rename_target <id> <название> — задать название канала (для текста force-sub)\n"
        "/list_accounts — список аккаунтов-слушателей\n"
        "/create_invite_link <target_id> <name> <source_label...> — создать именную инвайт-ссылку\n"
        "/revoke_invite_link <id> — деактивировать старую ссылку\n"
        "/add_gated_group <group_chat_id> <target_id> <title...> — включить force-sub в группе\n"
        "/list_gated_groups — список групп с force-sub\n"
        "/pause_gated_group <id> / /resume_gated_group <id>\n"
        "/report [ГГГГ-ММ-ДД | ГГГГ-ММ] — выслать отчёт за день (по умолчанию вчера) или за месяц\n"
        "/report_week [ГГГГ-ММ-ДД] — недельный отчёт (пн–вс) с динамикой по дням; без даты — за прошлую неделю\n"
        "/report_mtd — с 1-го числа по сегодня в сравнении с прошлым месяцем к тому же моменту\n"
        "/rename_gated_group <id> <название> — переименовать группу\n"
        "/chatid — узнать chat_id текущего чата (например, для REPORTS_CHAT_ID)\n"
    )


@router.message(Command("chatid"))
async def cmd_chatid(message: Message) -> None:
    # Без parse_mode: слово "chat_id" само по себе содержит "_", а Telegram
    # в старом Markdown-режиме трактует одиночное подчёркивание как начало
    # курсива и падает с "can't find end of the entity", если не находит пару.
    await message.reply(f"chat_id этого чата: {message.chat.id}")


@router.message(Command("add_source"))
async def cmd_add_source(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split(maxsplit=3)
    if len(args) < 4:
        await message.reply("Использование: /add_source <username> <lang|auto> <rewrite|as_is> <account_id>")
        return
    username, lang, mode, account_id_str = args
    username = username.lstrip("@")
    if mode not in ("rewrite", "as_is"):
        await message.reply("Режим должен быть rewrite или as_is")
        return
    try:
        account_id = int(account_id_str)
    except ValueError:
        await message.reply("account_id должен быть числом")
        return

    async with session_scope() as session:
        account = await session.get(UserbotAccount, account_id)
        if account is None:
            await message.reply(f"Аккаунт с id={account_id} не найден")
            return
        source = Source(
            username=username,
            lang=lang,
            mode=mode,
            userbot_account_id=account_id,
            added_by=str(message.from_user.id),
        )
        session.add(source)
        await session.flush()
        source_id = source.id

    await message.reply(f"Источник @{username} добавлен (id={source_id}), закреплён за аккаунтом {account_id}.")


@router.message(Command("list_sources"))
async def cmd_list_sources(message: Message) -> None:
    async with session_scope() as session:
        result = await session.execute(select(Source).order_by(Source.id))
        sources = list(result.scalars())
    if not sources:
        await message.reply("Источников пока нет.")
        return
    lines = [
        f"#{s.id} @{s.username} — lang={s.lang} mode={s.mode} "
        f"active={'да' if s.active else 'нет'} account={s.userbot_account_id} joined={'да' if s.joined else 'нет'}"
        for s in sources
    ]
    await message.reply("\n".join(lines))


@router.message(Command("pause_source"))
async def cmd_pause_source(message: Message, command: CommandObject) -> None:
    await _set_source_active(message, command, active=False)


@router.message(Command("resume_source"))
async def cmd_resume_source(message: Message, command: CommandObject) -> None:
    await _set_source_active(message, command, active=True)


async def _set_source_active(message: Message, command: CommandObject, active: bool) -> None:
    try:
        source_id = int((command.args or "").strip())
    except ValueError:
        await message.reply("Использование: /pause_source <id>")
        return
    async with session_scope() as session:
        source = await session.get(Source, source_id)
        if source is None:
            await message.reply("Источник не найден")
            return
        source.active = active
    await message.reply(f"Источник #{source_id} теперь {'активен' if active else 'на паузе'}.")


@router.message(Command("add_target"))
async def cmd_add_target(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split(maxsplit=2)
    if len(args) < 3:
        await message.reply("Использование: /add_target <username> <network_tag> <lang>")
        return
    username, network_tag, lang = args
    username = username.lstrip("@")
    async with session_scope() as session:
        target = TargetChannel(username=username, network_tag=network_tag, lang=lang)
        session.add(target)
        await session.flush()
        target_id = target.id
    await message.reply(
        f"Целевой канал @{username} добавлен (id={target_id}). "
        "Не забудьте добавить publisher-бота админом в этот канал."
    )


@router.message(Command("list_targets"))
async def cmd_list_targets(message: Message) -> None:
    async with session_scope() as session:
        result = await session.execute(select(TargetChannel).order_by(TargetChannel.id))
        targets = list(result.scalars())
    if not targets:
        await message.reply("Целевых каналов пока нет.")
        return
    lines = [
        f"#{t.id} @{t.username} «{t.title}» — network={t.network_tag} lang={t.lang} "
        f"active={'да' if t.active else 'нет'}"
        for t in targets
    ]
    await message.reply("\n".join(lines))


@router.message(Command("rename_target"))
async def cmd_rename_target(message: Message, command: CommandObject) -> None:
    """Задаёт человекочитаемое название канала — используется, например, в тексте
    предупреждения force-sub вместо @username."""
    args = (command.args or "").split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Использование: /rename_target <id> <название>")
        return
    target_id_str, title = args
    try:
        target_id = int(target_id_str)
    except ValueError:
        await message.reply("id должен быть числом")
        return
    async with session_scope() as session:
        target = await session.get(TargetChannel, target_id)
        if target is None:
            await message.reply("Целевой канал не найден")
            return
        target.title = title
    await message.reply(f"Название канала #{target_id} обновлено на «{title}».")


@router.message(Command("list_accounts"))
async def cmd_list_accounts(message: Message) -> None:
    async with session_scope() as session:
        result = await session.execute(select(UserbotAccount).options(selectinload(UserbotAccount.sources)).order_by(UserbotAccount.id))
        accounts = list(result.scalars())
    if not accounts:
        await message.reply("Аккаунтов-слушателей пока нет. Добавьте через scripts/add_userbot_account.py")
        return
    lines = [
        f"#{a.id} {a.label} ({a.phone_masked}) — status={a.status} sources={a.sources_count}/"
        f"{settings.max_sources_per_account}"
        for a in accounts
    ]
    await message.reply("\n".join(lines))


@router.message(Command("create_invite_link"))
async def cmd_create_invite_link(message: Message, command: CommandObject) -> None:
    """Создаёт именную инвайт-ссылку программно через бота (ТЗ 3.4.1/3.4.2)."""
    args = (command.args or "").split(maxsplit=2)
    if len(args) < 3:
        await message.reply(
            "Использование: /create_invite_link <target_id> <name_до_32_символов> <source_label>\n"
            "Пример: /create_invite_link 1 newsA_ig_story0912 News A / Instagram Stories / пост от 12.09"
        )
        return
    target_id_str, name, source_label = args
    if len(name) > 32:
        await message.reply("Код ссылки (name) не должен превышать 32 символа — ограничение Telegram API.")
        return
    try:
        target_id = int(target_id_str)
    except ValueError:
        await message.reply("target_id должен быть числом")
        return

    async with session_scope() as session:
        target = await session.get(TargetChannel, target_id)
        if target is None:
            await message.reply("Целевой канал не найден")
            return
        chat_id = target.tg_chat_id or f"@{target.username}"

    try:
        tg_link = await message.bot.create_chat_invite_link(chat_id=chat_id, name=name)
    except Exception:
        logger.exception("Не удалось создать инвайт-ссылку для канала %s", target_id)
        await message.reply("Не удалось создать ссылку — убедитесь, что бот админ этого канала.")
        return

    async with session_scope() as session:
        link = InviteLink(
            target_channel_id=target_id,
            name=name,
            tg_invite_link=tg_link.invite_link,
            source_label=source_label,
            created_by=str(message.from_user.id),
        )
        session.add(link)

    await message.reply(f"Ссылка создана: {tg_link.invite_link}\nОписание: {source_label}")


@router.message(Command("revoke_invite_link"))
async def cmd_revoke_invite_link(message: Message, command: CommandObject) -> None:
    """Деактивирует старую ссылку, не удаляя историю в БД (ТЗ 3.4.5: лимит 1000 ссылок на чат)."""
    try:
        link_id = int((command.args or "").strip())
    except ValueError:
        await message.reply("Использование: /revoke_invite_link <id>")
        return

    async with session_scope() as session:
        link = await session.get(InviteLink, link_id)
        if link is None:
            await message.reply("Ссылка не найдена")
            return
        target = await session.get(TargetChannel, link.target_channel_id)
        chat_id = target.tg_chat_id or f"@{target.username}"
        invite_link_value = link.tg_invite_link

    try:
        await message.bot.revoke_chat_invite_link(chat_id=chat_id, invite_link=invite_link_value)
    except Exception:
        logger.exception("Не удалось отозвать инвайт-ссылку %s", link_id)
        await message.reply("Не удалось отозвать ссылку в Telegram, но проверьте — возможно она уже неактивна.")

    async with session_scope() as session:
        link = await session.get(InviteLink, link_id)
        if link:
            link.revoked = True

    await message.reply(f"Ссылка #{link_id} деактивирована. История сохранена для отчётов по удержанию.")


@router.message(Command("add_gated_group"))
async def cmd_add_gated_group(message: Message, command: CommandObject) -> None:
    """Включает force-sub в группе: сообщения не подписанных на target_id участников
    будут удаляться, пока они не подпишутся на канал."""
    first_line, _, rest = (command.args or "").partition("\n")
    args = first_line.split(maxsplit=2)
    if len(args) < 3:
        await message.reply("Использование: /add_gated_group <group_chat_id> <target_id> <title...>")
        return
    group_chat_id_str, target_id_str, title = args
    try:
        group_chat_id = int(group_chat_id_str)
        target_id = int(target_id_str)
    except ValueError:
        await message.reply("group_chat_id и target_id должны быть числами")
        return

    async with session_scope() as session:
        target = await session.get(TargetChannel, target_id)
        if target is None:
            await message.reply("Целевой канал не найден")
            return
        gated = GatedGroup(
            tg_chat_id=group_chat_id,
            title=title,
            target_channel_id=target_id,
            added_by=str(message.from_user.id),
        )
        session.add(gated)
        await session.flush()
        gated_id = gated.id

    from news_agent.bot import force_sub  # локальный импорт — избегаем циклической зависимости

    await force_sub.ensure_gated_group_invite_links(message.bot)
    reply = (
        f"Force-sub включён для группы «{title}» (id={gated_id}). "
        "Убедитесь, что бот — админ и в этой группе (право удалять сообщения), и в целевом канале "
        "(право приглашать пользователей по ссылке)."
    )
    if rest.strip():
        reply += (
            "\n\n⚠️ В сообщении было что-то ещё на следующей строке — я это проигнорировал. "
            "Если хотели включить force-sub ещё для одной группы, отправьте /add_gated_group "
            "для неё отдельным сообщением."
        )
    await message.reply(reply)


@router.message(Command("rename_gated_group"))
async def cmd_rename_gated_group(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split(maxsplit=1)
    if len(args) < 2:
        await message.reply("Использование: /rename_gated_group <id> <новое_название>")
        return
    gated_id_str, title = args
    try:
        gated_id = int(gated_id_str)
    except ValueError:
        await message.reply("id должен быть числом")
        return
    async with session_scope() as session:
        gated = await session.get(GatedGroup, gated_id)
        if gated is None:
            await message.reply("Группа не найдена")
            return
        gated.title = title
    await message.reply(f"Название группы #{gated_id} обновлено на «{title}».")


@router.message(Command("list_gated_groups"))
async def cmd_list_gated_groups(message: Message) -> None:
    async with session_scope() as session:
        result = await session.execute(select(GatedGroup).order_by(GatedGroup.id))
        gated_groups = list(result.scalars())
    if not gated_groups:
        await message.reply("Групп с force-sub пока нет.")
        return
    lines = [
        f"#{g.id} «{g.title}» chat_id={g.tg_chat_id} target={g.target_channel_id} "
        f"active={'да' if g.active else 'нет'}"
        for g in gated_groups
    ]
    await message.reply("\n".join(lines))


@router.message(Command("pause_gated_group"))
async def cmd_pause_gated_group(message: Message, command: CommandObject) -> None:
    await _set_gated_group_active(message, command, active=False)


@router.message(Command("resume_gated_group"))
async def cmd_resume_gated_group(message: Message, command: CommandObject) -> None:
    await _set_gated_group_active(message, command, active=True)


async def _set_gated_group_active(message: Message, command: CommandObject, active: bool) -> None:
    try:
        gated_id = int((command.args or "").strip())
    except ValueError:
        await message.reply("Использование: /pause_gated_group <id>")
        return
    async with session_scope() as session:
        gated = await session.get(GatedGroup, gated_id)
        if gated is None:
            await message.reply("Группа не найдена")
            return
        gated.active = active
    await message.reply(f"Force-sub для группы #{gated_id} теперь {'включён' if active else 'выключен'}.")


@router.message(Command("report"))
async def cmd_report(message: Message, command: CommandObject) -> None:
    """Повторно отправляет отчёт в чат отчётов: за день (по умолчанию вчера) или за месяц (ГГГГ-ММ).
    Нужна, когда отчёт ушёл неполным (например, пока не работал юзербот) или не ушёл вовсе."""
    if not settings.reports_chat_id:
        await message.reply("REPORTS_CHAT_ID не задан — отчёты отключены.")
        return
    arg = (command.args or "").strip()
    try:
        if len(arg) == 7:
            year, month = (int(x) for x in arg.split("-"))
            dt.date(year, month, 1)  # ValueError для несуществующего месяца (например, 2026-13)
            await send_monthly_report(message.bot, year, month)
            sent = f"за {month:02d}.{year}"
        else:
            day = dt.date.fromisoformat(arg) if arg else dt.datetime.now(REPORT_TZ).date() - dt.timedelta(days=1)
            await send_daily_report(message.bot, day)
            sent = f"за {day:%d.%m.%Y}"
    except ValueError:
        await message.reply("Формат: /report 2026-11-13 (день) или /report 2026-11 (месяц). Без аргумента — за вчера.")
        return
    except Exception:
        logger.exception("Не удалось отправить отчёт по команде")
        await message.reply("Не удалось отправить отчёт, смотрите логи.")
        return
    await message.reply(f"Отчёт {sent} отправлен в чат отчётов.")


@router.message(Command("report_week"))
async def cmd_report_week(message: Message, command: CommandObject) -> None:
    """Недельный отчёт (понедельник–воскресенье). Аргумент — любая дата нужной недели;
    без аргумента — последняя закончившаяся неделя."""
    if not settings.reports_chat_id:
        await message.reply("REPORTS_CHAT_ID не задан — отчёты отключены.")
        return
    arg = (command.args or "").strip()
    today = dt.datetime.now(REPORT_TZ).date()
    try:
        monday = week_monday(dt.date.fromisoformat(arg)) if arg else last_full_week_monday(today)
    except ValueError:
        await message.reply("Формат: /report_week 2026-11-16 (любая дата нужной недели). Без аргумента — за прошлую неделю.")
        return
    if monday + dt.timedelta(days=7) > today:
        await message.reply("Эта неделя ещё не закончилась. Для текущего месяца используйте /report_mtd.")
        return
    try:
        await send_weekly_report(message.bot, monday)
    except Exception:
        logger.exception("Не удалось отправить недельный отчёт по команде")
        await message.reply("Не удалось отправить отчёт, смотрите логи.")
        return
    await message.reply(f"Недельный отчёт {monday:%d.%m}–{monday + dt.timedelta(days=6):%d.%m.%Y} отправлен в чат отчётов.")


@router.message(Command("report_mtd"))
async def cmd_report_mtd(message: Message) -> None:
    """С 1-го числа по сегодня и сравнение с прошлым месяцем к тому же моменту."""
    if not settings.reports_chat_id:
        await message.reply("REPORTS_CHAT_ID не задан — отчёты отключены.")
        return
    try:
        await send_month_to_date_report(message.bot)
    except Exception:
        logger.exception("Не удалось отправить отчёт с начала месяца")
        await message.reply("Не удалось отправить отчёт, смотрите логи.")
        return
    await message.reply("Отчёт с начала месяца отправлен в чат отчётов.")
