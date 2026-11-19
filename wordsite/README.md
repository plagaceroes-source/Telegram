# Мы из Бланки: сайт «Слово»

На главной одно слово. Хочешь своё — перебей ставку (каждая покупка ×2). Отдельный проект, к `news_agent` не привязан.

## Как работает
1. «Заменить слово» → заявка уходит в группу модерации (бот, кнопки ✅/❌).
2. Одобрили → цена фиксируется (= цена предыдущего ×2), слот держится за заявкой **5 минут**.
3. Клиент оплачивает (Bizum / карта Монобанка / крипта) и жмёт «Я оплатил» — таймер замирает, модератору приходит «💰 Оплата пришла / 🚫 Не пришла».
4. Подтвердили → слово сразу на сайте и в канале, цена следующего ×2, слот свободен.
5. Не оплатил за 5 минут (и не нажал «Я оплатил») → заявка сгорает, слот свободен.

Пока слот занят, другую заявку одобрить нельзя. Минимального срока показа нет.

## Запуск
```bash
cd wordsite
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить BOT_TOKEN, MOD_CHAT_ID, реквизиты
python run.py          # http://localhost:8000
python -m pytest tests # (pip install pytest)
```
Без `BOT_TOKEN` сайт работает, но модерации нет (заявки не одобрить).

## Что настроить
- `BOT_TOKEN` (@BotFather), `MOD_CHAT_ID`: добавь бота в группу модераторов. Кнопки работают только в этой группе.
- `CHANNEL_ID`: бот должен быть админом канала, тогда смена слова постится автоматически.
- Реквизиты оплаты и данные оператора для оферты: в `.env`.
- Юридические страницы (`offer.html`, `privacy.html`) — черновики, нужна проверка юристом.
- Один процесс (сайт + бот + таймер); SQLite-файл `DB_PATH`. Не запускай несколько копий.

## Деплой на Railway
1. Railway → **New Project → Deploy from GitHub repo** → этот репозиторий, ветка с проектом.
2. В сервисе: **Settings → Source → Root Directory = `wordsite`** (в корне репозитория лежит `railway.json` другого проекта, он не нужен). Сборка и запуск берутся из `wordsite/railway.json`.
3. **Settings → Volumes → + Add Volume**, mount path `/data` (база и логотипы).
4. **Variables:**
   - `DB_PATH=/data/wordsite.db`, `LOGO_DIR=/data/logos`
   - `BOT_TOKEN`, `MOD_CHAT_ID`, `CHANNEL_ID`, `CHANNEL_URL`, `SUPPORT_USERNAME`
   - `SITE_URL=https://<твой-домен>`
   - `BIZUM_PHONE`, `MONO_CARD`, `CRYPTO_ADDRESS`, `CRYPTO_NETWORK`, `UAH_RATE`, `START_PRICE`
   - `OPERATOR_NAME`, `OPERATOR_EMAIL`
5. **Settings → Networking → Generate Domain** (потом свой домен через Custom Domain).
6. Проверка: `https://<домен>/health` отвечает `{"ok": true}`.

Реплика должна быть одна (`numReplicas: 1`): внутри работает бот на long polling и таймер заявок. Две копии бота = конфликт Telegram.

## Резервная копия
В группе модерации команда `/backup` присылает zip: `wordsite.db` (согласованная копия базы) + папка `logos/`. Сохраняй файл у себя.
Восстановление вручную: остановить сервис, положить `wordsite.db` в `/data/` и содержимое `logos/` в `/data/logos/` (через Railway Console), запустить снова.
