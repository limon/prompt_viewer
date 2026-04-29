#!/usr/bin/env python3
"""Single-service ComfyUI and ChatGPT image gallery."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import sqlite3
import struct
import threading
import time
import zlib
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
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
PHOTO_ROOT = Path(os.environ.get("PROMPT_VIEWER_PHOTO_ROOT", BASE_DIR / "photos")).resolve()
COMFY_ROOT = PHOTO_ROOT / "comfyui"
CHATGPT_ROOT = PHOTO_ROOT / "chatgpt"
THUMB_ROOT = Path(
    os.environ.get("PROMPT_VIEWER_THUMB_ROOT", BASE_DIR / ".prompt_viewer_thumbs")
).resolve()
DB_PATH = Path(
    os.environ.get("PROMPT_VIEWER_DB_PATH", BASE_DIR / "prompt_viewer.sqlite3")
).resolve()
STATIC_ROOT = BASE_DIR / "static"
THUMB_MAX_EDGE = 480

SOURCE_COMFYUI = "comfyui"
SOURCE_CHATGPT = "chatgpt"
PARSER_COMFY_PNG = "comfy_png_summary"
PARSER_CHATGPT_XMP = "chatgpt_xmp"
DEFAULT_CHATGPT_MODEL = "image2"

XMP_PACKET_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_XMP_KEY = "XML:com.adobe.xmp"
NS_DC = "http://purl.org/dc/elements/1.1/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_XMP = "http://ns.adobe.com/xap/1.0/"
NS_PV = "https://prompt-viewer.local/ns/1.0/"
DATE_RE = re.compile(
    r"(?P<year>20\d{2})\D*(?P<month>\d{1,2})\D*(?P<day>\d{1,2})"
    r"(?:\D+(?P<hour>\d{1,2})\D*(?P<minute>\d{1,2})\D*(?P<second>\d{1,2}))?"
)

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
    CHATGPT_ROOT.mkdir(parents=True, exist_ok=True)
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
                title TEXT,
                size_bytes INTEGER NOT NULL,
                mtime REAL NOT NULL,
                width INTEGER,
                height INTEGER,
                longest_prompt_node TEXT,
                longest_prompt_class_type TEXT,
                longest_prompt_length INTEGER,
                longest_prompt_text TEXT,
                generated_at TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_images_generated_at ON images(generated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_images_mtime ON images(mtime DESC);
            CREATE INDEX IF NOT EXISTS idx_models_image_id ON models(image_id);
            CREATE INDEX IF NOT EXISTS idx_loras_image_id ON loras(image_id);
            CREATE INDEX IF NOT EXISTS idx_raw_metadata_image_id ON raw_metadata(image_id);
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(images)").fetchall()
        }
        if "generated_at" not in columns:
            conn.execute("ALTER TABLE images ADD COLUMN generated_at TEXT")
        if "title" not in columns:
            conn.execute("ALTER TABLE images ADD COLUMN title TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_images_generated_at ON images(generated_at DESC)"
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


def is_supported_chatgpt_image(path: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved.relative_to(CHATGPT_ROOT.resolve())
    except ValueError:
        return False
    return resolved.is_file() and resolved.suffix.lower() in {".png", ".jpg", ".jpeg"}


def is_supported_image(path: Path) -> bool:
    return is_supported_comfy_png(path) or is_supported_chatgpt_image(path)


def image_dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return image.size
    except Exception:
        return None, None


def iso_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def parse_generated_at_from_filename(path: Path) -> str | None:
    match = DATE_RE.search(path.stem)
    if not match:
        return None
    try:
        parsed = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour") or 0),
            int(match.group("minute") or 0),
            int(match.group("second") or 0),
        )
    except ValueError:
        return None
    return parsed.astimezone().isoformat(timespec="seconds")


def fallback_generated_at(path: Path) -> str:
    return parse_generated_at_from_filename(path) or iso_from_timestamp(path.stat().st_mtime)


def safe_upload_name(filename: str) -> str:
    name = Path(filename or "upload").name
    sanitized = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    if not sanitized:
        sanitized = "upload"
    return sanitized


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


def parse_xmp_xml(xml_text: str) -> dict[str, str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    def first_text(paths: list[str]) -> str:
        for path in paths:
            element = root.find(path)
            if element is not None and element.text:
                return element.text.strip()
        return ""

    prompt = first_text(
        [
            f".//{{{NS_PV}}}Prompt",
            f".//{{{NS_DC}}}description/{{{NS_RDF}}}Alt/{{{NS_RDF}}}li",
            f".//{{{NS_DC}}}description",
        ]
    )
    title = first_text(
        [
            f".//{{{NS_PV}}}Title",
            f".//{{{NS_DC}}}title/{{{NS_RDF}}}Alt/{{{NS_RDF}}}li",
            f".//{{{NS_DC}}}title",
        ]
    )
    generated_at = first_text(
        [
            f".//{{{NS_PV}}}GeneratedAt",
            f".//{{{NS_XMP}}}CreateDate",
        ]
    )
    model = first_text([f".//{{{NS_PV}}}Model"])
    return {
        "title": title,
        "prompt": prompt,
        "generated_at": generated_at,
        "model": model,
    }


def add_rdf_alt(parent: ET.Element, tag: str, value: str) -> None:
    container = ET.SubElement(parent, tag)
    alt = ET.SubElement(container, f"{{{NS_RDF}}}Alt")
    li = ET.SubElement(alt, f"{{{NS_RDF}}}li")
    li.set("{http://www.w3.org/XML/1998/namespace}lang", "x-default")
    li.text = value


def build_xmp_xml(
    prompt: str = "",
    generated_at: str = "",
    model: str = "",
    title: str = "",
) -> str:
    ET.register_namespace("x", "adobe:ns:meta/")
    ET.register_namespace("rdf", NS_RDF)
    ET.register_namespace("dc", NS_DC)
    ET.register_namespace("xmp", NS_XMP)
    ET.register_namespace("pv", NS_PV)

    xmpmeta = ET.Element("{adobe:ns:meta/}xmpmeta")
    rdf = ET.SubElement(xmpmeta, f"{{{NS_RDF}}}RDF")
    description = ET.SubElement(rdf, f"{{{NS_RDF}}}Description")
    description.set(f"{{{NS_RDF}}}about", "")
    if title:
        ET.SubElement(description, f"{{{NS_PV}}}Title").text = title
        add_rdf_alt(description, f"{{{NS_DC}}}title", title)
    if prompt:
        ET.SubElement(description, f"{{{NS_PV}}}Prompt").text = prompt
        add_rdf_alt(description, f"{{{NS_DC}}}description", prompt)
    if generated_at:
        ET.SubElement(description, f"{{{NS_PV}}}GeneratedAt").text = generated_at
        ET.SubElement(description, f"{{{NS_XMP}}}CreateDate").text = generated_at
    if model:
        ET.SubElement(description, f"{{{NS_PV}}}Model").text = model
    xml = ET.tostring(xmpmeta, encoding="utf-8", xml_declaration=False).decode("utf-8")
    return f'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>\n{xml}\n<?xpacket end="w"?>'


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("Not a PNG file")
    chunks: list[tuple[bytes, bytes]] = []
    offset = 8
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        ctype = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        chunks.append((ctype, payload))
        offset += 12 + length
        if ctype == b"IEND":
            break
    return chunks


def read_png_xmp(path: Path) -> str | None:
    data = path.read_bytes()
    for ctype, payload in png_chunks(data):
        if ctype != b"iTXt":
            continue
        parts = payload.split(b"\x00", 5)
        if len(parts) != 6:
            continue
        key, compressed, method, _lang, _translated, text = parts
        if key.decode("utf-8", errors="replace") != PNG_XMP_KEY:
            continue
        if compressed == b"\x01" and method == b"\x00":
            text = zlib.decompress(text)
        return text.decode("utf-8", errors="replace")
    return None


def write_png_xmp(path: Path, xml_text: str) -> None:
    data = path.read_bytes()
    chunks = png_chunks(data)
    payload = PNG_XMP_KEY.encode("utf-8") + b"\x00\x00\x00\x00\x00" + xml_text.encode(
        "utf-8"
    )
    output = bytearray(PNG_SIGNATURE)
    wrote = False
    for ctype, chunk_payload in chunks:
        if ctype == b"iTXt":
            key = chunk_payload.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            if key == PNG_XMP_KEY:
                continue
        if ctype == b"IEND" and not wrote:
            output.extend(pack_png_chunk(b"iTXt", payload))
            wrote = True
        output.extend(pack_png_chunk(ctype, chunk_payload))
    path.write_bytes(bytes(output))


def pack_png_chunk(ctype: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + ctype
        + payload
        + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
    )


def jpeg_segments(data: bytes) -> list[tuple[int | None, bytes]]:
    if data[:2] != b"\xff\xd8":
        raise ValueError("Not a JPEG file")
    segments: list[tuple[int | None, bytes]] = [(None, b"\xff\xd8")]
    offset = 2
    while offset < len(data):
        if data[offset] != 0xFF:
            segments.append((None, data[offset:]))
            break
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            segments.append((marker, bytes([0xFF, marker])))
            continue
        if offset + 2 > len(data):
            break
        length = struct.unpack(">H", data[offset : offset + 2])[0]
        start = offset - 2
        end = offset + length
        segment = data[start:end]
        segments.append((marker, segment))
        offset = end
        if marker == 0xDA:
            segments.append((None, data[offset:]))
            break
    return segments


def read_jpeg_xmp(path: Path) -> str | None:
    for marker, segment in jpeg_segments(path.read_bytes()):
        if marker == 0xE1:
            payload = segment[4:]
            if payload.startswith(XMP_PACKET_HEADER):
                return payload[len(XMP_PACKET_HEADER) :].decode("utf-8", errors="replace")
    return None


def write_jpeg_xmp(path: Path, xml_text: str) -> None:
    data = path.read_bytes()
    payload = XMP_PACKET_HEADER + xml_text.encode("utf-8")
    if len(payload) + 2 > 65535:
        raise ValueError("XMP packet is too large for a JPEG APP1 segment")
    xmp_segment = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    output = bytearray()
    inserted = False
    for index, (marker, segment) in enumerate(jpeg_segments(data)):
        if index == 0:
            output.extend(segment)
            continue
        if marker == 0xE1 and segment[4:].startswith(XMP_PACKET_HEADER):
            if not inserted:
                output.extend(xmp_segment)
                inserted = True
            continue
        if not inserted and marker not in {0xE0, 0xE1, 0xE2, 0xEE}:
            output.extend(xmp_segment)
            inserted = True
        output.extend(segment)
    if not inserted:
        output.extend(xmp_segment)
    path.write_bytes(bytes(output))


def read_xmp(path: Path) -> dict[str, str] | None:
    suffix = path.suffix.lower()
    xml_text = None
    if suffix == ".png":
        xml_text = read_png_xmp(path)
    elif suffix in {".jpg", ".jpeg"}:
        xml_text = read_jpeg_xmp(path)
    if not xml_text:
        return None
    parsed = parse_xmp_xml(xml_text)
    return parsed if any(parsed.values()) else None


def write_xmp(
    path: Path,
    prompt: str | None = None,
    generated_at: str | None = None,
    model: str | None = None,
    title: str | None = None,
) -> None:
    existing = read_xmp(path) or {}
    fields = {
        "prompt": existing.get("prompt", ""),
        "generated_at": existing.get("generated_at", ""),
        "model": existing.get("model", ""),
        "title": existing.get("title", ""),
    }
    if prompt is not None:
        fields["prompt"] = prompt
    if generated_at is not None:
        fields["generated_at"] = generated_at
    if model is not None:
        fields["model"] = model
    if title is not None:
        fields["title"] = title
    xml_text = build_xmp_xml(**fields)
    suffix = path.suffix.lower()
    if suffix == ".png":
        write_png_xmp(path, xml_text)
    elif suffix in {".jpg", ".jpeg"}:
        write_jpeg_xmp(path, xml_text)
    else:
        raise ValueError(f"Unsupported XMP image type: {suffix}")


def parse_comfy_png(path: Path) -> dict[str, Any]:
    chunks = read_png_text_chunks(path)
    xmp = read_xmp(path) or {}
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
        "title": xmp.get("title") or path.name,
    }


def parse_chatgpt_image(path: Path) -> dict[str, Any]:
    xmp = read_xmp(path) or {}
    title = xmp.get("title") or path.name
    prompt = xmp.get("prompt", "")
    generated_at = xmp.get("generated_at") or fallback_generated_at(path)
    model = xmp.get("model") or DEFAULT_CHATGPT_MODEL
    metadata_keys = [
        key
        for key, value in {
            "pv:Prompt": prompt,
            "pv:GeneratedAt": generated_at,
            "pv:Model": model,
            "pv:Title": title,
        }.items()
        if value
    ]
    return {
        "metadata_keys": metadata_keys,
        "models": [
            {
                "node": "xmp",
                "class_type": "ChatGPT",
                "field": "model",
                "model": model,
            }
        ]
        if model
        else [],
        "loras": [],
        "longest_prompt": {
            "node": "xmp",
            "class_type": "ChatGPT",
            "length": len(prompt),
            "text": prompt,
        }
        if prompt
        else None,
        "raw_metadata": {
            "title": title,
            "prompt": prompt,
            "generated_at": generated_at,
            "model": model,
        },
        "generated_at": generated_at,
        "title": title,
    }


def image_source_and_parser(path: Path) -> tuple[str, str] | None:
    if is_supported_comfy_png(path):
        return SOURCE_COMFYUI, PARSER_COMFY_PNG
    if is_supported_chatgpt_image(path):
        return SOURCE_CHATGPT, PARSER_CHATGPT_XMP
    return None


def parse_image_metadata(path: Path, source: str) -> dict[str, Any]:
    if source == SOURCE_COMFYUI:
        return parse_comfy_png(path)
    if source == SOURCE_CHATGPT:
        return parse_chatgpt_image(path)
    raise ValueError(f"Unsupported source: {source}")


def upsert_image(path: Path) -> int | None:
    path = path.resolve()
    source_parser = image_source_and_parser(path)
    if source_parser is None:
        return None
    source, parser = source_parser

    stat = path.stat()
    width, height = image_dimensions(path)
    relative_path = path_relative_to_photos(path)
    file_name = path.name
    result: dict[str, Any] = {
        "metadata_keys": [],
        "models": [],
        "loras": [],
        "longest_prompt": None,
        "raw_metadata": {},
        "generated_at": None,
        "title": path.name,
    }
    parse_status = "ok"
    parse_error = None

    try:
        result = parse_image_metadata(path, source)
    except Exception as exc:
        parse_status = "error"
        parse_error = str(exc)

    longest = result.get("longest_prompt") or {}
    generated_at = result.get("generated_at")
    title = str(result.get("title") or file_name)
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
                       title = ?,
                       size_bytes = ?,
                       mtime = ?,
                       width = ?,
                       height = ?,
                       longest_prompt_node = ?,
                       longest_prompt_class_type = ?,
                       longest_prompt_length = ?,
                       longest_prompt_text = ?,
                       generated_at = ?,
                       metadata_keys_json = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                """,
                (
                    source,
                    str(path),
                    parser,
                    parse_status,
                    parse_error,
                    file_name,
                    title,
                    stat.st_size,
                    stat.st_mtime,
                    width,
                    height,
                    longest.get("node"),
                    longest.get("class_type"),
                    longest.get("length"),
                    longest.get("text"),
                    generated_at,
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
                    parse_error, file_name, title, size_bytes, mtime, width, height,
                    longest_prompt_node, longest_prompt_class_type,
                    longest_prompt_length, longest_prompt_text, generated_at,
                    metadata_keys_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    relative_path,
                    str(path),
                    parser,
                    parse_status,
                    parse_error,
                    file_name,
                    title,
                    stat.st_size,
                    stat.st_mtime,
                    width,
                    height,
                    longest.get("node"),
                    longest.get("class_type"),
                    longest.get("length"),
                    longest.get("text"),
                    generated_at,
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


def sync_deleted_images(current_paths: set[str], source: str) -> int:
    deleted = 0
    with db_lock, connect() as conn:
        rows = conn.execute(
            "SELECT id, relative_path, absolute_path FROM images WHERE source = ?",
            (source,),
        ).fetchall()
    for row in rows:
        path = Path(row["absolute_path"])
        if row["relative_path"] not in current_paths or not is_supported_image(path):
            delete_image(int(row["id"]))
            deleted += 1
    return deleted


def scan_photos() -> dict[str, int]:
    count = 0
    current_by_source: dict[str, set[str]] = {
        SOURCE_COMFYUI: set(),
        SOURCE_CHATGPT: set(),
    }
    with scan_lock:
        for path in COMFY_ROOT.rglob("*.png"):
            current_by_source[SOURCE_COMFYUI].add(path_relative_to_photos(path))
            if upsert_image(path) is not None:
                count += 1
        for pattern in ("*.png", "*.jpg", "*.jpeg"):
            for path in CHATGPT_ROOT.rglob(pattern):
                current_by_source[SOURCE_CHATGPT].add(path_relative_to_photos(path))
                if upsert_image(path) is not None:
                    count += 1
        deleted = sum(
            sync_deleted_images(paths, source)
            for source, paths in current_by_source.items()
        )
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
    if not is_supported_image(path):
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
        "title": row["title"] or row["file_name"],
        "parse_status": row["parse_status"],
        "parse_error": row["parse_error"],
        "size_bytes": row["size_bytes"],
        "mtime": row["mtime"],
        "generated_at": row["generated_at"],
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


async def save_upload_file(upload: UploadFile, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.uploading")
    try:
        with tmp.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        tmp.replace(target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def parse_upload_metadata(metadata: str | None) -> dict[str, dict[str, str]]:
    if not metadata:
        return {}
    try:
        raw = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="metadata must be a JSON list")
    result: dict[str, dict[str, str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        filename = safe_upload_name(str(item.get("filename", "")))
        result[filename] = {
            "title": str(item.get("title") or ""),
            "prompt": str(item.get("prompt") or ""),
            "generated_at": str(item.get("generated_at") or ""),
            "model": str(item.get("model") or DEFAULT_CHATGPT_MODEL),
        }
    return result


def upload_result(path: Path, image_id: int | None) -> dict[str, Any]:
    return {
        "file_name": path.name,
        "relative_path": path_relative_to_photos(path),
        "image_id": image_id,
        "thumb_url": f"/thumbs/{image_id}" if image_id else None,
        "media_url": f"/media/{image_id}" if image_id else None,
    }


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


@app.post("/api/uploads/chatgpt/inspect")
async def inspect_chatgpt_uploads(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    items = []
    for upload in files:
        name = safe_upload_name(upload.filename or "")
        suffix = Path(name).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg"}:
            items.append(
                {
                    "file_name": name,
                    "supported": False,
                    "has_xmp": False,
                    "metadata": {},
                    "error": "ChatGPT uploads support PNG and JPEG files.",
                }
            )
            continue
        tmp = CHATGPT_ROOT / f".inspect-{time.time_ns()}-{name}"
        await save_upload_file(upload, tmp)
        try:
            metadata = read_xmp(tmp) or {}
            items.append(
                {
                    "file_name": name,
                    "supported": True,
                    "has_xmp": bool(metadata),
                    "metadata": metadata,
                    "generated_at_fallback": fallback_generated_at(tmp),
                }
            )
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    return {"items": items}


@app.post("/api/uploads/comfyui")
async def upload_comfyui(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    items = []
    for upload in files:
        name = safe_upload_name(upload.filename or "")
        if Path(name).suffix.lower() != ".png":
            raise HTTPException(status_code=400, detail=f"{name} is not a PNG file")
        target = COMFY_ROOT / name
        await save_upload_file(upload, target)
        image_id = upsert_image(target)
        items.append(upload_result(target, image_id))
    return {"items": items}


@app.post("/api/uploads/chatgpt")
async def upload_chatgpt(
    files: list[UploadFile] = File(...),
    metadata: str | None = Form(None),
) -> dict[str, Any]:
    submitted = parse_upload_metadata(metadata)
    items = []
    for upload in files:
        name = safe_upload_name(upload.filename or "")
        suffix = Path(name).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg"}:
            raise HTTPException(status_code=400, detail=f"{name} is not a PNG/JPEG file")
        target = CHATGPT_ROOT / name
        await save_upload_file(upload, target)
        existing_xmp = read_xmp(target)
        supplied = submitted.get(name, {})
        title = supplied.get("title", "").strip()
        if not existing_xmp:
            prompt = supplied.get("prompt", "")
            generated_at = supplied.get("generated_at") or fallback_generated_at(target)
            model = supplied.get("model") or DEFAULT_CHATGPT_MODEL
            write_xmp(target, prompt, generated_at, model, title or name)
        elif title:
            write_xmp(target, title=title)
        image_id = upsert_image(target)
        items.append(upload_result(target, image_id))
    return {"items": items}


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
                OR images.title LIKE ?
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
        params.extend([like, like, like, like, like, like, like])

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
            ORDER BY COALESCE(generated_at, '') DESC, mtime DESC, id DESC
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


@app.patch("/api/images/{image_id}/metadata")
def update_image_metadata(
    image_id: int,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    row = fetch_image_row(image_id)
    path = Path(row["absolute_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing on disk")

    unknown = set(payload) - {"title", "prompt"}
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported metadata fields: {', '.join(sorted(unknown))}",
        )
    if "prompt" in payload and row["source"] != SOURCE_CHATGPT:
        raise HTTPException(
            status_code=400,
            detail="Prompt edits are only supported for ChatGPT images",
        )

    title = None
    if "title" in payload:
        title = str(payload.get("title") or "").strip() or row["file_name"]

    prompt = None
    if "prompt" in payload:
        prompt = str(payload.get("prompt") or "")

    try:
        write_xmp(path, title=title, prompt=prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    upsert_image(path)
    return image_detail(image_id)


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
