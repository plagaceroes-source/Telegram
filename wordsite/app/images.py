"""Обработка логотипов: настоящая проверка картинки, квадрат 256x256, PNG без метаданных."""
import io
import secrets
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from . import config

Image.MAX_IMAGE_PIXELS = 25_000_000
SIZE = 256


class LogoError(Exception):
    pass


def save_logo(data: bytes) -> str:
    if not data:
        raise LogoError("Файл пустой")
    if len(data) > config.MAX_LOGO_BYTES:
        raise LogoError(f"Логотип больше {config.MAX_LOGO_BYTES // 1024 // 1024} МБ")
    try:
        im = Image.open(io.BytesIO(data))
        if im.format not in ("PNG", "JPEG", "WEBP"):
            raise LogoError("Подходят PNG, JPG и WebP")
        im.load()
        im = ImageOps.exif_transpose(im).convert("RGBA")
    except LogoError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise LogoError("Это не похоже на картинку")
    im = ImageOps.fit(im, (SIZE, SIZE), Image.LANCZOS)
    name = secrets.token_urlsafe(16).replace("-", "a").replace("_", "b") + ".png"
    out = Path(config.LOGO_DIR)
    out.mkdir(parents=True, exist_ok=True)
    im.save(out / name, "PNG", optimize=True)  # перекодирование убирает метаданные и скрытые данные
    return name


def delete_logo(name: str | None):
    if name and name.isalnum() is False and not name.replace(".png", "").isalnum():
        return
    if name:
        try:
            (Path(config.LOGO_DIR) / name).unlink(missing_ok=True)
        except OSError:
            pass
