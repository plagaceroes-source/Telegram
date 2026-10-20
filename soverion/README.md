# SOVERION — корпоративный сайт

Next.js (App Router) + TypeScript. Сайт-витрина мерча полного цикла для B2B, сбор заявок на расчёт.

## Запуск
```bash
npm install
cp .env.example .env.local   # заполнить ключи
npm run dev                  # http://localhost:3000 -> /ru
npm run build
```

## Структура
- `src/app/[locale]/` — страницы (главная, каталог, контакты)
- `src/app/api/lead/` — приём заявок: Telegram + email (Resend); YouGile — фаза 2
- `src/i18n/` — словари; сейчас только RU, EN/ES добавляются в `config.ts`
- `src/content/` — данные витрины (временно в коде, дальше CMS)
- `assets/` — логотип, мокапы, медиа клиента (исходники проекта)
- `docs/` — решения по ТЗ и план работ

## Статус
См. `docs/PLAN.md`.
