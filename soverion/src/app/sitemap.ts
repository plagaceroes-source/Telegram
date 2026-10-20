import type { MetadataRoute } from "next";
import { locales } from "@/i18n/config";

export default function sitemap(): MetadataRoute.Sitemap {
  const base = process.env.NEXT_PUBLIC_SITE_URL ?? "https://soverion.com";
  return locales.flatMap((l) => ["", "/catalog", "/contacts"].map((p) => ({ url: `${base}/${l}${p}` })));
}
