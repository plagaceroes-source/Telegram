import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { Header } from "@/components/Header";
import { Footer } from "@/components/Footer";
import { getDictionary } from "@/i18n";
import { isLocale, locales } from "@/i18n/config";
import "../globals.css";

export const generateStaticParams = () => locales.map((locale) => ({ locale }));

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://soverion.com"),
  title: { default: "SOVERION — производство мерча полного цикла", template: "%s | SOVERION" },
  description: "Мерч под ключ для бизнеса: дизайн, пошив, нанесение, доставка. Тираж от 50 шт.",
};

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const d = getDictionary(locale);
  return (
    <html lang={locale}>
      <body>
        <Header d={d} locale={locale} />
        <main>{children}</main>
        <Footer d={d} />
      </body>
    </html>
  );
}
