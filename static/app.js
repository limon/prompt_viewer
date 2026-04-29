const state = {
  page: Number(new URLSearchParams(window.location.search).get("page") || 1),
  perPage: Number(new URLSearchParams(window.location.search).get("per_page") || 48),
  total: 0,
  q: new URLSearchParams(window.location.search).get("q") || "",
  source: new URLSearchParams(window.location.search).get("source") || "",
  uploadFiles: [],
  uploadSources: new Map(),
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
const dropZone = document.querySelector("#dropZone");
const fileInput = document.querySelector("#fileInput");
const uploadButton = document.querySelector("#uploadButton");
const uploadList = document.querySelector("#uploadList");

search.value = state.q;
source.value = state.source;

async function fetchImagesPage(page) {
  const params = new URLSearchParams({
    page,
    per_page: state.perPage,
  });
  if (state.q) params.set("q", state.q);
  if (state.source) params.set("source", state.source);

  const response = await fetch(`/api/images?${params.toString()}`);
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
  const params = new URLSearchParams({
    page: state.page,
    per_page: state.perPage,
  });
  if (state.q) params.set("q", state.q);
  if (state.source) params.set("source", state.source);
  return `/image/${id}?${params.toString()}`;
}

function renderGrid(items) {
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
          <img class="thumb" src="${item.thumb_url}" alt="">
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
  return "";
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

function renderUploadList() {
  uploadList.querySelectorAll("[data-title-key]").forEach((input) => {
    state.uploadTitles.set(input.dataset.titleKey, input.value);
  });
  uploadList.querySelectorAll("[data-prompt-key]").forEach((textarea) => {
    state.uploadPrompts.set(textarea.dataset.promptKey, textarea.value);
  });
  const hasUnknown = state.uploadFiles.some((file) => !uploadSource(file));
  const waitingForInspect = state.uploadFiles.some(
    (file) => uploadSource(file) === "chatgpt" && !state.inspectItems.has(fileKey(file)),
  );
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
      const promptValue = state.uploadPrompts.get(key) ?? "";
      const sourceSelect = `
        <select class="uploadSourceSelect" data-source-key="${escapeHtml(key)}" aria-label="Source for ${escapeHtml(file.name)}">
          <option value=""${kind ? "" : " selected"}>Source</option>
          <option value="comfyui"${kind === "comfyui" ? " selected" : ""}>ComfyUI</option>
          <option value="chatgpt"${kind === "chatgpt" ? " selected" : ""}>ChatGPT</option>
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
        kind === "chatgpt" && inspected && !hasXmp
          ? `
            <label class="uploadField">
              <textarea data-prompt-key="${escapeHtml(key)}" placeholder="Prompt for ${escapeHtml(file.name)}">${escapeHtml(promptValue)}</textarea>
            </label>
          `
          : "";
      let status = "Choose ComfyUI or ChatGPT";
      if (kind === "comfyui") {
        status = "ComfyUI metadata will be parsed after upload";
      } else if (kind === "chatgpt") {
        status = inspected
          ? hasXmp
            ? "Will use image metadata"
            : "Prompt optional; date/model will be filled automatically"
          : "Checking metadata...";
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
  state.uploadTitles.delete(key);
  state.uploadPrompts.delete(key);
  state.inspectItems.delete(key);
  const url = state.previewUrls.get(key);
  if (url) URL.revokeObjectURL(url);
  state.previewUrls.delete(key);
  renderUploadList();
}

async function inspectChatgptFiles(files) {
  const formData = new FormData();
  files.forEach((file) => formData.append("files", file));
  const response = await fetch("/api/uploads/chatgpt/inspect", {
    method: "POST",
    body: formData,
  });
  if (!response.ok) throw new Error(`Inspect failed: ${response.status}`);
  const data = await response.json();
  data.items.forEach((item, index) => {
    const file = files[index];
    if (file) state.inspectItems.set(fileKey(file), item);
  });
  renderUploadList();
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
    }
  });
  state.uploadFiles = [...state.uploadFiles, ...additions];
  prunePreviewUrls();
  renderUploadList();
  const chatgptFiles = additions.filter((file) => uploadSource(file) === "chatgpt");
  if (chatgptFiles.length) {
    await inspectChatgptFiles(chatgptFiles);
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
  loadImages().catch((error) => {
    grid.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
});

sourceTabs.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.source = button.dataset.source;
  source.value = state.source;
  state.page = 1;
  loadImages().catch((error) => {
    grid.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
  });
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
  const file = fileByKey(key);
  renderUploadList();
  if (file && selectedSource === "chatgpt" && !state.inspectItems.has(key)) {
    inspectChatgptFiles([file]).catch((error) => {
      uploadList.innerHTML = `<p class="errorBox">${escapeHtml(error.message)}</p>`;
    });
  }
});

uploadButton.addEventListener("click", async () => {
  if (!state.uploadFiles.length) return;
  uploadButton.disabled = true;
  try {
    const comfyFiles = state.uploadFiles.filter((file) => uploadSource(file) === "comfyui");
    const chatgptFiles = state.uploadFiles.filter((file) => uploadSource(file) === "chatgpt");
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

    if (chatgptFiles.length) {
      const chatgptData = new FormData();
      chatgptFiles.forEach((file) => chatgptData.append("files", file));
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
      const metadata = chatgptFiles.map((file) => {
        const inspected = state.inspectItems.get(fileKey(file));
        const prompt = prompts.get(fileKey(file)) || "";
        const title = (titles.get(fileKey(file)) || file.name).trim() || file.name;
        if (!inspected) {
          throw new Error(`Metadata check is still running for ${file.name}`);
        }
        return {
          filename: file.name,
          source: "chatgpt",
          title,
          prompt: inspected?.has_xmp ? "" : prompt,
          generated_at: parseGeneratedFromName(file),
          mtime: fileMtimeIso(file),
          model: "image2",
        };
      });
      chatgptData.append("metadata", JSON.stringify(metadata));
      const response = await fetch("/api/uploads/chatgpt", {
        method: "POST",
        body: chatgptData,
      });
      if (!response.ok) throw new Error(`ChatGPT upload failed: ${response.status}`);
    }
    state.uploadFiles = [];
    state.uploadSources = new Map();
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
    loadImages();
  }
});

next.addEventListener("click", () => {
  if (state.page * state.perPage < state.total) {
    state.page += 1;
    loadImages();
  }
});

loadImages().catch((error) => {
  grid.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
});
