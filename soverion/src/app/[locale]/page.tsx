import Link from "next/link";
import { LeadForm } from "@/components/LeadForm";
import { Reveal } from "@/components/Reveal";
import { getDictionary } from "@/i18n";
import type { Locale } from "@/i18n/config";
import { products } from "@/content/products";

export default async function Home({ params }: { params: Promise<{ locale: Locale }> }) {
  const { locale } = await params;
  const d = getDictionary(locale);
  return (
    <>
      <section className="hero">
        <div className="blob" aria-hidden />
        <p className="kicker">{d.hero.kicker}</p>
        <h1>{d.hero.title}</h1>
        <p className="lead">{d.hero.text}</p>
        <Link href={`/${locale}/contacts`} className="btn">{d.cta}</Link>
        <dl className="facts">
          {d.hero.facts.map((f) => (
            <div key={f.l}><dt>{f.v}</dt><dd>{f.l}</dd></div>
          ))}
        </dl>
      </section>

      <div className="marquee" aria-hidden>
        <div className="track">
          {[...d.marquee, ...d.marquee].map((m, i) => <span key={i}>{m}</span>)}
        </div>
      </div>

      <section className="section">
        <Reveal><h2>{d.categoriesTitle}</h2></Reveal>
        <div className="grid">
          {products.map((p) => (
            <Reveal key={p.slug}>
              <Link href={`/${locale}/catalog#${p.slug}`} className="card">
                <div className="ph" aria-hidden />
                <h3>{p.name}</h3>
                <p>{p.category}</p>
              </Link>
            </Reveal>
          ))}
        </div>
      </section>

      <section className="section" id="process">
        <Reveal><h2>{d.processTitle}</h2></Reveal>
        <ol className="steps">
          {d.process.map((s, i) => (
            <Reveal key={s.t}>
              <li><span>0{i + 1}</span><h3>{s.t}</h3><p>{s.d}</p></li>
            </Reveal>
          ))}
        </ol>
      </section>

      <section className="section" id="cases">
        <Reveal><h2>{d.casesTitle}</h2><p className="muted">{d.casesNote}</p></Reveal>
      </section>

      <section className="section narrow"><LeadForm d={d} /></section>
    </>
  );
}
