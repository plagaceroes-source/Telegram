import type { NextConfig } from "next";

const config: NextConfig = {
  async redirects() {
    // Старт только RU; /en и /es подключаются после запуска (см. src/i18n/config.ts)
    return [{ source: "/", destination: "/ru", permanent: false }];
  },
  images: { formats: ["image/avif", "image/webp"] },
};

export default config;
