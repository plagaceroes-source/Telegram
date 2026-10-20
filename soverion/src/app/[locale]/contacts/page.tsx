import type { Metadata } from "next";
import { LeadForm } from "@/components/LeadForm";
import { getDictionary } from "@/i18n";
import type { Locale } from "@/i18n/config";

export const metadata: Metadata = { title: "Контакты" };

export default async function Contacts({ params }: { params: Promise<{ locale: Locale }> }) {
  const { locale } = await params;
  const d = getDictionary(locale);
  return (
    <section className="section narrow">
      <h1>{d.contacts.title}</h1>
      <p className="muted">Телефон, email, мессенджеры и реквизиты — после получения данных от клиента.</p>
      <LeadForm d={d} />
    </section>
  );
}
