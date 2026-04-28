#!/usr/bin/env python3
"""Repeatable image sync checks using PNG files from ./test."""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


TEST_ROOT = app.BASE_DIR / "test"


def test_images() -> list[Path]:
    images = sorted(TEST_ROOT.glob("*.png"))
    if len(images) < 2:
        raise AssertionError("Expected at least two PNG files in ./test")
    return images


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
    for path in app.COMFY_ROOT.glob("*.png"):
        path.unlink()
    for path in app.THUMB_ROOT.glob("*"):
        if path.is_file():
            path.unlink()

    app.init_db()


def copy_to_comfy(source: Path) -> Path:
    target = app.COMFY_ROOT / source.name
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


def assert_db_matches_files() -> None:
    expected = current_comfy_paths()
    actual = db_paths()
    if actual != expected:
        raise AssertionError(f"DB paths differ from files: actual={actual}, expected={expected}")


def assert_model_rows_exist() -> None:
    with sqlite3.connect(app.DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
    if count <= 0:
        raise AssertionError("Expected parsed model rows")


def test_new_image_addition(images: list[Path]) -> None:
    reset_state()
    copy_to_comfy(images[0])
    first = app.scan_photos()
    if first != {"scanned": 1, "deleted": 0}:
        raise AssertionError(f"Unexpected first scan result: {first}")

    copy_to_comfy(images[1])
    second = app.scan_photos()
    if second != {"scanned": 2, "deleted": 0}:
        raise AssertionError(f"Unexpected second scan result: {second}")
    assert_db_matches_files()
    assert_model_rows_exist()


def test_old_image_deletion(images: list[Path]) -> None:
    reset_state()
    first = copy_to_comfy(images[0])
    copy_to_comfy(images[1])
    scan = app.scan_photos()
    if scan != {"scanned": 2, "deleted": 0}:
        raise AssertionError(f"Unexpected setup scan result: {scan}")

    first.unlink()
    deleted = app.scan_photos()
    if deleted != {"scanned": 1, "deleted": 1}:
        raise AssertionError(f"Unexpected delete scan result: {deleted}")
    assert_db_matches_files()


def test_consistency_after_reset(images: list[Path]) -> None:
    reset_state()
    for image in images:
        copy_to_comfy(image)
    scan = app.scan_photos()
    if scan != {"scanned": len(images), "deleted": 0}:
        raise AssertionError(f"Unexpected consistency scan result: {scan}")
    assert_db_matches_files()


def main() -> int:
    images = test_images()
    tests = (
        test_new_image_addition,
        test_old_image_deletion,
        test_consistency_after_reset,
    )
    try:
        for test in tests:
            test(images)
            print(f"PASS {test.__name__}")
    finally:
        reset_state()
        print("RESET photos/comfyui, database, and thumbnails")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
