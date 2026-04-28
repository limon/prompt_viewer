#!/usr/bin/env python3
"""Single-service ComfyUI PNG gallery."""

from __future__ import annotations

import json
import mimetypes
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from comfy_png_summary import (
    extract_longest_prompt,
    extract_loras,
    extract_models,
    load_comfy_json,
    read_png_text_chunks,
)


BASE_DIR = Path(__file__).resolve().parent
PHOTO_ROOT = BASE_DIR / "photos"
COMFY_ROOT = PHOTO_ROOT / "comfyui"
THUMB_ROOT = BASE_DIR / ".prompt_viewer_thumbs"
DB_PATH = BASE_DIR / "prompt_viewer.sqlite3"
STATIC_ROOT = BASE_DIR / "static"
THUMB_MAX_EDGE = 480

SOURCE_COMFYUI = "comfyui"
PARSER_COMFY_PNG = "comfy_png_summary"

db_lock = threading.RLock()
scan_lock = threading.RLock()
pending_paths: set[Path] = set()
observer: Observer | None = None


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_dirs() -> None:
    PHOTO_ROOT.mkdir(exist_ok=True)
    COMFY_ROOT.mkdir(parents=True, exist_ok=True)
    THUMB_ROOT.mkdir(exist_ok=True)


def init_db() -> None:
    with db_lock, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE,
                absolute_path TEXT NOT NULL,
                parser TEXT,
                parse_status TEXT NOT NULL,
                parse_error TEXT,
                file_name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime REAL NOT NULL,
                width INTEGER,
                height INTEGER,
                longest_prompt_node TEXT,
                longest_prompt_class_type TEXT,
                longest_prompt_length INTEGER,
                longest_prompt_text TEXT,
                metadata_keys_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY,
                image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                node TEXT NOT NULL,
                class_type TEXT NOT NULL,
                field TEXT NOT NULL,
                model TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS loras (
                id INTEGER PRIMARY KEY,
                image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                node TEXT NOT NULL,
                class_type TEXT NOT NULL,
                field TEXT NOT NULL,
                lora TEXT NOT NULL,
                strength_json TEXT
            );

            CREATE TABLE IF NOT EXISTS raw_metadata (
                id INTEGER PRIMARY KEY,
                image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_images_source ON images(source);
            CREATE INDEX IF NOT EXISTS idx_images_mtime ON images(mtime DESC);
            CREATE INDEX IF NOT EXISTS idx_models_image_id ON models(image_id);
            CREATE INDEX IF NOT EXISTS idx_loras_image_id ON loras(image_id);
            CREATE INDEX IF NOT EXISTS idx_raw_metadata_image_id ON raw_metadata(image_id);
            """
        )


def path_relative_to_photos(path: Path) -> str:
    return path.resolve().relative_to(PHOTO_ROOT.resolve()).as_posix()


def is_supported_comfy_png(path: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved.relative_to(COMFY_ROOT.resolve())
    except ValueError:
        return False
    return resolved.is_file() and resolved.suffix.lower() == ".png"


def png_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def thumb_path(image_id: int) -> Path:
    return THUMB_ROOT / f"{image_id}.jpg"


def generate_thumbnail(path: Path, image_id: int) -> None:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Pillow is required to generate thumbnails") from exc

    target = thumb_path(image_id)
    tmp = target.with_suffix(".tmp.jpg")
    with Image.open(path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE))
        if image.mode not in {"RGB", "L"}:
            image = image.convert("RGB")
        image.save(tmp, "JPEG", quality=86, optimize=True)
    tmp.replace(target)


def delete_image(image_id: int) -> None:
    with db_lock, connect() as conn:
        conn.execute("DELETE FROM images WHERE id = ?", (image_id,))
    target = thumb_path(image_id)
    try:
        target.unlink()
    except FileNotFoundError:
        pass


def delete_image_by_path(path: Path) -> None:
    try:
        relative_path = path_relative_to_photos(path)
    except ValueError:
        return
    with db_lock, connect() as conn:
        row = conn.execute(
            "SELECT id FROM images WHERE relative_path = ?", (relative_path,)
        ).fetchone()
    if row:
        delete_image(int(row["id"]))


def parse_comfy_png(path: Path) -> dict[str, Any]:
    chunks = read_png_text_chunks(path)
    prompt = load_comfy_json(chunks, "prompt")
    if prompt is None:
        raise ValueError("No ComfyUI 'prompt' metadata chunk found in PNG.")
    workflow = load_comfy_json(chunks, "workflow")
    return {
        "metadata_keys": sorted(chunks),
        "models": extract_models(prompt),
        "loras": extract_loras(prompt, workflow),
        "longest_prompt": extract_longest_prompt(prompt),
        "raw_metadata": chunks,
    }


def upsert_image(path: Path) -> int | None:
    path = path.resolve()
    if not is_supported_comfy_png(path):
        return None

    stat = path.stat()
    width, height = png_dimensions(path)
    relative_path = path_relative_to_photos(path)
    file_name = path.name
    result: dict[str, Any] = {
        "metadata_keys": [],
        "models": [],
        "loras": [],
        "longest_prompt": None,
        "raw_metadata": {},
    }
    parse_status = "ok"
    parse_error = None

    try:
        result = parse_comfy_png(path)
    except Exception as exc:
        parse_status = "error"
        parse_error = str(exc)

    longest = result.get("longest_prompt") or {}
    with db_lock, connect() as conn:
        existing = conn.execute(
            "SELECT id FROM images WHERE relative_path = ?", (relative_path,)
        ).fetchone()
        if existing:
            image_id = int(existing["id"])
            conn.execute(
                """
                UPDATE images
                   SET source = ?,
                       absolute_path = ?,
                       parser = ?,
                       parse_status = ?,
                       parse_error = ?,
                       file_name = ?,
                       size_bytes = ?,
                       mtime = ?,
                       width = ?,
                       height = ?,
                       longest_prompt_node = ?,
                       longest_prompt_class_type = ?,
                       longest_prompt_length = ?,
                       longest_prompt_text = ?,
                       metadata_keys_json = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                """,
                (
                    SOURCE_COMFYUI,
                    str(path),
                    PARSER_COMFY_PNG,
                    parse_status,
                    parse_error,
                    file_name,
                    stat.st_size,
                    stat.st_mtime,
                    width,
                    height,
                    longest.get("node"),
                    longest.get("class_type"),
                    longest.get("length"),
                    longest.get("text"),
                    json.dumps(result.get("metadata_keys", []), ensure_ascii=False),
                    image_id,
                ),
            )
            conn.execute("DELETE FROM models WHERE image_id = ?", (image_id,))
            conn.execute("DELETE FROM loras WHERE image_id = ?", (image_id,))
            conn.execute("DELETE FROM raw_metadata WHERE image_id = ?", (image_id,))
        else:
            cursor = conn.execute(
                """
                INSERT INTO images (
                    source, relative_path, absolute_path, parser, parse_status,
                    parse_error, file_name, size_bytes, mtime, width, height,
                    longest_prompt_node, longest_prompt_class_type,
                    longest_prompt_length, longest_prompt_text, metadata_keys_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    SOURCE_COMFYUI,
                    relative_path,
                    str(path),
                    PARSER_COMFY_PNG,
                    parse_status,
                    parse_error,
                    file_name,
                    stat.st_size,
                    stat.st_mtime,
                    width,
                    height,
                    longest.get("node"),
                    longest.get("class_type"),
                    longest.get("length"),
                    longest.get("text"),
                    json.dumps(result.get("metadata_keys", []), ensure_ascii=False),
                ),
            )
            image_id = int(cursor.lastrowid)

        conn.executemany(
            """
            INSERT INTO models (image_id, node, class_type, field, model)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    image_id,
                    str(item.get("node", "")),
                    str(item.get("class_type", "")),
                    str(item.get("field", "")),
                    str(item.get("model", "")),
                )
                for item in result.get("models", [])
            ],
        )
        conn.executemany(
            """
            INSERT INTO loras (image_id, node, class_type, field, lora, strength_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    image_id,
                    str(item.get("node", "")),
                    str(item.get("class_type", "")),
                    str(item.get("field", "")),
                    str(item.get("lora", "")),
                    json.dumps(
                        {
                            key: value
                            for key, value in item.items()
                            if key
                            in {
                                "strength",
                                "strength_model",
                                "strength_clip",
                                "model_strength",
                                "clip_strength",
                            }
                        },
                        ensure_ascii=False,
                    ),
                )
                for item in result.get("loras", [])
            ],
        )
        conn.executemany(
            """
            INSERT INTO raw_metadata (image_id, key, value)
            VALUES (?, ?, ?)
            """,
            [
                (image_id, str(key), str(value))
                for key, value in result.get("raw_metadata", {}).items()
            ],
        )

    try:
        generate_thumbnail(path, image_id)
    except Exception:
        pass
    return image_id


def sync_deleted_images(current_paths: set[str]) -> int:
    deleted = 0
    with db_lock, connect() as conn:
        rows = conn.execute(
            "SELECT id, relative_path, absolute_path FROM images WHERE source = ?",
            (SOURCE_COMFYUI,),
        ).fetchall()
    for row in rows:
        path = Path(row["absolute_path"])
        if row["relative_path"] not in current_paths or not is_supported_comfy_png(path):
            delete_image(int(row["id"]))
            deleted += 1
    return deleted


def scan_photos() -> dict[str, int]:
    count = 0
    current_paths: set[str] = set()
    with scan_lock:
        for path in COMFY_ROOT.rglob("*.png"):
            current_paths.add(path_relative_to_photos(path))
            if upsert_image(path) is not None:
                count += 1
        deleted = sync_deleted_images(current_paths)
    return {"scanned": count, "deleted": deleted}


def wait_until_stable(path: Path, checks: int = 2, delay: float = 0.5) -> bool:
    last: tuple[int, float] | None = None
    stable = 0
    for _ in range(30):
        if not path.exists():
            return False
        stat = path.stat()
        current = (stat.st_size, stat.st_mtime)
        if current == last:
            stable += 1
            if stable >= checks:
                return True
        else:
            stable = 0
            last = current
        time.sleep(delay)
    return False


def schedule_parse(path: Path) -> None:
    path = path.resolve()
    if not is_supported_comfy_png(path):
        return
    with scan_lock:
        if path in pending_paths:
            return
        pending_paths.add(path)

    def worker() -> None:
        try:
            if wait_until_stable(path):
                upsert_image(path)
        finally:
            with scan_lock:
                pending_paths.discard(path)

    threading.Thread(target=worker, daemon=True).start()


class PhotoEventHandler(FileSystemEventHandler):
    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            schedule_parse(Path(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            schedule_parse(Path(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            delete_image_by_path(Path(event.src_path))
            schedule_parse(Path(event.dest_path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            delete_image_by_path(Path(event.src_path))


def start_observer() -> Observer:
    obs = Observer()
    obs.schedule(PhotoEventHandler(), str(PHOTO_ROOT), recursive=True)
    obs.start()
    return obs


def row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "source": row["source"],
        "relative_path": row["relative_path"],
        "file_name": row["file_name"],
        "parse_status": row["parse_status"],
        "parse_error": row["parse_error"],
        "size_bytes": row["size_bytes"],
        "mtime": row["mtime"],
        "width": row["width"],
        "height": row["height"],
        "longest_prompt": row["longest_prompt_text"],
        "metadata_keys": json.loads(row["metadata_keys_json"] or "[]"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "thumb_url": f"/thumbs/{row['id']}",
        "media_url": f"/media/{row['id']}",
    }


def fetch_image_row(image_id: int) -> sqlite3.Row:
    with db_lock, connect() as conn:
        row = conn.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Image not found")
    return row


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global observer
    init_dirs()
    init_db()
    scan_photos()
    observer = start_observer()
    try:
        yield
    finally:
        if observer:
            observer.stop()
            observer.join(timeout=5)


app = FastAPI(title="Prompt Viewer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/image/{image_id}")
def image_page(image_id: int) -> FileResponse:
    return FileResponse(STATIC_ROOT / "image.html")


@app.get("/api/images")
def list_images(
    q: str = "",
    source: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(48, ge=1, le=200),
) -> dict[str, Any]:
    clauses = []
    params: list[Any] = []
    if source:
        clauses.append("images.source = ?")
        params.append(source)
    if q:
        like = f"%{q}%"
        clauses.append(
            """
            (
                images.file_name LIKE ?
                OR images.relative_path LIKE ?
                OR images.source LIKE ?
                OR images.longest_prompt_text LIKE ?
                OR EXISTS (
                    SELECT 1 FROM models
                     WHERE models.image_id = images.id AND models.model LIKE ?
                )
                OR EXISTS (
                    SELECT 1 FROM loras
                     WHERE loras.image_id = images.id AND loras.lora LIKE ?
                )
            )
            """
        )
        params.extend([like, like, like, like, like, like])

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    offset = (page - 1) * per_page
    with db_lock, connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS count FROM images {where_sql}", params
        ).fetchone()["count"]
        rows = conn.execute(
            f"""
            SELECT * FROM images
            {where_sql}
            ORDER BY mtime DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, offset],
        ).fetchall()
    return {
        "items": [row_to_summary(row) for row in rows],
        "page": page,
        "per_page": per_page,
        "total": total,
    }


@app.get("/api/images/{image_id}")
def image_detail(image_id: int) -> dict[str, Any]:
    row = fetch_image_row(image_id)
    with db_lock, connect() as conn:
        models = [
            dict(item)
            for item in conn.execute(
                "SELECT node, class_type, field, model FROM models WHERE image_id = ?",
                (image_id,),
            ).fetchall()
        ]
        loras = []
        for item in conn.execute(
            "SELECT node, class_type, field, lora, strength_json FROM loras WHERE image_id = ?",
            (image_id,),
        ).fetchall():
            lora = dict(item)
            lora["strengths"] = json.loads(lora.pop("strength_json") or "{}")
            loras.append(lora)
        raw_metadata = {
            item["key"]: item["value"]
            for item in conn.execute(
                "SELECT key, value FROM raw_metadata WHERE image_id = ?",
                (image_id,),
            ).fetchall()
        }

    detail = row_to_summary(row)
    detail["absolute_path"] = row["absolute_path"]
    detail["parser"] = row["parser"]
    detail["longest_prompt_detail"] = {
        "node": row["longest_prompt_node"],
        "class_type": row["longest_prompt_class_type"],
        "length": row["longest_prompt_length"],
        "text": row["longest_prompt_text"],
    }
    detail["models"] = models
    detail["loras"] = loras
    detail["raw_metadata"] = raw_metadata
    return detail


@app.get("/media/{image_id}")
def media(image_id: int) -> FileResponse:
    row = fetch_image_row(image_id)
    path = Path(row["absolute_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/png")


@app.get("/thumbs/{image_id}")
def thumbs(image_id: int) -> FileResponse:
    row = fetch_image_row(image_id)
    path = Path(row["absolute_path"])
    target = thumb_path(image_id)
    if not target.is_file():
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File missing on disk")
        try:
            generate_thumbnail(path, image_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(target, media_type="image/jpeg")


@app.post("/api/rescan")
def rescan() -> dict[str, Any]:
    return scan_photos()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8888, reload=False)
