import type { Metadata } from "next";
import { LeadForm } from "@/components/LeadForm";
import { getDictionary } from "@/i18n";
import type { Locale } from "@/i18n/config";
import { products } from "@/content/products";

export const metadata: Metadata = { title: "Каталог" };

export default async function Catalog({ params }: { params: Promise<{ locale: Locale }> }) {
  const { locale } = await params;
  const d = getDictionary(locale);
  return (
    <>
      <section className="section">
        <h1>{d.catalog.title}</h1>
        <p className="lead">{d.catalog.lead}</p>
        <div className="grid">
          {products.map((p) => (
            <article key={p.slug} id={p.slug} className="card">
              <div className="ph" aria-hidden />
              <h3>{p.name}</h3>
              <p>{d.catalog.min}: {p.min} шт.</p>
              <p>{d.catalog.methods}: {p.methods.join(", ")}</p>
            </article>
          ))}
        </div>
      </section>
      <section className="section narrow"><LeadForm d={d} /></section>
    </>
  );
}
