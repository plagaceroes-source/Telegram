"use client";
import { useState, type FormEvent } from "react";
import type { Dictionary } from "@/i18n/ru";

export function LeadForm({ d, product }: { d: Dictionary; product?: string }) {
  const [state, setState] = useState<"idle" | "sending" | "ok" | "err">("idle");

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setState("sending");
    try {
      const res = await fetch("/api/lead", { method: "POST", body: new FormData(e.currentTarget) });
      setState(res.ok ? "ok" : "err");
      if (res.ok) e.currentTarget.reset();
    } catch {
      setState("err");
    }
  }

  const f = d.form;
  return (
    <form className="form" onSubmit={onSubmit}>
      <h2>{f.title}</h2>
      <input type="hidden" name="product" value={product ?? ""} />
      {/* honeypot от спама */}
      <input type="text" name="website" tabIndex={-1} autoComplete="off" className="hp" aria-hidden />
      <label>
        {f.name}
        <input name="name" required maxLength={100} />
      </label>
      <label>
        {f.contact}
        <input name="contact" required maxLength={150} />
      </label>
      <label>
        {f.comment}
        <textarea name="comment" rows={4} maxLength={2000} />
      </label>
      <label>
        {f.file}
        <input type="file" name="file" accept=".png,.jpg,.jpeg,.svg,.pdf,.ai,.zip" />
      </label>
      <label className="check">
        <input type="checkbox" name="consent" required /> {f.consent}
      </label>
      <button className="btn" disabled={state === "sending"}>
        {f.submit}
      </button>
      {state === "ok" && <p role="status" className="ok">{f.ok}</p>}
      {state === "err" && <p role="alert" className="err">{f.err}</p>}
    </form>
  );
}
