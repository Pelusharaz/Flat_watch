"""Listing photos: validate, strip metadata (EXIF/GPS), resize, compress, thumbnail.

Files live in static/photos/<listing_id>/ with random names, so Streamlit can serve them
at /app/static/... (used by email notifications) and nobody can guess other paths.
"""
from __future__ import annotations

import io
import secrets
import time
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageOps

from .config import STATIC_DIR

MAX_BYTES = 10 * 1024 * 1024
MAX_PHOTOS = 20
FULL_SIZE = 1600
THUMB_SIZE = 480
ALLOWED = {"JPEG", "PNG", "WEBP"}
Image.MAX_IMAGE_PIXELS = 40_000_000        # decompression-bomb guard


class PhotoError(ValueError):
    pass


def _dir(listing_id: str) -> Path:
    safe = "".join(c for c in listing_id if c.isalnum() or c in "-_")
    d = STATIC_DIR / "photos" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def process_upload(db, listing_id: str, data: bytes, category: str = "Other") -> int:
    """Store one uploaded photo. Returns the photo id. Raises PhotoError for bad files."""
    if len(data) > MAX_BYTES:
        raise PhotoError("Photo is larger than 10 MB.")
    n = db.one("SELECT COUNT(*) AS n FROM photos WHERE listing_id=?", (listing_id,))["n"]
    if n >= MAX_PHOTOS:
        raise PhotoError(f"Max {MAX_PHOTOS} photos per listing.")
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        probe.verify()                              # detects truncated / corrupt files
        img = Image.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise PhotoError("Not a valid image.") from exc
    if fmt not in ALLOWED:
        raise PhotoError("Use JPEG, PNG or WEBP.")
    img = ImageOps.exif_transpose(img).convert("RGB")     # apply rotation, then drop ALL metadata
    return _save(db, listing_id, img, category)


def _save(db, listing_id: str, img: Image.Image, category: str) -> int:
    d = _dir(listing_id)
    name = secrets.token_hex(8)
    full = img.copy()
    full.thumbnail((FULL_SIZE, FULL_SIZE))
    thumb = img.copy()
    thumb.thumbnail((THUMB_SIZE, THUMB_SIZE))
    full_path, thumb_path = d / f"{name}.jpg", d / f"{name}_t.jpg"
    full.save(full_path, "JPEG", quality=82, optimize=True, progressive=True)   # re-encode = no hidden payload
    thumb.save(thumb_path, "JPEG", quality=75, optimize=True)
    sort = db.one("SELECT COALESCE(MAX(sort), -1) + 1 AS s FROM photos WHERE listing_id=?", (listing_id,))["s"]
    cur = db.execute(
        "INSERT INTO photos(listing_id, path, thumb_path, category, width, height, bytes, sort, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (listing_id, full_path.relative_to(STATIC_DIR).as_posix(), thumb_path.relative_to(STATIC_DIR).as_posix(), category,
         full.width, full.height, full_path.stat().st_size, sort, time.time()))
    return cur.lastrowid


def list_photos(db, listing_id: str) -> list:
    return db.query("SELECT * FROM photos WHERE listing_id=? ORDER BY sort, id", (listing_id,))


def first_thumb(db, listing_id: str) -> Optional[str]:
    r = db.one("SELECT thumb_path FROM photos WHERE listing_id=? ORDER BY sort, id LIMIT 1", (listing_id,))
    return r["thumb_path"] if r else None


def abs_path(rel: str) -> str:
    return str(STATIC_DIR / rel)


def delete_photo(db, photo_id: int, listing_id: str) -> None:
    r = db.one("SELECT * FROM photos WHERE id=? AND listing_id=?", (photo_id, listing_id))
    if r:
        for p in (r["path"], r["thumb_path"]):
            (STATIC_DIR / p).unlink(missing_ok=True)
        db.execute("DELETE FROM photos WHERE id=?", (photo_id,))


def move_photo(db, photo_id: int, listing_id: str, direction: int) -> None:
    rows = list_photos(db, listing_id)
    ids = [r["id"] for r in rows]
    if photo_id not in ids:
        return
    i = ids.index(photo_id)
    j = max(0, min(len(ids) - 1, i + direction))
    ids[i], ids[j] = ids[j], ids[i]
    for pos, pid in enumerate(ids):
        db.execute("UPDATE photos SET sort=? WHERE id=?", (pos, pid))


def set_category(db, photo_id: int, listing_id: str, category: str) -> None:
    db.execute("UPDATE photos SET category=? WHERE id=? AND listing_id=?", (category, photo_id, listing_id))


# ---------------------------------------------------------- sample photos --
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "sample_photos"


def make_sample_photos(db, listing_id: str, categories: list[str]) -> None:
    """Attach the shipped illustrations (data/sample_photos, rendered by tools/render_sample_photos.py)
    to a demo listing. A stable variant per listing, so each flat looks different."""
    if list_photos(db, listing_id):
        return
    import hashlib
    v = int(hashlib.md5(listing_id.encode()).hexdigest(), 16)
    for i, cat in enumerate(categories):
        files = sorted(SAMPLE_DIR.glob(cat.lower().replace(" ", "_") + "_*.jpg"))
        if not files:
            continue
        img = Image.open(files[(v + i) % len(files)]).convert("RGB")
        _save(db, listing_id, img, cat)
