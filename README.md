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
5. Получить ключ для перевода/рерайта — задаётся через `AI_PROVIDER` в `.env`:
   - `AI_PROVIDER=claude` + `ANTHROPIC_API_KEY` с https://console.anthropic.com
     (pay-as-you-go, без гарантированного бесплатного тарифа);
   - `AI_PROVIDER=gemini` + `GEMINI_API_KEY` с https://aistudio.google.com/apikey
     (есть бесплатный тариф — удобно для теста).
   Переключение между ними — только правка `.env`, код менять не нужно
   (см. `news_agent/services/ai_provider.py`).
6. Настроить, кому приходят карточки на утверждение (ТЗ 2.5 п.6):
   - **Лично каждому** (по умолчанию): узнать Telegram user id через `@userinfobot`
     → `APPROVER_CHAT_IDS=id1,id2,...`. Каждый получит свою копию карточки в личку.
   - **В общую группу** (чтобы утверждать могли несколько человек одной кнопкой):
     создать группу → добавить туда бота → написать в группе что-нибудь и посмотреть
     id чата (например через `@getidsbot`, либо в логах бота при первом сообщении —
     id супергруппы отрицательный, вида `-1001234567890`) → `APPROVAL_CHAT_ID=-100...`.
     `APPROVER_CHAT_IDS` в этом режиме опционален: если задать — кнопки смогут
     нажимать только эти user id, если оставить пустым — любой участник группы.
     Кнопки (Publish/Edit/Reject) работают в группе сразу, без доп. настроек в
     @BotFather; а вот текстовые команды (`/add_source` и т.п.) в группе потребуют
     `/setprivacy` → Disable у @BotFather, либо отправки командой в ответ на
     сообщение бота.

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

## Деплой на Railway (альтернатива Render)

У Railway нет единого файла-блюпринта на весь стек, как `render.yaml` у Render —
каждый сервис создаётся в дашборде отдельно, но использует один и тот же
GitHub-репозиторий и общий `railway.json` (настройки сборки через Nixpacks).

1. **New Project → Deploy from GitHub repo** → выбрать этот репозиторий.
2. **+ New → Database → PostgreSQL** — Railway создаст БД и переменную
   `DATABASE_URL` (код сам приводит её к `postgresql+asyncpg://`, руками менять
   не нужно).
3. Создать **Shared Variables** проекта (Project Settings → Variables) —
   аналог envVarGroup в Render, доступны всем сервисам через `${{shared.ИМЯ}}`:
   `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `BOT_TOKEN`, `AI_PROVIDER`,
   `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `TARGET_LANGUAGE`,
   `APPROVAL_CHAT_ID`, `APPROVER_CHAT_IDS`.
4. На каждый компонент — **+ New → GitHub Repo** (тот же репозиторий, ещё один
   сервис), в Settings → Deploy указать **Custom Start Command**:

   | Сервис | Start Command | Доп. настройки |
   |---|---|---|
   | admin | `python scripts/run_admin.py` | Settings → Networking → Generate Domain (публичный URL); слушает `$PORT`, который Railway передаёт автоматически |
   | bot | `python scripts/run_bot.py` | подключить Volume, mount path `/app/storage` |
   | processing | `python scripts/run_processing.py` | — |
   | userbot-1 | `python scripts/run_userbot.py` | `USERBOT_ACCOUNT_ID=1`; тот же (или отдельный) Volume на `/app/storage` |
   | stats-snapshot | `python scripts/run_stats_snapshot.py` | Settings → Cron Schedule: `0 * * * *` |

   Каждый сервис — ссылку на все Shared Variables + свою `DATABASE_URL` (реф на
   сервис Postgres) добавить в Variables.
5. Volumes в Railway обязательно нужны для юзербота (сессия Telethon) и бота
   (временные медиа) — без них файловая система сбрасывается при редеплое,
   как и на Render.

## Структура проекта

```
news_agent/
  config.py           — настройки из .env
  db/                  — модели SQLAlchemy + сессия (async)
  services/            — Claude API (перевод/рерайт), детект языка
  userbot/             — Telethon-слушатель (по аккаунту на процесс)
  processing/          — обработчик очереди raw_posts → draft_posts
  bot/                 — aiogram: карточки на утверждение + админ-команды + публикация
                          + force_sub.py — гейт на подписку в группах (см. ниже)
  stats/               — снепшоты подписчиков, join/leave атрибуция по инвайт-ссылкам
admin/                 — FastAPI веб-админка (CRUD источников/каналов, статистика)
scripts/               — точки входа для каждого процесса
render.yaml            — блюпринт деплоя на Render
```

## Force-sub: гейт на подписку в группах

Тот же approval-бот (`scripts/run_bot.py`) умеет в отдельных группах требовать
подписку на канал из сетки: сообщение участника, который не подписан на
привязанный `target_channel`, удаляется, а в чат на `FORCE_SUB_WARNING_TTL`
секунд присылается предупреждение с кнопкой-приглашением. У каждой группы —
своя именная invite-ссылка на канал (переиспользует `InviteLink`/`SubscriberEvent`
из `news_agent/stats/events.py`), поэтому вступления по ней попадают в ту же
статистику атрибуции, что и обычные кампании.

Настройка:

1. Добавить бота админом в группу с правом «Удаление сообщений».
2. Убедиться, что бот уже админ целевого канала с правом «Приглашение
   пользователей по ссылке» (см. раздел выше про добавление целевых каналов).
3. Узнать `tg_chat_id` группы (отрицательное число вида `-100...`, как и для
   `APPROVAL_CHAT_ID`) и `id` нужного целевого канала (`/list_targets`).
4. В боте: `/add_gated_group <group_chat_id> <target_id> <title...>`.

Команды: `/list_gated_groups`, `/pause_gated_group <id>`, `/resume_gated_group <id>`.

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
