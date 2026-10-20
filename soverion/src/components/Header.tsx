import Link from "next/link";
import type { Dictionary } from "@/i18n/ru";

export function Header({ d, locale }: { d: Dictionary; locale: string }) {
  return (
    <header className="header">
      <Link href={`/${locale}`} className="logo" aria-label={d.brand}>
        {d.brand}
      </Link>
      <nav aria-label="Основное меню">
        {d.nav.map((n) => (
          <Link key={n.href} href={`/${locale}${n.href}`}>
            {n.label}
          </Link>
        ))}
      </nav>
      <Link href={`/${locale}/contacts`} className="btn btn-sm">
        {d.cta}
      </Link>
    </header>
  );
}
