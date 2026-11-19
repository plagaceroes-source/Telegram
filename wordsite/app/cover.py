"""Обложка поста в канал: рисуется на каждую смену слова, в стиле сайта (1280x720)."""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import config

BASE = Path(__file__).parent
FONT = BASE / "static" / "fonts" / "InterVariable.ttf"
W, H, S = 1280, 720, 2  # рисуем в 2x и уменьшаем: гладкие круги и линии
PAPER, INK, YELLOW, MUTED, SOFT = "#f8f6f4", "#050505", "#ffd60a", "#62615f", "#ece9e6"
MARGIN = 64


def font(size, weight=900):
    f = ImageFont.truetype(str(FONT), int(size * S))
    f.set_variation_by_axes([14, weight])  # opsz, wght
    return f


def _text_w(text, f, tracking):
    return sum(f.getlength(ch) + tracking for ch in text) - tracking


def _draw_tracked(d, xy, text, f, fill, tracking):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + tracking


def _fit_font(text, max_w, max_size, weight=900, track_em=-0.055):
    size = max_size
    while size > 24:
        f = font(size, weight)
        if _text_w(text, f, track_em * size * S) <= max_w:
            return f, size
        size -= 4
    return font(24, weight), 24


def _ellipsize(text, f, max_w):
    if f.getlength(text) <= max_w:
        return text
    while text and f.getlength(text + "…") > max_w:
        text = text[:-1]
    return text + "…"


def _avatar(base, d, cx, cy, r, word, logo_path):
    box = (cx - r, cy - r, cx + r, cy + r)
    if logo_path and Path(logo_path).exists():
        im = ImageOps.fit(Image.open(logo_path).convert("RGBA"), (2 * r, 2 * r), Image.LANCZOS)
        mask = Image.new("L", (2 * r, 2 * r), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
        base.paste(im, (cx - r, cy - r), mask)
    else:
        f = font(r / S, 900)
        ch = (word[:1] or "?").upper()
        bb = f.getbbox(ch)
        d.text((cx - (bb[0] + bb[2]) / 2, cy - (bb[1] + bb[3]) / 2), ch, font=f, fill=INK)
    d.ellipse(box, outline=YELLOW, width=2 * S)


def make_cover(word, price, next_price, business="", url="", logo_path=None) -> bytes:
    im = Image.new("RGB", (W * S, H * S), PAPER)
    d = ImageDraw.Draw(im)
    m = MARGIN * S

    # шапка: логотип + плашка
    f_logo = font(40)
    tr = -0.055 * 40 * S
    _draw_tracked(d, (m, 46 * S), "МЫ ИЗ", f_logo, INK, tr)
    _draw_tracked(d, (m, 46 * S + 38 * S), "БЛАНКИ", f_logo, INK, tr)
    f_tag = font(26, 650)
    tag = "НОВОЕ СЛОВО НА ГЛАВНОЙ"
    tw = f_tag.getlength(tag)
    x1, y1 = W * S - m, 62 * S
    d.rounded_rectangle((x1 - tw - 44 * S, y1, x1, y1 + 56 * S), radius=28 * S, outline=YELLOW, width=2 * S)
    d.text((x1 - tw - 22 * S, y1 + 13 * S), tag, font=f_tag, fill=INK)
    d.line((m, 160 * S, W * S - m, 160 * S), fill=YELLOW, width=2 * S)

    # слово
    text = (word or "").upper()
    f_word, size = _fit_font(text, (W - 2 * MARGIN) * S, 280)
    track = -0.055 * size * S
    cap = f_word.getbbox("H")
    cap_h = cap[3] - cap[1]
    word_top = 492 * S - cap_h  # слово стоит на одной базовой линии
    _draw_tracked(d, (m, word_top - cap[1]), text, f_word, INK, track)

    # корона (как на сайте)
    cw = 150 * S
    cx0, cy0 = m + 6 * S, word_top - 108 * S
    pts = [(8, 78), (20, 22), (46, 56), (66, 8), (90, 56), (116, 26), (112, 80), (8, 78)]
    pts = [(cx0 + x / 130 * cw, cy0 + y / 100 * cw * 100 / 130) for x, y in pts]
    d.line(pts, fill=YELLOW, width=5 * S, joint="curve")

    # низ: владелец и цена
    base_y = 590 * S
    r = 52
    _avatar(im, d, m + r * S, base_y, r * S, word, logo_path)
    name = business.strip() or "Аноним"
    f_name, f_url = font(36, 750), font(26, 450)
    tx = m + 2 * r * S + 28 * S
    max_text = 560 * S
    d.text((tx, base_y - (46 if url else 22) * S), _ellipsize(name, f_name, max_text), font=f_name, fill=INK)
    if url:
        short = url.replace("https://", "").replace("http://", "").rstrip("/")
        d.text((tx, base_y + 4 * S), _ellipsize(short + " ↗", f_url, max_text), font=f_url, fill=MUTED)

    f_big, f_small = font(46, 800), font(24, 500)
    pr = f"{price:g} €"
    nx = f"заменить: {next_price:g} €"
    bw = max(f_big.getlength(pr), f_small.getlength(nx)) + 56 * S
    bx1, bx0 = W * S - m, W * S - m - bw
    d.rounded_rectangle((bx0, base_y - 62 * S, bx1, base_y + 62 * S), radius=30 * S, outline=YELLOW, width=2 * S)
    d.text((bx0 + 28 * S, base_y - 50 * S), pr, font=f_big, fill=INK)
    d.text((bx0 + 28 * S, base_y + 14 * S), nx, font=f_small, fill=MUTED)

    out = io.BytesIO()
    im.resize((W, H), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def make_announce_cover(site="somosdeblanca.info", price=1, word="ТВОЁСЛОВО", tag="СЛОВО НА ПРОДАЖУ") -> bytes:
    """Обложка анонса запуска: огромное слово, адрес сайта и цена первого слова."""
    im = Image.new("RGB", (W * S, H * S), PAPER)
    d = ImageDraw.Draw(im)
    m = MARGIN * S
    f_logo = font(40)
    tr = -0.055 * 40 * S
    _draw_tracked(d, (m, 46 * S), "МЫ ИЗ", f_logo, INK, tr)
    _draw_tracked(d, (m, 46 * S + 38 * S), "БЛАНКИ", f_logo, INK, tr)
    f_tag = font(26, 650)
    tw = f_tag.getlength(tag)
    x1, y1 = W * S - m, 62 * S
    d.rounded_rectangle((x1 - tw - 44 * S, y1, x1, y1 + 56 * S), radius=28 * S, outline=YELLOW, width=2 * S)
    d.text((x1 - tw - 22 * S, y1 + 13 * S), tag, font=f_tag, fill=INK)
    d.line((m, 160 * S, W * S - m, 160 * S), fill=YELLOW, width=2 * S)

    f_word, size = _fit_font(word, (W - 2 * MARGIN) * S, 280)
    track = -0.055 * size * S
    cap = f_word.getbbox("H")
    word_top = 492 * S - (cap[3] - cap[1])
    _draw_tracked(d, (m, word_top - cap[1]), word, f_word, INK, track)
    cw = 150 * S
    cx0, cy0 = m + 6 * S, word_top - 108 * S
    pts = [(8, 78), (20, 22), (46, 56), (66, 8), (90, 56), (116, 26), (112, 80), (8, 78)]
    pts = [(cx0 + x / 130 * cw, cy0 + y / 100 * cw * 100 / 130) for x, y in pts]
    d.line(pts, fill=YELLOW, width=5 * S, joint="curve")

    base_y = 590 * S
    f_site, f_sub = font(38, 750), font(24, 450)
    d.text((m, base_y - 44 * S), f"{site} ↗", font=f_site, fill=INK)
    d.text((m, base_y + 8 * S), "Одно слово на всю Коста-Бланку", font=f_sub, fill=MUTED)
    f_big, f_small = font(46, 800), font(24, 500)
    pr, nx = f"от {price:g} €", "каждая замена ×2"
    bw = max(f_big.getlength(pr), f_small.getlength(nx)) + 56 * S
    bx1, bx0 = W * S - m, W * S - m - bw
    d.rounded_rectangle((bx0, base_y - 62 * S, bx1, base_y + 62 * S), radius=30 * S, outline=YELLOW, width=2 * S)
    d.text((bx0 + 28 * S, base_y - 50 * S), pr, font=f_big, fill=INK)
    d.text((bx0 + 28 * S, base_y + 14 * S), nx, font=f_small, fill=MUTED)
    out = io.BytesIO()
    im.resize((W, H), Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()
