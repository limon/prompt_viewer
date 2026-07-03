function readListStateFromUrl() {
  const query = new URLSearchParams(window.location.search);
  return {
    page: Number(query.get("page") || 1),
    perPage: Number(query.get("per_page") || 48),
    q: query.get("q") || "",
    source: query.get("source") || "",
  };
}

const initialListState = readListStateFromUrl();

const state = {
  page: initialListState.page,
  perPage: initialListState.perPage,
  total: 0,
  q: initialListState.q,
  source: initialListState.source,
  viewMode: localStorage.getItem("promptViewer.viewMode") || "masonry",
  uploadFiles: [],
  uploadSources: new Map(),
  uploadSourceOrigins: new Map(),
  uploadTitles: new Map(),
  uploadPrompts: new Map(),
  inspectItems: new Map(),
  previewUrls: new Map(),
  items: [],
};

const grid = document.querySelector("#grid");
const count = document.querySelector("#count");
const pageLabel = document.querySelector("#pageLabel");
const prev = document.querySelector("#prev");
const next = document.querySelector("#next");
const filters = document.querySelector("#filters");
const search = document.querySelector("#search");
const source = document.querySelector("#source");
const rescan = document.querySelector("#rescan");
const sourceTabs = document.querySelector("#sourceTabs");
const viewToggle = document.querySelector("#viewToggle");
const dropZone = document.querySelector("#dropZone");
const fileInput = document.querySelector("#fileInput");
const uploadButton = document.querySelector("#uploadButton");
const uploadList = document.querySelector("#uploadList");
const PROMPT_UPLOAD_SOURCES = ["chatgpt", "grok"];

function listParams(page = state.page) {
  const params = new URLSearchParams({
    page,
    per_page: state.perPage,
  });
  if (state.q) params.set("q", state.q);
  if (state.source) params.set("source", state.source);
  return params;
}

function syncControlsFromState() {
  search.value = state.q;
  source.value = state.source;
}

function syncUrlFromState(options = {}) {
  const { replace = false } = options;
  const url = `/?${listParams().toString()}`;
  if (replace) {
    window.history.replaceState(null, "", url);
    return;
  }
  window.history.pushState(null, "", url);
}

syncControlsFromState();

async function fetchImagesPage(page) {
  const response = await fetch(`/api/images?${listParams(page).toString()}`);
  if (!response.ok) throw new Error(`List failed: ${response.status}`);
  return response.json();
}

function fmtDate(seconds) {
  if (!seconds) return "-";
  return new Date(seconds * 1000).toLocaleString();
}

function fmtGenerated(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function fmtDateOnlyFromSeconds(seconds) {
  if (!seconds) return "-";
  const date = new Date(seconds * 1000);
  return Number.isNaN(date.getTime()) ? "-" : date.toISOString().slice(0, 10);
}

function fmtDateOnlyFromGenerated(value) {
  if (!value) return "-";
  const text = String(value);
  const match = text.match(/(20\d{2})\D*(\d{1,2})\D*(\d{1,2})/);
  if (match) {
    const [, year, month, day] = match;
    return `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toISOString().slice(0, 10);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function imageHref(id) {
  return `/image/${id}?${listParams().toString()}`;
}

function renderGrid(items) {
  updateViewMode();
  if (!items.length) {
    grid.innerHTML = '<p class="empty">No images found.</p>';
    return;
  }

  grid.innerHTML = items
    .map((item) => {
      const statusClass = item.parse_status === "ok" ? "" : " error";
      const sourceClass = item.parse_status === "ok" ? ` ${escapeHtml(item.source)}` : "";
      const displayDate = item.generated_at
        ? fmtDateOnlyFromGenerated(item.generated_at)
        : fmtDateOnlyFromSeconds(item.mtime);
      return `
        <a class="card" href="${imageHref(item.id)}">
          <img
            class="thumb"
            src="${item.thumb_url}"
            alt=""
            width="${escapeHtml(item.width || "")}"
            height="${escapeHtml(item.height || "")}"
          >
          <div class="meta">
            <p class="name" title="${escapeHtml(item.relative_path)}">${escapeHtml(item.title || item.file_name)}</p>
            <div class="sub">
              <span class="badge${sourceClass}${statusClass}">${escapeHtml(item.source)}</span>
              <span class="cardDate">${escapeHtml(displayDate)}</span>
            </div>
          </div>
        </a>
      `;
    })
    .join("");
}

function updateViewMode() {
  const mode = state.viewMode === "masonry" ? "masonry" : "grid";
  state.viewMode = mode;
  grid.classList.toggle("masonry", mode === "masonry");
  viewToggle.querySelectorAll("button").forEach((button) => {
    const active = button.dataset.view === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function updateSourceTabs() {
  sourceTabs.querySelectorAll("button").forEach((button) => {
    button.classList.toggle("active", button.dataset.source === state.source);
  });
}

function fileKey(file) {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function clearPreviewUrls() {
  state.previewUrls.forEach((url) => URL.revokeObjectURL(url));
  state.previewUrls = new Map();
}

function prunePreviewUrls() {
  const activeKeys = new Set(state.uploadFiles.map(fileKey));
  for (const [key, url] of state.previewUrls.entries()) {
    if (!activeKeys.has(key)) {
      URL.revokeObjectURL(url);
      state.previewUrls.delete(key);
    }
  }
}

function previewUrl(file) {
  const key = fileKey(file);
  let url = state.previewUrls.get(key);
  if (!url) {
    url = URL.createObjectURL(file);
    state.previewUrls.set(key, url);
  }
  return url;
}

function inferredUploadSource(file) {
  const lower = file.name.toLowerCase();
  if (lower.startsWith("comfyui")) return "comfyui";
  if (lower.startsWith("chatgpt")) return "chatgpt";
  if (lower.startsWith("grok")) return "grok";
  return "comfyui";
}

function isSupportedUploadSource(value) {
  return value === "comfyui" || PROMPT_UPLOAD_SOURCES.includes(value);
}

function isPromptUploadSource(value) {
  return PROMPT_UPLOAD_SOURCES.includes(value);
}

function isInspectableUpload(file) {
  return [".png", ".jpg", ".jpeg"].some((suffix) => file.name.toLowerCase().endsWith(suffix));
}

function uploadSource(file) {
  return state.uploadSources.get(fileKey(file)) ?? inferredUploadSource(file);
}

function fileByKey(key) {
  return state.uploadFiles.find((file) => fileKey(file) === key);
}

function parseGeneratedFromName(file) {
  const match = file.name.match(/(20\d{2})\D*(\d{1,2})\D*(\d{1,2})(?:\D+(\d{1,2})\D*(\d{1,2})\D*(\d{1,2}))?/);
  if (!match) return "";
  const [, year, month, day, hour = "00", minute = "00", second = "00"] = match;
  const padded = [month, day, hour, minute, second].map((part) => part.padStart(2, "0"));
  return new Date(`${year}-${padded[0]}-${padded[1]}T${padded[2]}:${padded[3]}:${padded[4]}`).toISOString();
}

function fileMtimeIso(file) {
  return new Date(file.lastModified).toISOString();
}

function snapshotUploadInputs() {
  uploadList.querySelectorAll("[data-title-key]").forEach((input) => {
    state.uploadTitles.set(input.dataset.titleKey, input.value);
  });
  uploadList.querySelectorAll("[data-prompt-key]").forEach((textarea) => {
    state.uploadPrompts.set(textarea.dataset.promptKey, textarea.value);
  });
}

function renderUploadList(options = {}) {
  const { preserveInputs = true } = options;
  if (preserveInputs) snapshotUploadInputs();
  const hasUnknown = state.uploadFiles.some((file) => !uploadSource(file));
  const waitingForInspect = state.uploadFiles.some((file) => isInspectableUpload(file) && !state.inspectItems.has(fileKey(file)));
  uploadButton.disabled = state.uploadFiles.length === 0 || hasUnknown || waitingForInspect;
  if (!state.uploadFiles.length) {
    uploadList.innerHTML = "";
    return;
  }
  uploadList.innerHTML = state.uploadFiles
    .map((file) => {
      const kind = uploadSource(file);
      const inspected = state.inspectItems.get(fileKey(file));
      const hasXmp = inspected?.has_xmp;
      const key = fileKey(file);
      const defaultTitle = inspected?.metadata?.title || file.name;
      const titleValue = state.uploadTitles.get(key) ?? defaultTitle;
      const promptValue = state.uploadPrompts.get(key) ?? (isPromptUploadSource(kind) ? (inspected?.metadata?.prompt || "") : "");
      const sourceSelect = `
        <select class="uploadSourceSelect" data-source-key="${escapeHtml(key)}" aria-label="Source for ${escapeHtml(file.name)}">
          <option value=""${kind ? "" : " selected"}>Source</option>
          <option value="comfyui"${kind === "comfyui" ? " selected" : ""}>ComfyUI</option>
          <option value="chatgpt"${kind === "chatgpt" ? " selected" : ""}>ChatGPT</option>
          <option value="grok"${kind === "grok" ? " selected" : ""}>Grok</option>
        </select>
      `;
      const titleDisplay =
        kind
          ? `
            <label class="uploadTitleField">
              <input
                data-title-key="${escapeHtml(key)}"
                type="text"
                value="${escapeHtml(titleValue)}"
                placeholder="${escapeHtml(file.name)}"
              >
            </label>
          `
          : "";
      const promptInput =
        isPromptUploadSource(kind) && inspected
          ? `
            <label class="uploadField">
              <textarea data-prompt-key="${escapeHtml(key)}" placeholder="Prompt for ${escapeHtml(file.name)}">${escapeHtml(promptValue)}</textarea>
            </label>
          `
          : "";
      let status = "Choose ComfyUI, ChatGPT, or Grok";
      if (kind === "comfyui") {
        status = "ComfyUI metadata will be parsed after upload";
      } else if (isPromptUploadSource(kind)) {
        status = inspected
          ? hasXmp
            ? "Will use image metadata"
            : "Prompt optional; date/model will be filled automatically"
          : "Checking metadata...";
      } else if (!inspected && isInspectableUpload(file)) {
        status = "Reading image metadata...";
      }
      return `
        <div class="uploadItem">
          <button class="removeUpload" type="button" data-remove-key="${escapeHtml(key)}" aria-label="Remove ${escapeHtml(file.name)}">×</button>
          <img class="uploadThumb" src="${escapeHtml(previewUrl(file))}" alt="">
          <div>
            ${
              titleDisplay ||
              `<strong>${escapeHtml(file.name)}</strong>`
            }
            <div class="uploadMetaLine">
              <em class="uploadType ${escapeHtml(kind || "unknown")}">${escapeHtml(kind || "unknown")}</em>
              ${sourceSelect}
            </div>
            <span>${escapeHtml(status)}</span>
          </div>
          <div class="uploadMetadata">
            ${promptInput}
          </div>
        </div>
      `;
    })
    .join("");
}

function removeUploadFile(key) {
  state.uploadFiles = state.uploadFiles.filter((file) => fileKey(file) !== key);
  state.uploadSources.delete(key);
  state.uploadSourceOrigins.delete(key);
  state.uploadTitles.delete(key);
  state.uploadPrompts.delete(key);
  state.inspectItems.delete(key);
  const url = state.previewUrls.get(key);
  if (url) URL.revokeObjectURL(url);
  state.previewUrls.delete(key);
  renderUploadList();
}

async function inspectUploadFiles(files) {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  const response = await fetch("/api/uploads/inspect", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) throw new Error(`Inspect failed: ${response.status}`);
  const data = await response.json();
  data.items.forEach((item, index) => {
    const file = files[index];
    if (!file) return;
    const key = fileKey(file);
    state.inspectItems.set(key, item);
    const embeddedTitle = item?.metadata?.title || "";
    const currentTitle = state.uploadTitles.get(key);
    if (embeddedTitle && (currentTitle === undefined || currentTitle === file.name)) {
      state.uploadTitles.set(key, embeddedTitle);
    }
    const embeddedPrompt = item?.metadata?.prompt || "";
    const currentPrompt = state.uploadPrompts.get(key);
    if (embeddedPrompt && (currentPrompt === undefined || currentPrompt === "")) {
      state.uploadPrompts.set(key, embeddedPrompt);
    }
    const embeddedSource = item?.metadata?.source;
    const currentSource = state.uploadSources.get(key) || "";
    const sourceOrigin = state.uploadSourceOrigins.get(key) || "inferred";
    if (
      isSupportedUploadSource(embeddedSource) &&
      sourceOrigin !== "manual" &&
      (sourceOrigin === "inferred" || sourceOrigin === "inspected" || !currentSource)
    ) {
      state.uploadSources.set(key, embeddedSource);
      state.uploadSourceOrigins.set(key, "inspected");
    }
  });
  renderUploadList({ preserveInputs: false });
}

async function addUploadFiles(files) {
  const existing = new Set(state.uploadFiles.map(fileKey));
  const additions = Array.from(files).filter((file) => !existing.has(fileKey(file)));
  if (!additions.length) {
    renderUploadList();
    return;
  }
  additions.forEach((file) => {
    const key = fileKey(file);
    if (!state.uploadSources.has(key)) {
      state.uploadSources.set(key, inferredUploadSource(file));
      state.uploadSourceOrigins.set(key, "inferred");
    }
  });
  state.uploadFiles = [...state.uploadFiles, ...additions];
  prunePreviewUrls();
  renderUploadList();
  const inspectableFiles = additions.filter(isInspectableUpload);
  if (inspectableFiles.length) {
    await inspectUploadFiles(inspectableFiles);
  }
}

async function loadImages() {
  const data = await fetchImagesPage(state.page);

  state.total = data.total;
  state.items = data.items;
  renderGrid(data.items);
  const start = data.total === 0 ? 0 : (data.page - 1) * data.per_page + 1;
  const end = Math.min(data.page * data.per_page, data.total);
  count.textContent = `${start}-${end} of ${data.total} images`;
  pageLabel.textContent = `Page ${data.page}`;
  prev.disabled = data.page <= 1;
  next.disabled = end >= data.total;
  updateSourceTabs();
}

filters.addEventListener("submit", (event) => {
  event.preventDefault();
  state.page = 1;
  state.q = search.value.trim();
  state.source = source.value;
  syncUrlFromState();
  loadImages().catch((error) => {
    grid.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
});

sourceTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.source = button.dataset.source;
  state.page = 1;
  syncControlsFromState();
  syncUrlFromState();
  loadImages().catch((error) => {
    grid.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
});

viewToggle.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.viewMode = button.dataset.view === "masonry" ? "masonry" : "grid";
  localStorage.setItem("promptViewer.viewMode", state.viewMode);
  updateViewMode();
});

fileInput.addEventListener("change", () => {
  addUploadFiles(fileInput.files).catch((error) => {
    uploadList.innerHTML = `<p class="errorBox">${escapeHtml(error.message)}</p>`;
  }).finally(() => {
    fileInput.value = "";
  });
});

dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropZone.classList.add("dragging");
});

dropZone.addEventListener("dragleave", () => {
  dropZone.classList.remove("dragging");
});

dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropZone.classList.remove("dragging");
  addUploadFiles(event.dataTransfer.files).catch((error) => {
    uploadList.innerHTML = `<p class="errorBox">${escapeHtml(error.message)}</p>`;
  });
});

uploadList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-key]");
  if (!button) return;
  removeUploadFile(button.dataset.removeKey);
});

uploadList.addEventListener("change", (event) => {
  const select = event.target.closest("[data-source-key]");
  if (!select) return;
  const key = select.dataset.sourceKey;
  const selectedSource = select.value;
  state.uploadSources.set(key, selectedSource);
  state.uploadSourceOrigins.set(key, "manual");
  const file = fileByKey(key);
  renderUploadList();
  if (file && isInspectableUpload(file) && !state.inspectItems.has(key)) {
    inspectUploadFiles([file]).catch((error) => {
      uploadList.innerHTML = `<p class="errorBox">${escapeHtml(error.message)}</p>`;
    });
  }
});

uploadButton.addEventListener("click", async () => {
  if (!state.uploadFiles.length) return;
  uploadButton.disabled = true;
  try {
    const comfyFiles = state.uploadFiles.filter((file) => uploadSource(file) === "comfyui");
    const promptFilesBySource = new Map(
      PROMPT_UPLOAD_SOURCES.map((promptSource) => [
        promptSource,
        state.uploadFiles.filter((file) => uploadSource(file) === promptSource),
      ]),
    );
    const unknownFiles = state.uploadFiles.filter((file) => !uploadSource(file));
    if (unknownFiles.length) {
      throw new Error(`Choose a source for ${unknownFiles[0].name}`);
    }

    if (comfyFiles.length) {
      const comfyData = new FormData();
      comfyFiles.forEach((file) => comfyData.append("files", file));
      const titles = new Map(
        Array.from(uploadList.querySelectorAll("[data-title-key]")).map((input) => [
          input.dataset.titleKey,
          input.value,
        ]),
      );
      const metadata = comfyFiles.map((file) => ({
        filename: file.name,
        source: "comfyui",
        title: (titles.get(fileKey(file)) || file.name).trim() || file.name,
        generated_at: parseGeneratedFromName(file),
        mtime: fileMtimeIso(file),
      }));
      comfyData.append("metadata", JSON.stringify(metadata));
      const response = await fetch("/api/uploads/comfyui", {
        method: "POST",
        body: comfyData,
      });
      if (!response.ok) throw new Error(`ComfyUI upload failed: ${response.status}`);
    }

    const prompts = new Map(
      Array.from(uploadList.querySelectorAll("[data-prompt-key]")).map((textarea) => [
        textarea.dataset.promptKey,
        textarea.value,
      ]),
    );
    const titles = new Map(
      Array.from(uploadList.querySelectorAll("[data-title-key]")).map((input) => [
        input.dataset.titleKey,
        input.value,
      ]),
    );
    for (const promptSource of PROMPT_UPLOAD_SOURCES) {
      const promptFiles = promptFilesBySource.get(promptSource) || [];
      if (!promptFiles.length) continue;
      const promptData = new FormData();
      promptFiles.forEach((file) => promptData.append("files", file));
      const metadata = promptFiles.map((file) => {
        const inspected = state.inspectItems.get(fileKey(file));
        const prompt = prompts.get(fileKey(file)) || "";
        const title = (titles.get(fileKey(file)) || file.name).trim() || file.name;
        if (!inspected) {
          throw new Error(`Metadata check is still running for ${file.name}`);
        }
        return {
          filename: file.name,
          source: promptSource,
          title,
          prompt,
          generated_at: parseGeneratedFromName(file),
          mtime: fileMtimeIso(file),
        };
      });
      promptData.append("metadata", JSON.stringify(metadata));
      const response = await fetch(`/api/uploads/${promptSource}`, {
        method: "POST",
        body: promptData,
      });
      if (!response.ok) throw new Error(`${promptSource} upload failed: ${response.status}`);
    }
    state.uploadFiles = [];
    state.uploadSources = new Map();
    state.uploadSourceOrigins = new Map();
    state.uploadTitles = new Map();
    state.uploadPrompts = new Map();
    state.inspectItems = new Map();
    clearPreviewUrls();
    fileInput.value = "";
    renderUploadList();
    await loadImages();
  } catch (error) {
    uploadList.innerHTML = `<p class="errorBox">${escapeHtml(error.message)}</p>`;
  } finally {
    renderUploadList();
  }
});

rescan.addEventListener("click", async () => {
  rescan.disabled = true;
  try {
    await fetch("/api/rescan", { method: "POST" });
    await loadImages();
  } finally {
    rescan.disabled = false;
  }
});

prev.addEventListener("click", () => {
  if (state.page > 1) {
    state.page -= 1;
    syncUrlFromState();
    loadImages();
  }
});

next.addEventListener("click", () => {
  if (state.page * state.perPage < state.total) {
    state.page += 1;
    syncUrlFromState();
    loadImages();
  }
});

window.addEventListener("popstate", () => {
  const nextState = readListStateFromUrl();
  state.page = nextState.page;
  state.perPage = nextState.perPage;
  state.q = nextState.q;
  state.source = nextState.source;
  syncControlsFromState();
  loadImages().catch((error) => {
    grid.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
});

loadImages().catch((error) => {
  grid.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
