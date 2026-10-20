// Для запуска EN/ES: добавить код в locales и словарь в dictionaries.
export const locales = ["ru"] as const;
export type Locale = (typeof locales)[number];
export const defaultLocale: Locale = "ru";
export const isLocale = (v: string): v is Locale => (locales as readonly string[]).includes(v);
