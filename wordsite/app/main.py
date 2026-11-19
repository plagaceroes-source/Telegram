import asyncio
import contextlib
import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import bot, config, fmt, images, service

BASE = Path(__file__).parent
log = logging.getLogger("web")


async def sweeper():
    while True:
        try:
            for o in service.sweep_expired():
                await bot.notify_expired(o)
        except Exception:
            log.exception("sweeper")
        await asyncio.sleep(3)


@contextlib.asynccontextmanager
async def lifespan(app):
    service.init()
    tasks = [asyncio.create_task(sweeper()), asyncio.create_task(bot.start())]
    yield
    for t in tasks:
        t.cancel()
    await bot.stop()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
Path(config.LOGO_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/logos", StaticFiles(directory=config.LOGO_DIR), name="logos")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")


def render(request: Request, name: str, **ctx):
    ctx.update(cfg=config, st=service.state(), page=name.removesuffix(".html"), year=datetime.now().year)
    return templates.TemplateResponse(request, name, ctx)


def duration(sec: float) -> str:
    sec = int(sec)
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d} д {h} ч"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин" if m else f"{sec} сек"


templates.env.filters["duration"] = duration
templates.env.filters["date"] = lambda t: datetime.fromtimestamp(t, ZoneInfo("Europe/Madrid")).strftime("%d.%m.%Y %H:%M")
templates.env.filters["price"] = fmt.price
templates.env.filters["date_ru"] = fmt.date_ru


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/")
async def home(request: Request):
    return render(request, "home.html", recent=service.history(2))


@app.get("/rules")
async def rules(request: Request):
    return render(request, "rules.html")


@app.get("/history")
async def history(request: Request):
    return render(request, "history.html", words=service.all_words())


@app.get("/about")
async def about(request: Request):
    return render(request, "about.html")


@app.get("/offer")
async def offer(request: Request):
    if not config.SHOW_OFFER:
        raise HTTPException(404)
    return render(request, "offer.html")


@app.get("/privacy")
async def privacy(request: Request):
    return render(request, "privacy.html")


@app.get("/o/{token}")
async def order_page(request: Request, token: str):
    o = service.get_order_by_token(token)
    if not o:
        raise HTTPException(404)
    return render(request, "order.html", token=token)


# ---------- API ----------
def _public_word(w):
    return w and {"text": w["text"], "url": w["url"], "business": w["business"], "logo": w["logo"],
                  "price": w["price"], "started_at": w["started_at"]}


@app.get("/api/state")
async def api_state(vid: str = ""):
    s = service.state()
    return {"word": _public_word(s["word"]), "next_price": s["next_price"], "locked": s["locked"],
            "lock_left": s["lock_left"], "online": service.ping(vid),
            "total_words": s["total_words"], "total_sum": s["total_sum"]}


@app.post("/api/orders")
async def api_create(request: Request, word: str = Form(...), url: str = Form(""), business: str = Form(""),
                     contact: str = Form(""), agree: bool = Form(False), logo: UploadFile | None = File(None)):
    if not agree:
        raise HTTPException(400, "Нужно согласиться с правилами")
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "").split(",")[0].strip()
    name = None
    try:
        service.clean_word(word)  # проверяем слово до записи файла
        if logo and logo.filename:
            name = images.save_logo(await logo.read(config.MAX_LOGO_BYTES + 1))
        o = service.create_order(word, url, business, contact, ip, name)
    except (service.OrderError, images.LogoError) as e:
        images.delete_logo(name)
        raise HTTPException(400, str(e))
    await bot.notify_new(o)
    return {"token": o["token"]}


def _order_view(o):
    now = time.time()
    v = {"word": o["word"], "status": o["status"], "price": o["price"], "method": o["method"],
         "left": max(0, int(o["expires_at"] - now)) if o["status"] == "approved" and o["expires_at"] else None}
    if o["status"] in ("approved", "claimed"):
        v["pay"] = service.pay_info(o["price"])
    return v


@app.get("/api/orders/{token}")
async def api_order(token: str):
    o = service.get_order_by_token(token)
    if not o:
        raise HTTPException(404)
    return _order_view(o)


class ClaimIn(BaseModel):
    method: str


@app.post("/api/orders/{token}/claim")
async def api_claim(token: str, body: ClaimIn):
    try:
        o = service.claim_paid(token, body.method)
    except service.OrderError as e:
        raise HTTPException(400, str(e))
    await bot.notify_claimed(o)
    return _order_view(o)


@app.post("/api/orders/{token}/cancel")
async def api_cancel(token: str):
    try:
        o = service.cancel(token)
    except service.OrderError as e:
        raise HTTPException(400, str(e))
    return _order_view(o)
