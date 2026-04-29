# Prompt Viewer

Single-service FastAPI gallery for ComfyUI PNG files and ChatGPT PNG/JPEG images.

## Layout

- `photos/`: watched image root.
- `photos/comfyui/`: ComfyUI source directory. PNG files here are parsed with `comfy_png_summary.py`.
- `photos/chatgpt/`: ChatGPT source directory. PNG/JPEG files here are parsed from Prompt Viewer XMP metadata.
- `.prompt_viewer_thumbs/`: generated thumbnails.
- `prompt_viewer.sqlite3`: local SQLite database.

## Run

```bash
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8888
```

Open `http://127.0.0.1:8888/`.

## Docker

```bash
docker compose up --build
```

Open `http://127.0.0.1:8888/`. If that port is already in use:

```bash
PROMPT_VIEWER_HOST_PORT=8889 docker compose up --build
```

Docker Compose stores images under `./photos`, thumbnails under `./.prompt_viewer_thumbs`, and the SQLite database under `./docker-data`.

To run the published image instead of building locally:

```bash
docker compose -f compose.remote.yaml up
```

## Dependency Locking

`requirements.txt` lists the direct Python dependencies. `requirements.lock.txt` pins the resolved Python package versions used by both Docker and the Nix development shell.

`nix develop` creates or updates `./.venv` from `requirements.lock.txt` and puts it on `PATH`. The Docker image uses the same lock file with Python 3.13.

When changing Python dependencies, update `requirements.txt`, regenerate `requirements.lock.txt`, and rebuild the Docker image.

On startup the app creates `photos/comfyui` and `photos/chatgpt`, scans existing files, and starts a watchdog observer for new or changed files under `photos`.

The browser upload panel supports ComfyUI PNG uploads and ChatGPT PNG/JPEG uploads. ChatGPT uploads are inspected for XMP first; files with readable XMP use the embedded prompt/date/model, and files without XMP are written with Prompt Viewer XMP before indexing.

## Test

```bash
nix develop -c python scripts/test_image_sync.py
```

The sync test resets the SQLite database for each case, copies PNG files from `test/`, verifies new image addition, verifies deleted files are removed from the database, checks XMP round-trips, and checks that database paths match the current photo roots.
After the test run finishes, it resets `photos/comfyui`, `photos/chatgpt`, the SQLite database, WAL/SHM files, and generated thumbnails back to a clean initial state.
