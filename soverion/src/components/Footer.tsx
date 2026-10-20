import type { Dictionary } from "@/i18n/ru";

export function Footer({ d }: { d: Dictionary }) {
  return (
    <footer className="footer">
      <span>© {new Date().getFullYear()} {d.brand}. {d.footer.rights}</span>
      <a href="#">{d.footer.privacy}</a>
    </footer>
  );
}
