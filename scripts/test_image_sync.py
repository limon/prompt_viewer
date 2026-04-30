#!/usr/bin/env python3
"""Repeatable image sync checks using PNG files from ./test."""

from __future__ import annotations

import shutil
import sqlite3
import sys
import tempfile
import json
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
from fastapi.testclient import TestClient


TEST_ROOT = app.BASE_DIR / "test"


@dataclass(frozen=True)
class TestImages:
    comfyui: list[Path]
    chatgpt: list[Path]


def test_images() -> TestImages:
    images = sorted(TEST_ROOT.glob("*.png"))
    comfyui = [path for path in images if "comfyui" in path.name.lower()]
    chatgpt = [path for path in images if "chatgpt" in path.name.lower()]
    if len(comfyui) < 2:
        raise AssertionError("Expected at least two ComfyUI PNG files in ./test")
    if len(chatgpt) < 1:
        raise AssertionError("Expected at least one ChatGPT PNG file in ./test")
    return TestImages(comfyui=comfyui, chatgpt=chatgpt)


def reset_state() -> None:
    for db_file in (
        app.DB_PATH,
        app.DB_PATH.with_name(f"{app.DB_PATH.name}-wal"),
        app.DB_PATH.with_name(f"{app.DB_PATH.name}-shm"),
    ):
        try:
            db_file.unlink()
        except FileNotFoundError:
            pass

    app.init_dirs()
    for root in (app.COMFY_ROOT, app.CHATGPT_ROOT, app.GROK_ROOT, app.TRASH_ROOT):
        for path in root.rglob("*"):
            if path.is_file():
                path.unlink()
    for path in app.THUMB_ROOT.glob("*"):
        if path.is_file():
            path.unlink()

    app.init_db()


def copy_to_comfy(source: Path) -> Path:
    target = app.COMFY_ROOT / source.name
    shutil.copy2(source, target)
    return target


def copy_to_grok(source: Path) -> Path:
    target = app.GROK_ROOT / source.name
    shutil.copy2(source, target)
    return target


def db_paths() -> set[str]:
    with sqlite3.connect(app.DB_PATH) as conn:
        rows = conn.execute(
            "SELECT relative_path FROM images ORDER BY relative_path"
        ).fetchall()
    return {row[0] for row in rows}


def current_comfy_paths() -> set[str]:
    return {
        path.relative_to(app.PHOTO_ROOT).as_posix()
        for path in app.COMFY_ROOT.glob("*.png")
    }


def current_all_paths() -> set[str]:
    roots = (app.COMFY_ROOT, app.CHATGPT_ROOT, app.GROK_ROOT)
    return {
        path.relative_to(app.PHOTO_ROOT).as_posix()
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
    }


def assert_db_matches_files() -> None:
    expected = current_all_paths()
    actual = db_paths()
    if actual != expected:
        raise AssertionError(f"DB paths differ from files: actual={actual}, expected={expected}")


def assert_model_rows_exist() -> None:
    with sqlite3.connect(app.DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    if count <= 0:
        raise AssertionError("Expected parsed model rows")


def create_png(path: Path) -> None:
    from PIL import Image

    image = Image.new("RGB", (8, 8), (20, 120, 200))
    image.save(path, "PNG")


def create_jpeg(path: Path) -> None:
    from PIL import Image

    image = Image.new("RGB", (8, 8), (200, 80, 30))
    image.save(path, "JPEG", quality=90)


def insert_png_text(path: Path, key: str, value: str) -> None:
    data = path.read_bytes()
    payload = key.encode("latin-1") + b"\x00" + value.encode("utf-8")
    marker = data.rfind(app.pack_png_chunk(b"IEND", b""))
    if marker < 0:
        raise AssertionError("PNG IEND chunk not found")
    path.write_bytes(data[:marker] + app.pack_png_chunk(b"tEXt", payload) + data[marker:])


def jpeg_scan_data(path: Path) -> bytes:
    segments = app.jpeg_segments(path.read_bytes())
    for index, (marker, segment) in enumerate(segments):
        if marker == 0xDA:
            return segment + segments[index + 1][1]
    raise AssertionError("JPEG scan segment not found")


def test_new_image_addition(images: TestImages) -> None:
    reset_state()
    copy_to_comfy(images.comfyui[0])
    first = app.scan_photos()
    if first != {"scanned": 1, "deleted": 0}:
        raise AssertionError(f"Unexpected first scan result: {first}")

    copy_to_comfy(images.comfyui[1])
    second = app.scan_photos()
    if second != {"scanned": 2, "deleted": 0}:
        raise AssertionError(f"Unexpected second scan result: {second}")
    assert_db_matches_files()
    assert_model_rows_exist()


def test_old_image_deletion(images: TestImages) -> None:
    reset_state()
    first = copy_to_comfy(images.comfyui[0])
    copy_to_comfy(images.comfyui[1])
    scan = app.scan_photos()
    if scan != {"scanned": 2, "deleted": 0}:
        raise AssertionError(f"Unexpected setup scan result: {scan}")

    first.unlink()
    deleted = app.scan_photos()
    if deleted != {"scanned": 1, "deleted": 1}:
        raise AssertionError(f"Unexpected delete scan result: {deleted}")
    assert_db_matches_files()


def test_consistency_after_reset(images: TestImages) -> None:
    reset_state()
    for image in images.comfyui:
        copy_to_comfy(image)
    scan = app.scan_photos()
    if scan != {"scanned": len(images.comfyui), "deleted": 0}:
        raise AssertionError(f"Unexpected consistency scan result: {scan}")
    assert_db_matches_files()


def test_png_xmp_roundtrip_and_chunk_preservation(_images: TestImages) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "xmp.png"
        create_png(path)
        insert_png_text(path, "comfy-note", "keep me")
        app.write_xmp(
            path,
            "a quiet test prompt",
            "2026-04-01T12:34:56+08:00",
            "image2",
            "Quiet Title",
            source="chatgpt",
        )
        xmp = app.read_xmp(path)
        if xmp != {
            "title": "Quiet Title",
            "prompt": "a quiet test prompt",
            "generated_at": "2026-04-01T12:34:56+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Unexpected PNG XMP: {xmp}")
        app.write_xmp(path, title="Renamed Title")
        xmp = app.read_xmp(path)
        if xmp != {
            "title": "Renamed Title",
            "prompt": "a quiet test prompt",
            "generated_at": "2026-04-01T12:34:56+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Title-only XMP update did not preserve fields: {xmp}")
        app.write_xmp(path, prompt="updated prompt")
        xmp = app.read_xmp(path)
        if xmp != {
            "title": "Renamed Title",
            "prompt": "updated prompt",
            "generated_at": "2026-04-01T12:34:56+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Prompt-only XMP update did not preserve fields: {xmp}")
        app.write_xmp(path, source="comfyui")
        xmp = app.read_xmp(path)
        if xmp != {
            "title": "Renamed Title",
            "prompt": "updated prompt",
            "generated_at": "2026-04-01T12:34:56+08:00",
            "model": "image2",
            "source": "comfyui",
        }:
            raise AssertionError(f"Source-only XMP update did not preserve fields: {xmp}")
        chunks = app.read_png_text_chunks(path)
        if chunks.get("comfy-note") != "keep me":
            raise AssertionError("Non-XMP PNG text chunk was not preserved")


def test_chatgpt_filename_date_parser(_images: TestImages) -> None:
    parsed = app.parse_generated_at_from_filename(
        Path("ChatGPT Image 2026年4月22日 01_06_39.png")
    )
    if not parsed or not parsed.startswith("2026-04-22T01:06:39"):
        raise AssertionError(f"Unexpected parsed ChatGPT filename date: {parsed}")


def test_jpeg_xmp_roundtrip_without_reencode(_images: TestImages) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "xmp.jpg"
        create_jpeg(path)
        before = jpeg_scan_data(path)
        app.write_xmp(
            path,
            "jpeg prompt",
            "2026-04-02T12:34:56+08:00",
            "image2",
            "JPEG Title",
            source="chatgpt",
        )
        after = jpeg_scan_data(path)
        xmp = app.read_xmp(path)
        if xmp != {
            "title": "JPEG Title",
            "prompt": "jpeg prompt",
            "generated_at": "2026-04-02T12:34:56+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Unexpected JPEG XMP: {xmp}")
        if before != after:
            raise AssertionError("JPEG scan data changed; image was likely re-encoded")


def test_chatgpt_scan_restore_and_delete(_images: TestImages) -> None:
    reset_state()
    target = app.CHATGPT_ROOT / "20260403_010203_chatgpt.png"
    create_png(target)
    app.write_xmp(target, "scan prompt", "2026-04-03T01:02:03+08:00", "image2", "Scan Title")
    scan = app.scan_photos()
    if scan != {"scanned": 1, "deleted": 0}:
        raise AssertionError(f"Unexpected ChatGPT scan result: {scan}")
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM images").fetchone()
        model = conn.execute("SELECT model FROM models").fetchone()[0]
    if row["source"] != "chatgpt" or row["parser"] != "chatgpt_xmp":
        raise AssertionError(f"Unexpected ChatGPT row: {dict(row)}")
    if row["title"] != "Scan Title":
        raise AssertionError("ChatGPT title was not restored from XMP")
    if row["longest_prompt_text"] != "scan prompt":
        raise AssertionError("ChatGPT prompt was not restored from XMP")
    if row["generated_at"] != "2026-04-03T01:02:03+08:00" or model != "image2":
        raise AssertionError("ChatGPT generated_at/model were not restored from XMP")
    target.unlink()
    scan = app.scan_photos()
    if scan != {"scanned": 0, "deleted": 1}:
        raise AssertionError(f"Unexpected ChatGPT delete scan result: {scan}")


def test_grok_scan_restore_and_delete(_images: TestImages) -> None:
    reset_state()
    target = app.GROK_ROOT / "20260403_010203_grok.png"
    create_png(target)
    app.write_xmp(target, "grok prompt", "2026-04-03T01:02:03+08:00", None, "Grok Title", "grok")
    scan = app.scan_photos()
    if scan != {"scanned": 1, "deleted": 0}:
        raise AssertionError(f"Unexpected Grok scan result: {scan}")
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM images").fetchone()
        model = conn.execute("SELECT model FROM models").fetchone()[0]
    if row["source"] != "grok" or row["parser"] != "grok_xmp":
        raise AssertionError(f"Unexpected Grok row: {dict(row)}")
    if row["title"] != "Grok Title":
        raise AssertionError("Grok title was not restored from XMP")
    if row["longest_prompt_text"] != "grok prompt":
        raise AssertionError("Grok prompt was not restored from XMP")
    if row["generated_at"] != "2026-04-03T01:02:03+08:00" or model != "grok_imagine":
        raise AssertionError("Grok generated_at/model were not restored from XMP")
    target.unlink()
    scan = app.scan_photos()
    if scan != {"scanned": 0, "deleted": 1}:
        raise AssertionError(f"Unexpected Grok delete scan result: {scan}")


def test_chatgpt_upload_writes_xmp_and_overwrites(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "20260404_050607_upload.png"
        create_png(source)
        client = TestClient(app.app)
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "title": "Uploaded Title",
                    "prompt": "uploaded prompt",
                    "generated_at": "2026-04-04T05:06:07+08:00",
                    "model": "image2",
                }
            ]
        )
        for _ in range(2):
            response = client.post(
                "/api/uploads/chatgpt",
                files=[("files", (source.name, source.read_bytes(), "image/png"))],
                data={"metadata": metadata},
            )
            if response.status_code != 200:
                raise AssertionError(f"ChatGPT upload failed: {response.text}")
        target = app.CHATGPT_ROOT / source.name
        xmp = app.read_xmp(target)
        if xmp != {
            "title": "Uploaded Title",
            "prompt": "uploaded prompt",
            "generated_at": "2026-04-04T05:06:07+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Unexpected uploaded XMP: {xmp}")
        with sqlite3.connect(app.DB_PATH) as conn:
            count = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            row = conn.execute("SELECT title, longest_prompt_text FROM images").fetchone()
        if count != 1 or row[0] != "Uploaded Title" or row[1] != "uploaded prompt":
            raise AssertionError("ChatGPT same-name upload did not upsert correctly")


def test_grok_upload_writes_xmp_and_overwrites(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "20260404_050607_grok_upload.png"
        create_png(source)
        client = TestClient(app.app)
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "title": "Uploaded Grok Title",
                    "prompt": "uploaded grok prompt",
                    "generated_at": "2026-04-04T05:06:07+08:00",
                }
            ]
        )
        for _ in range(2):
            response = client.post(
                "/api/uploads/grok",
                files=[("files", (source.name, source.read_bytes(), "image/png"))],
                data={"metadata": metadata},
            )
            if response.status_code != 200:
                raise AssertionError(f"Grok upload failed: {response.text}")
        target = app.GROK_ROOT / source.name
        xmp = app.read_xmp(target)
        if xmp != {
            "title": "Uploaded Grok Title",
            "prompt": "uploaded grok prompt",
            "generated_at": "2026-04-04T05:06:07+08:00",
            "model": "grok_imagine",
            "source": "grok",
        }:
            raise AssertionError(f"Unexpected uploaded Grok XMP: {xmp}")
        with sqlite3.connect(app.DB_PATH) as conn:
            count = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
            row = conn.execute("SELECT source, title, longest_prompt_text FROM images").fetchone()
        if count != 1 or row[0] != "grok" or row[1] != "Uploaded Grok Title" or row[2] != "uploaded grok prompt":
            raise AssertionError("Grok same-name upload did not upsert correctly")


def test_grok_upload_accepts_jpeg_content_with_png_extension(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "grok-image-1.png"
        create_jpeg(source)
        client = TestClient(app.app)
        inspect = client.post(
            "/api/uploads/grok/inspect",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
        )
        if inspect.status_code != 200:
            raise AssertionError(f"Grok inspect failed for JPEG-content .png: {inspect.text}")
        if not inspect.json()["items"][0]["supported"]:
            raise AssertionError(f"Grok inspect unexpectedly rejected JPEG-content .png: {inspect.text}")
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "title": "JPEG Content Grok Title",
                    "prompt": "jpeg content grok prompt",
                }
            ]
        )
        response = client.post(
            "/api/uploads/grok",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 200:
            raise AssertionError(f"Grok upload failed for JPEG-content .png: {response.text}")
        target = app.GROK_ROOT / source.name
        xmp = app.read_xmp(target)
        if not xmp:
            raise AssertionError("JPEG-content .png upload did not write XMP")
        if xmp.get("title") != "JPEG Content Grok Title" or xmp.get("prompt") != "jpeg content grok prompt":
            raise AssertionError(f"Unexpected title/prompt for JPEG-content .png upload: {xmp}")
        if xmp.get("model") != "grok_imagine" or xmp.get("source") != "grok":
            raise AssertionError(f"Unexpected XMP for JPEG-content .png upload: {xmp}")
        if not xmp.get("generated_at"):
            raise AssertionError(f"JPEG-content .png upload did not set generated_at: {xmp}")
        if app.media_type_for_path(target) != "image/jpeg":
            raise AssertionError("JPEG-content .png should be served as image/jpeg")


def test_chatgpt_upload_existing_xmp_title_and_prompt_update(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "20260406_070809_existing.png"
        create_png(source)
        app.write_xmp(
            source,
            "existing prompt",
            "2026-04-06T07:08:09+08:00",
            "image2",
            "Existing Title",
        )
        client = TestClient(app.app)
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "title": "Upload Override Title",
                    "prompt": "edited upload prompt",
                    "generated_at": "2020-01-01T00:00:00+08:00",
                    "model": "ignored-model",
                }
            ]
        )
        response = client.post(
            "/api/uploads/chatgpt",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 200:
            raise AssertionError(f"ChatGPT existing-XMP upload failed: {response.text}")
        target = app.CHATGPT_ROOT / source.name
        xmp = app.read_xmp(target)
        if xmp != {
            "title": "Upload Override Title",
            "prompt": "edited upload prompt",
            "generated_at": "2026-04-06T07:08:09+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Existing-XMP upload did not apply prompt/title edits: {xmp}")


def test_grok_upload_existing_xmp_title_and_prompt_update(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "20260406_070809_existing_grok.png"
        create_png(source)
        app.write_xmp(
            source,
            "existing grok prompt",
            "2026-04-06T07:08:09+08:00",
            "grok_imagine",
            "Existing Grok Title",
            "grok",
        )
        client = TestClient(app.app)
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "title": "Upload Override Grok Title",
                    "prompt": "edited grok prompt",
                    "generated_at": "2020-01-01T00:00:00+08:00",
                    "model": "ignored-model",
                }
            ]
        )
        response = client.post(
            "/api/uploads/grok",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 200:
            raise AssertionError(f"Grok existing-XMP upload failed: {response.text}")
        target = app.GROK_ROOT / source.name
        xmp = app.read_xmp(target)
        if xmp != {
            "title": "Upload Override Grok Title",
            "prompt": "edited grok prompt",
            "generated_at": "2026-04-06T07:08:09+08:00",
            "model": "grok_imagine",
            "source": "grok",
        }:
            raise AssertionError(f"Grok existing-XMP upload did not apply prompt/title edits: {xmp}")


def test_chatgpt_upload_existing_xmp_title_only_preserves_prompt(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "20260406_070809_existing_title_only.png"
        create_png(source)
        app.write_xmp(
            source,
            "existing prompt",
            "2026-04-06T07:08:09+08:00",
            "image2",
            "Existing Title",
        )
        client = TestClient(app.app)
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "title": "Upload Override Title",
                    "generated_at": "2020-01-01T00:00:00+08:00",
                    "model": "ignored-model",
                }
            ]
        )
        response = client.post(
            "/api/uploads/chatgpt",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 200:
            raise AssertionError(f"ChatGPT existing-XMP title-only upload failed: {response.text}")
        target = app.CHATGPT_ROOT / source.name
        xmp = app.read_xmp(target)
        if xmp != {
            "title": "Upload Override Title",
            "prompt": "existing prompt",
            "generated_at": "2026-04-06T07:08:09+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Existing-XMP title-only upload did not preserve prompt: {xmp}")


def test_chatgpt_upload_allows_empty_prompt(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "20260407_080910_empty_prompt.png"
        create_png(source)
        client = TestClient(app.app)
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "title": "Empty Prompt Upload",
                    "prompt": "",
                    "generated_at": "2026-04-07T08:09:10+08:00",
                    "model": "image2",
                }
            ]
        )
        response = client.post(
            "/api/uploads/chatgpt",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 200:
            raise AssertionError(f"ChatGPT empty-prompt upload failed: {response.text}")
        target = app.CHATGPT_ROOT / source.name
        xmp = app.read_xmp(target)
        if xmp != {
            "title": "Empty Prompt Upload",
            "prompt": "",
            "generated_at": "2026-04-07T08:09:10+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Empty prompt was not preserved as empty string: {xmp}")
        with sqlite3.connect(app.DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT title, longest_prompt_text FROM images"
            ).fetchone()
        if row["title"] != "Empty Prompt Upload" or row["longest_prompt_text"] is not None:
            raise AssertionError(f"Empty prompt upload was indexed incorrectly: {dict(row)}")


def test_chatgpt_upload_uses_mtime_when_filename_has_no_date(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "upload_without_date.png"
        create_png(source)
        client = TestClient(app.app)
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "source": "chatgpt",
                    "title": "Mtime Upload",
                    "prompt": "mtime prompt",
                    "mtime": "2026-04-10T11:12:13+08:00",
                    "model": "image2",
                }
            ]
        )
        response = client.post(
            "/api/uploads/chatgpt",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 200:
            raise AssertionError(f"ChatGPT mtime upload failed: {response.text}")
        target = app.CHATGPT_ROOT / source.name
        xmp = app.read_xmp(target)
        if not xmp or xmp.get("generated_at") != "2026-04-10T11:12:13+08:00":
            raise AssertionError(f"Upload did not use supplied mtime fallback: {xmp}")


def test_chatgpt_upload_source_only_xmp_still_accepts_prompt(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "source_only_xmp.png"
        create_png(source)
        app.write_xmp(source, source="chatgpt")
        client = TestClient(app.app)
        inspect = client.post(
            "/api/uploads/chatgpt/inspect",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
        )
        if inspect.status_code != 200 or inspect.json()["items"][0]["has_xmp"]:
            raise AssertionError(f"Source-only XMP should not hide prompt input: {inspect.text}")
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "source": "chatgpt",
                    "title": "Source Only",
                    "prompt": "prompt after source-only",
                    "mtime": "2026-04-12T13:14:15+08:00",
                    "model": "image2",
                }
            ]
        )
        response = client.post(
            "/api/uploads/chatgpt",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 200:
            raise AssertionError(f"Source-only XMP upload failed: {response.text}")
        xmp = app.read_xmp(app.CHATGPT_ROOT / source.name)
        if not xmp or xmp.get("prompt") != "prompt after source-only":
            raise AssertionError(f"Source-only XMP upload did not write prompt: {xmp}")


def test_grok_upload_source_only_xmp_still_accepts_prompt(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "grok_source_only_xmp.png"
        create_png(source)
        app.write_xmp(source, source="grok")
        client = TestClient(app.app)
        inspect = client.post(
            "/api/uploads/grok/inspect",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
        )
        if inspect.status_code != 200 or inspect.json()["items"][0]["has_xmp"]:
            raise AssertionError(f"Grok source-only XMP should not hide prompt input: {inspect.text}")
        metadata = json.dumps(
            [
                {
                    "filename": source.name,
                    "source": "grok",
                    "title": "Grok Source Only",
                    "prompt": "grok prompt after source-only",
                    "mtime": "2026-04-12T13:14:15+08:00",
                }
            ]
        )
        response = client.post(
            "/api/uploads/grok",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 200:
            raise AssertionError(f"Grok source-only XMP upload failed: {response.text}")
        xmp = app.read_xmp(app.GROK_ROOT / source.name)
        if not xmp or xmp.get("prompt") != "grok prompt after source-only":
            raise AssertionError(f"Grok source-only XMP upload did not write prompt: {xmp}")


def test_upload_inspect_reads_xmp_defaults(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "inspect_defaults.png"
        create_png(source)
        app.write_xmp(
            source,
            "inspect prompt",
            "2026-04-20T10:11:12+08:00",
            "image2",
            "Inspect Title",
            "chatgpt",
        )
        client = TestClient(app.app)
        response = client.post(
            "/api/uploads/inspect",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
        )
        if response.status_code != 200:
            raise AssertionError(f"Upload inspect failed: {response.text}")
        item = response.json()["items"][0]
        if not item["supported"] or not item["has_xmp"]:
            raise AssertionError(f"Upload inspect did not detect embedded XMP: {item}")
        if item["metadata"] != {
            "title": "Inspect Title",
            "prompt": "inspect prompt",
            "generated_at": "2026-04-20T10:11:12+08:00",
            "model": "image2",
            "source": "chatgpt",
        }:
            raise AssertionError(f"Upload inspect returned wrong metadata: {item}")


def test_upload_rejects_invalid_source_before_save(_images: TestImages) -> None:
    reset_state()
    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "invalid_source.png"
        create_png(source)
        client = TestClient(app.app)
        metadata = json.dumps([{"filename": source.name, "source": "other"}])
        response = client.post(
            "/api/uploads/chatgpt",
            files=[("files", (source.name, source.read_bytes(), "image/png"))],
            data={"metadata": metadata},
        )
        if response.status_code != 400:
            raise AssertionError(f"Invalid upload source should fail: {response.text}")
        if (app.CHATGPT_ROOT / source.name).exists():
            raise AssertionError("Invalid source upload saved a file")


def test_chatgpt_named_fixture_upload(images: TestImages) -> None:
    reset_state()
    source = images.chatgpt[0]
    client = TestClient(app.app)
    metadata = json.dumps(
        [
            {
                "filename": source.name,
                "prompt": "fixture prompt",
                "model": "image2",
            }
        ]
    )
    response = client.post(
        "/api/uploads/chatgpt",
        files=[("files", (source.name, source.read_bytes(), "image/png"))],
        data={"metadata": metadata},
    )
    if response.status_code != 200:
        raise AssertionError(f"Named ChatGPT fixture upload failed: {response.text}")
    target = app.CHATGPT_ROOT / app.safe_upload_name(source.name)
    xmp = app.read_xmp(target)
    if not xmp or xmp.get("title") != target.name or xmp.get("prompt") != "fixture prompt":
        raise AssertionError(f"ChatGPT fixture upload did not write expected XMP: {xmp}")
    if not xmp.get("generated_at", "").startswith("2026-"):
        raise AssertionError(f"ChatGPT fixture date was not parsed from filename: {xmp}")
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT source, longest_prompt_text FROM images").fetchone()
    if row["source"] != "chatgpt" or row["longest_prompt_text"] != "fixture prompt":
        raise AssertionError(f"ChatGPT fixture upload was not indexed correctly: {dict(row)}")


def test_scan_writes_missing_source_and_uses_xmp_source(images: TestImages) -> None:
    reset_state()
    comfy = copy_to_comfy(images.comfyui[0])
    chatgpt_in_comfy = app.COMFY_ROOT / "manual-chatgpt-source.png"
    create_png(chatgpt_in_comfy)
    app.write_xmp(
        chatgpt_in_comfy,
        "manual prompt",
        "2026-04-11T12:13:14+08:00",
        "image2",
        "Manual ChatGPT Source",
        source="chatgpt",
    )
    scan = app.scan_photos()
    if scan != {"scanned": 2, "deleted": 0}:
        raise AssertionError(f"Unexpected source scan result: {scan}")
    comfy_xmp = app.read_xmp(comfy)
    if not comfy_xmp or comfy_xmp.get("source") != "comfyui":
        raise AssertionError(f"Scan did not write missing ComfyUI source: {comfy_xmp}")
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT source, parser, longest_prompt_text FROM images WHERE file_name = ?",
            (chatgpt_in_comfy.name,),
        ).fetchone()
    if (
        row["source"] != "chatgpt"
        or row["parser"] != "chatgpt_xmp"
        or row["longest_prompt_text"] != "manual prompt"
    ):
        raise AssertionError(f"XMP source was not used over folder source: {dict(row)}")


def test_comfyui_upload_parses_metadata(images: TestImages) -> None:
    reset_state()
    source = images.comfyui[0]
    client = TestClient(app.app)
    metadata = json.dumps(
        [
            {
                "filename": source.name,
                "source": "comfyui",
                "title": "Uploaded ComfyUI Title",
                "mtime": "2026-04-09T10:11:12+08:00",
            }
        ]
    )
    response = client.post(
        "/api/uploads/comfyui",
        files=[("files", (source.name, source.read_bytes(), "image/png"))],
        data={"metadata": metadata},
    )
    if response.status_code != 200:
        raise AssertionError(f"ComfyUI upload failed: {response.text}")
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT source, title, generated_at FROM images").fetchone()
        model_count = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    if (
        row["source"] != "comfyui"
        or row["title"] != "Uploaded ComfyUI Title"
        or row["generated_at"] != "2026-04-09T10:11:12+08:00"
        or model_count <= 0
    ):
        raise AssertionError("ComfyUI upload did not parse metadata")
    target = app.COMFY_ROOT / source.name
    xmp = app.read_xmp(target)
    if (
        not xmp
        or xmp.get("title") != "Uploaded ComfyUI Title"
        or xmp.get("source") != "comfyui"
        or xmp.get("generated_at") != "2026-04-09T10:11:12+08:00"
    ):
        raise AssertionError(f"ComfyUI upload did not write title XMP: {xmp}")


def test_comfyui_filename_date_parser(images: TestImages) -> None:
    reset_state()
    target = app.COMFY_ROOT / "ComfyUI 2026-04-08 09_10_11.png"
    shutil.copy2(images.comfyui[0], target)
    scan = app.scan_photos()
    if scan != {"scanned": 1, "deleted": 0}:
        raise AssertionError(f"Unexpected ComfyUI date scan result: {scan}")
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT source, generated_at FROM images"
        ).fetchone()
    if row["source"] != "comfyui" or not row["generated_at"].startswith("2026-04-08T09:10:11"):
        raise AssertionError(f"ComfyUI filename date was not parsed: {dict(row)}")


def test_default_title_and_title_search(images: TestImages) -> None:
    reset_state()
    source = copy_to_comfy(images.comfyui[0])
    app.scan_photos()
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id, file_name, title FROM images").fetchone()
    if row["title"] != source.name or row["file_name"] != source.name:
        raise AssertionError(f"Default title did not use filename: {dict(row)}")
    client = TestClient(app.app)
    response = client.patch(
        f"/api/images/{row['id']}/metadata",
        json={"title": "Searchable ComfyUI Title"},
    )
    if response.status_code != 200:
        raise AssertionError(f"ComfyUI title update failed: {response.text}")
    xmp = app.read_xmp(source)
    if not xmp or xmp.get("title") != "Searchable ComfyUI Title":
        raise AssertionError(f"ComfyUI title was not written to XMP: {xmp}")
    search = client.get("/api/images", params={"q": "Searchable ComfyUI"})
    if search.status_code != 200 or search.json()["total"] != 1:
        raise AssertionError(f"Title search did not find image: {search.text}")


def test_image_delete_api_removes_file_db_and_thumb(images: TestImages) -> None:
    reset_state()
    source = copy_to_comfy(images.comfyui[0])
    app.scan_photos()
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT id FROM images").fetchone()
    image_id = row["id"]
    thumb = app.thumb_path(image_id)
    if not thumb.is_file():
        raise AssertionError("Expected thumbnail to exist before delete")
    client = TestClient(app.app)
    response = client.delete(f"/api/images/{image_id}")
    if response.status_code != 200:
        raise AssertionError(f"Image delete failed: {response.text}")
    payload = response.json()
    if source.exists():
        raise AssertionError("Image file still exists in source folder after delete")
    trashed_path = payload.get("trashed_path")
    if not trashed_path:
        raise AssertionError(f"Delete response did not return trashed_path: {payload}")
    trash_target = app.PHOTO_ROOT / trashed_path
    if not trash_target.is_file():
        raise AssertionError(f"Image file was not moved to trash: {trash_target}")
    if thumb.exists():
        raise AssertionError("Thumbnail still exists after delete")
    with sqlite3.connect(app.DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    if count != 0:
        raise AssertionError("Image row still exists after delete")
    rescan = app.scan_photos()
    if rescan != {"scanned": 0, "deleted": 0}:
        raise AssertionError(f"Trash file should be ignored by scan: {rescan}")


def test_metadata_patch_chatgpt_grok_and_comfyui_prompt_rejection(images: TestImages) -> None:
    reset_state()
    chatgpt = app.CHATGPT_ROOT / "20260405_010203_chatgpt.png"
    create_png(chatgpt)
    app.write_xmp(
        chatgpt,
        "original prompt",
        "2026-04-05T01:02:03+08:00",
        "image2",
        "Original Title",
    )
    grok = app.GROK_ROOT / "20260405_020304_grok.png"
    create_png(grok)
    app.write_xmp(
        grok,
        "original grok prompt",
        "2026-04-05T02:03:04+08:00",
        "grok_imagine",
        "Original Grok Title",
        "grok",
    )
    comfy = copy_to_comfy(images.comfyui[0])
    app.scan_photos()

    client = TestClient(app.app)
    with sqlite3.connect(app.DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        chatgpt_row = conn.execute(
            "SELECT id FROM images WHERE source = ?", (app.SOURCE_CHATGPT,)
        ).fetchone()
        grok_row = conn.execute(
            "SELECT id FROM images WHERE source = ?", (app.SOURCE_GROK,)
        ).fetchone()
        comfy_row = conn.execute(
            "SELECT id FROM images WHERE source = ?", (app.SOURCE_COMFYUI,)
        ).fetchone()

    response = client.patch(
        f"/api/images/{chatgpt_row['id']}/metadata",
        json={"title": "Edited Title", "prompt": "edited prompt"},
    )
    if response.status_code != 200:
        raise AssertionError(f"ChatGPT metadata patch failed: {response.text}")
    data = response.json()
    if data["title"] != "Edited Title" or data["longest_prompt_detail"]["text"] != "edited prompt":
        raise AssertionError(f"ChatGPT metadata response was not updated: {data}")
    xmp = app.read_xmp(chatgpt)
    if xmp != {
        "title": "Edited Title",
        "prompt": "edited prompt",
        "generated_at": "2026-04-05T01:02:03+08:00",
        "model": "image2",
        "source": "chatgpt",
    }:
        raise AssertionError(f"ChatGPT metadata patch did not preserve XMP fields: {xmp}")

    response = client.patch(
        f"/api/images/{grok_row['id']}/metadata",
        json={"title": "Edited Grok Title", "prompt": "edited grok prompt"},
    )
    if response.status_code != 200:
        raise AssertionError(f"Grok metadata patch failed: {response.text}")
    data = response.json()
    if data["title"] != "Edited Grok Title" or data["longest_prompt_detail"]["text"] != "edited grok prompt":
        raise AssertionError(f"Grok metadata response was not updated: {data}")
    xmp = app.read_xmp(grok)
    if xmp != {
        "title": "Edited Grok Title",
        "prompt": "edited grok prompt",
        "generated_at": "2026-04-05T02:03:04+08:00",
        "model": "grok_imagine",
        "source": "grok",
    }:
        raise AssertionError(f"Grok metadata patch did not preserve XMP fields: {xmp}")

    response = client.patch(
        f"/api/images/{comfy_row['id']}/metadata",
        json={"prompt": "should be rejected"},
    )
    if response.status_code != 400:
        raise AssertionError(f"ComfyUI prompt patch should be rejected: {response.text}")
    xmp = app.read_xmp(comfy)
    if not xmp or xmp.get("source") != "comfyui" or xmp.get("prompt"):
        raise AssertionError(f"Rejected ComfyUI prompt patch modified XMP incorrectly: {xmp}")


def main() -> int:
    images = test_images()
    tests = (
        test_png_xmp_roundtrip_and_chunk_preservation,
        test_chatgpt_filename_date_parser,
        test_jpeg_xmp_roundtrip_without_reencode,
        test_new_image_addition,
        test_old_image_deletion,
        test_consistency_after_reset,
        test_chatgpt_scan_restore_and_delete,
        test_grok_scan_restore_and_delete,
        test_chatgpt_upload_writes_xmp_and_overwrites,
        test_grok_upload_writes_xmp_and_overwrites,
        test_grok_upload_accepts_jpeg_content_with_png_extension,
        test_chatgpt_upload_existing_xmp_title_and_prompt_update,
        test_grok_upload_existing_xmp_title_and_prompt_update,
        test_chatgpt_upload_existing_xmp_title_only_preserves_prompt,
        test_chatgpt_upload_allows_empty_prompt,
        test_chatgpt_upload_uses_mtime_when_filename_has_no_date,
        test_chatgpt_upload_source_only_xmp_still_accepts_prompt,
        test_grok_upload_source_only_xmp_still_accepts_prompt,
        test_upload_inspect_reads_xmp_defaults,
        test_upload_rejects_invalid_source_before_save,
        test_chatgpt_named_fixture_upload,
        test_scan_writes_missing_source_and_uses_xmp_source,
        test_comfyui_upload_parses_metadata,
        test_comfyui_filename_date_parser,
        test_default_title_and_title_search,
        test_image_delete_api_removes_file_db_and_thumb,
        test_metadata_patch_chatgpt_grok_and_comfyui_prompt_rejection,
    )
    try:
        for test in tests:
            test(images)
            print(f"PASS {test.__name__}")
    finally:
        reset_state()
        print("RESET photos/comfyui, photos/chatgpt, photos/grok, database, and thumbnails")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
