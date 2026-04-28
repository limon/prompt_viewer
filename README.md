# Prompt Viewer

Single-service FastAPI gallery for ComfyUI PNG files.

## Layout

- `photos/`: watched image root.
- `photos/comfyui/`: ComfyUI source directory. PNG files here are parsed with `comfy_png_summary.py`.
- `.prompt_viewer_thumbs/`: generated thumbnails.
- `prompt_viewer.sqlite3`: local SQLite database.

## Run

```bash
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8888
```

Open `http://127.0.0.1:8888/`.

On startup the app creates `photos/comfyui`, scans existing PNG files, and starts a watchdog observer for new or changed files under `photos`.

## Test

```bash
nix develop -c python scripts/test_image_sync.py
```

The sync test resets the SQLite database for each case, copies PNG files from `test/`, verifies new image addition, verifies deleted files are removed from the database, and checks that database paths match the current `photos/comfyui` contents.
After the test run finishes, it resets `photos/comfyui`, the SQLite database, WAL/SHM files, and generated thumbnails back to a clean initial state.
