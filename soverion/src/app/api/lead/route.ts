import { NextResponse } from "next/server";

export const runtime = "nodejs";
const MAX_FILE = 10 * 1024 * 1024;

const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);

export async function POST(req: Request) {
  const fd = await req.formData();
  if (fd.get("website")) return NextResponse.json({ ok: true }); // honeypot

  const name = String(fd.get("name") ?? "").trim().slice(0, 100);
  const contact = String(fd.get("contact") ?? "").trim().slice(0, 150);
  const comment = String(fd.get("comment") ?? "").trim().slice(0, 2000);
  const product = String(fd.get("product") ?? "").trim().slice(0, 100);
  if (!name || !contact || !fd.get("consent")) {
    return NextResponse.json({ ok: false, error: "invalid" }, { status: 400 });
  }
  const file = fd.get("file");
  const hasFile = file instanceof File && file.size > 0;
  if (hasFile && file.size > MAX_FILE) {
    return NextResponse.json({ ok: false, error: "file_too_large" }, { status: 413 });
  }

  const text =
    `<b>Новая заявка SOVERION</b>\nИмя: ${esc(name)}\nКонтакт: ${esc(contact)}` +
    (product ? `\nТовар: ${esc(product)}` : "") +
    (comment ? `\nКомментарий: ${esc(comment)}` : "");

  const jobs: Promise<Response>[] = [];
  const { TELEGRAM_BOT_TOKEN: tg, TELEGRAM_CHAT_ID: chat, RESEND_API_KEY: resend } = process.env;

  if (tg && chat) {
    const api = `https://api.telegram.org/bot${tg}`;
    if (hasFile) {
      const body = new FormData();
      body.set("chat_id", chat);
      body.set("caption", text);
      body.set("parse_mode", "HTML");
      body.set("document", file);
      jobs.push(fetch(`${api}/sendDocument`, { method: "POST", body }));
    } else {
      jobs.push(
        fetch(`${api}/sendMessage`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ chat_id: chat, text, parse_mode: "HTML" }),
        }),
      );
    }
  }
  if (resend && process.env.LEAD_EMAIL_TO && process.env.LEAD_EMAIL_FROM) {
    jobs.push(
      fetch("https://api.resend.com/emails", {
        method: "POST",
        headers: { authorization: `Bearer ${resend}`, "content-type": "application/json" },
        body: JSON.stringify({
          from: process.env.LEAD_EMAIL_FROM,
          to: process.env.LEAD_EMAIL_TO,
          subject: `Заявка SOVERION: ${name}`,
          html: text.replace(/\n/g, "<br>"),
        }),
      }),
    );
  }
  // TODO фаза 2: передача в YouGile (API-ключ и колонка уточняются у клиента)

  if (jobs.length === 0) {
    console.warn("[lead] каналы доставки не настроены, заявка:", { name, contact, comment, product });
    return NextResponse.json({ ok: true, delivered: false });
  }
  const results = await Promise.allSettled(jobs);
  const ok = results.some((r) => r.status === "fulfilled" && r.value.ok);
  return NextResponse.json({ ok }, { status: ok ? 200 : 502 });
}
