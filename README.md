# News Agent — автоматизация новостных каналов Telegram

Модуль автоматизации: юзербот мониторит каналы-источники → Claude API переводит
и рерайтит посты → бот присылает черновик владельцу на утверждение → после
одобрения пост публикуется в сетку целевых каналов. Плюс веб-админка для
управления источниками/каналами и статистика подписчиков сети.

Полное техническое задание — во вложенном PDF; ниже только шпаргалка по запуску.

## Архитектура

```
Userbot (Telethon)  →  Processing core (Claude API)  →  Approval bot (Bot API)
                                                              │ approve
                                                              ▼
Admin panel (FastAPI) ⇄ Database (SQLite/Postgres) ⇄ Publisher (тот же/другой бот)
                              ▲
                     Stats collector (снепшоты + join/leave)
```

Каждый компонент — отдельный процесс (см. `scripts/`), связаны только через БД.

## Установка

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # и заполнить значения
```

### Что нужно подготовить вручную (ТЗ раздел 6)

1. Отдельный (или существующий) номер телефона для юзербота → зарегистрировать
   на https://my.telegram.org → получить `api_id`/`api_hash` → в `.env`.
2. Через `@BotFather` создать бота (можно один на approval+publisher для старта)
   → токен в `BOT_TOKEN`.
3. Добавить этого бота администратором во все целевые каналы сети с правом
   публикации и правом приглашать пользователей (для инвайт-ссылок и chat_member
   апдейтов).
4. Собрать первичный список каналов-источников.
5. Получить ключ Anthropic API → `ANTHROPIC_API_KEY`.
6. Узнать свой Telegram user id (например через `@userinfobot`) → `APPROVER_CHAT_IDS`
   (через запятую, если утверждающих несколько).

### Первый запуск

```bash
# 1. Авторизовать первый аккаунт юзербота (интерактивно вводится код из Telegram/SMS)
python scripts/add_userbot_account.py --phone +34600111222 --label "Аккаунт 1"

# 2. Поднять компоненты (в отдельных терминалах/процессах)
python scripts/run_bot.py            # approval + publisher бот
python scripts/run_processing.py     # перевод/рерайт через Claude
python scripts/run_userbot.py --account-id 1
python scripts/run_admin.py          # веб-админка на http://localhost:8000

# 3. Источники и целевые каналы добавляются либо командами боту (/add_source,
#    /add_target — см. /help в боте), либо через веб-админку.
```

### Разовый снепшот статистики подписчиков

```bash
python scripts/run_stats_snapshot.py
```
На проде запускается по расписанию как Render Cron Job (см. `render.yaml`).

## Деплой на Render

См. `render.yaml` — блюпринт разворачивает:

| Компонент | Тип сервиса |
|---|---|
| Userbot (на каждый аккаунт) | Background Worker + персистентный Disk (сессия) |
| Approval + Publisher бот | Background Worker (long polling) |
| Processing core | Background Worker |
| Админка | Web Service |
| Снепшоты статистики | Cron Job |
| БД | Render PostgreSQL (managed) |

Секреты (`ANTHROPIC_API_KEY`, `TELEGRAM_API_ID/HASH`, `BOT_TOKEN`, `APPROVER_CHAT_IDS`)
задаются через Render Environment Variables/Secret Files — не хранить в репозитории.

При добавлении нового аккаунта юзербота: продублируйте сервис
`news-agent-userbot-1` в `render.yaml` с новым `USERBOT_ACCOUNT_ID`, авторизуйте
аккаунт локально через `add_userbot_account.py`, скопируйте файл сессии из
`storage/sessions/` на диск нового сервиса (или перезапустите скрипт авторизации
с тем же `--phone` прямо на сервере — код придёт в тот же Telegram-аккаунт).

## Структура проекта

```
news_agent/
  config.py           — настройки из .env
  db/                  — модели SQLAlchemy + сессия (async)
  services/            — Claude API (перевод/рерайт), детект языка
  userbot/             — Telethon-слушатель (по аккаунту на процесс)
  processing/          — обработчик очереди raw_posts → draft_posts
  bot/                 — aiogram: карточки на утверждение + админ-команды + публикация
  stats/               — снепшоты подписчиков, join/leave атрибуция по инвайт-ссылкам
admin/                 — FastAPI веб-админка (CRUD источников/каналов, статистика)
scripts/               — точки входа для каждого процесса
render.yaml            — блюпринт деплоя на Render
```

## Известные ограничения (проговорено в ТЗ, не баг)

- Telegram не даёт 100%-точной атрибуции подписчиков по источнику — именные
  инвайт-ссылки дают лучшее приближение, не гарантию (раздел 3.3).
- Bot API не различает, по какой ссылке ушедший пользователь когда-то вступил —
  берётся последний известный `join` для этой пары user/канал (раздел 3.4.4).
- Лимит Telegram — не более 1000 активных инвайт-ссылок на чат одновременно;
  старые кампании стоит деактивировать командой `/revoke_invite_link` (раздел 3.4.5).

## Что не реализовано в этой версии (следующие шаги)

- Автобалансировка источников между аккаунтами юзербота (сейчас ручной выбор
  аккаунта при добавлении источника — так и предполагалось на старте, раздел 2.1.2).
- Автоматическая периодическая деактивация устаревших инвайт-ссылок (сейчас
  вручную командой `/revoke_invite_link`).
- Webhook-режим для approval/publisher бота (сейчас long polling; вебхук —
  опция для будущего, раздел 8).
