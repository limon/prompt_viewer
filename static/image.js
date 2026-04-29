const pathParts = window.location.pathname.split("/");
const imageId = pathParts[pathParts.length - 1];
const query = new URLSearchParams(window.location.search);

const state = {
  page: Number(query.get("page") || 1),
  perPage: Number(query.get("per_page") || 48),
  total: 0,
  q: query.get("q") || "",
  source: query.get("source") || "",
  items: [],
  selectedIndex: -1,
  originalSize: false,
  item: null,
};

const backLink = document.querySelector("#backLink");
const prevImage = document.querySelector("#prevImage");
const nextImage = document.querySelector("#nextImage");
const positionLabel = document.querySelector("#positionLabel");
const thumbNav = document.querySelector("#thumbNav");
const sizeMode = document.querySelector("#sizeMode");
const previewFrame = document.querySelector("#previewFrame");
const previewImage = document.querySelector("#previewImage");
const detail = document.querySelector("#detail");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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

function fmtSizeKb(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value)) return "-";
  return `${(value / 1024).toLocaleString(undefined, {
    maximumFractionDigits: value < 1024 * 10 ? 1 : 0,
  })} KB`;
}

function listParams(page = state.page) {
  const params = new URLSearchParams({
    page,
    per_page: state.perPage,
  });
  if (state.q) params.set("q", state.q);
  if (state.source) params.set("source", state.source);
  return params;
}

function imageHref(id, page = state.page) {
  return `/image/${id}?${listParams(page).toString()}`;
}

async function fetchImagesPage(page) {
  const response = await fetch(`/api/images?${listParams(page).toString()}`);
  if (!response.ok) throw new Error(`List failed: ${response.status}`);
  return response.json();
}

function updateBackLink() {
  const params = listParams(state.page);
  backLink.href = `/?${params.toString()}`;
}

function renderList(items, field) {
  if (!items.length) return '<p class="listEmpty">None</p>';
  return `
    <ul class="list">
      ${items
        .map(
          (item) => `
            <li>
              <strong>${escapeHtml(item[field])}</strong><br>
              <span>${escapeHtml(item.class_type)}.${escapeHtml(item.field)} [${escapeHtml(item.node)}]</span>
            </li>
          `,
        )
        .join("")}
    </ul>
  `;
}

function updateSizeMode() {
  document.body.classList.toggle("originalSizeMode", state.originalSize);
  previewImage.classList.toggle("originalSize", state.originalSize);
  sizeMode.textContent = state.originalSize ? "Fit to window" : "Original size";
  sizeMode.setAttribute("aria-pressed", state.originalSize ? "true" : "false");
}

function updateNav() {
  const absoluteIndex =
    state.selectedIndex >= 0
      ? (state.page - 1) * state.perPage + state.selectedIndex + 1
      : 0;
  positionLabel.textContent = absoluteIndex ? `${absoluteIndex} of ${state.total}` : "";
  prevImage.disabled = absoluteIndex <= 1;
  nextImage.disabled = absoluteIndex >= state.total;
  renderThumbNav();
}

function renderThumbNav() {
  thumbNav.innerHTML = state.items
    .map((item, index) => {
      const current = index === state.selectedIndex;
      return `
        <a
          class="thumbNavItem${current ? " current" : ""}"
          href="${imageHref(item.id)}"
          title="${escapeHtml(item.title || item.file_name)}"
          aria-current="${current ? "true" : "false"}"
        >
          <img src="${item.thumb_url}" alt="">
        </a>
      `;
    })
    .join("");

  requestAnimationFrame(() => {
    const current = thumbNav.querySelector(".thumbNavItem.current");
    if (current) {
      current.scrollIntoView({ block: "nearest", inline: "center" });
    }
  });
}

function renderDetail(item) {
  const prompt = item.longest_prompt_detail?.text || "";
  const title = item.title || item.file_name;
  const displayDate = item.generated_at ? fmtGenerated(item.generated_at) : fmtDate(item.mtime);
  document.title = `${title} - Prompt Viewer`;
  previewImage.src = item.media_url;

  detail.innerHTML = `
    <div class="metadataHeader">
      <button
        id="titleEditable"
        class="editableValue titleEditable"
        type="button"
        data-field="title"
      >${escapeHtml(title)}</button>
    </div>
    ${item.parse_error ? `<div class="errorBox">${escapeHtml(item.parse_error)}</div>` : ""}
    <dl class="kv">
      <dt>Source</dt><dd>${escapeHtml(item.source)}</dd>
      <dt>Path</dt><dd>${escapeHtml(item.relative_path)}</dd>
      <dt>Dimensions</dt><dd>${escapeHtml(item.width || "?")} x ${escapeHtml(item.height || "?")}</dd>
      <dt>Size</dt><dd>${escapeHtml(fmtSizeKb(item.size_bytes))}</dd>
      <dt>Date</dt><dd>${escapeHtml(displayDate)}</dd>
    </dl>
    <h2 class="sectionTitle">Prompt</h2>
    ${
      item.source === "chatgpt"
        ? `
          <button
            id="promptEditable"
            class="editableValue prompt editablePrompt"
            type="button"
            data-field="prompt"
          >${escapeHtml(prompt || "None")}</button>
        `
        : `<p class="prompt">${escapeHtml(prompt || "None")}</p>`
    }
    <h2 class="sectionTitle">Models</h2>
    ${renderList(item.models, "model")}
    ${
      item.source === "comfyui"
        ? `
          <h2 class="sectionTitle">LoRAs</h2>
          ${renderList(item.loras, "lora")}
        `
        : ""
    }
  `;
  bindInlineEditors(item);
  updateSizeMode();
}

async function loadDetail(id) {
  detail.innerHTML = '<p class="empty">Loading...</p>';
  const response = await fetch(`/api/images/${id}`);
  if (!response.ok) {
    detail.innerHTML = '<p class="errorBox">Image detail failed to load.</p>';
    return;
  }

  const item = await response.json();
  state.item = item;
  renderDetail(item);
}

function bindInlineEditors(item) {
  detail.querySelectorAll(".editableValue").forEach((element) => {
    element.addEventListener("click", () => startInlineEdit(element, item));
  });
}

function fieldValue(item, field) {
  if (field === "title") return item.title || item.file_name;
  if (field === "prompt") return item.longest_prompt_detail?.text || "";
  return "";
}

function renderEditorControl(field, value) {
  if (field === "prompt") {
    return `<textarea class="inlineTextarea">${escapeHtml(value)}</textarea>`;
  }
  return `<input class="inlineInput" type="text" value="${escapeHtml(value)}">`;
}

function startInlineEdit(element, item) {
  if (detail.querySelector(".inlineEditor")) return;
  const field = element.dataset.field;
  const value = fieldValue(item, field);
  const editor = document.createElement("div");
  editor.className = `inlineEditor ${field === "title" ? "titleInlineEditor" : ""}`;
  editor.innerHTML = `
    ${renderEditorControl(field, value)}
    <div class="inlineActions">
      <button type="button" data-action="save">Save</button>
      <button type="button" data-action="cancel">Cancel</button>
      <span></span>
    </div>
  `;
  element.replaceWith(editor);

  const input = editor.querySelector("input, textarea");
  const status = editor.querySelector("span");
  const saveButton = editor.querySelector('[data-action="save"]');
  input.focus();
  input.select();

  const cancel = () => renderDetail(state.item || item);
  const save = async () => {
    const nextValue = input.value;
    if (nextValue === value) {
      cancel();
      return;
    }

    saveButton.disabled = true;
    status.textContent = "Saving...";
    try {
      const response = await fetch(`/api/images/${item.id}/metadata`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [field]: nextValue }),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `Save failed: ${response.status}`);
      }
      const updated = await response.json();
      state.item = updated;
      if (state.selectedIndex >= 0 && state.items[state.selectedIndex]) {
        state.items[state.selectedIndex] = {
          ...state.items[state.selectedIndex],
          title: updated.title,
          file_name: updated.file_name,
        };
      }
      renderThumbNav();
      renderDetail(updated);
    } catch (error) {
      status.textContent = error.message;
      saveButton.disabled = false;
    }
  };

  editor.querySelector('[data-action="cancel"]').addEventListener("click", cancel);
  saveButton.addEventListener("click", save);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      cancel();
    }
    if (field === "title" && event.key === "Enter") {
      event.preventDefault();
      save();
    }
    if (field === "prompt" && event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      save();
    }
  });
}

async function loadContext(id) {
  const data = await fetchImagesPage(state.page);
  state.total = data.total;
  state.items = data.items;
  state.selectedIndex = data.items.findIndex((item) => String(item.id) === String(id));

  if (state.selectedIndex < 0) {
    state.items = [{ id: Number(id) }];
    state.selectedIndex = 0;
  }

  updateBackLink();
  updateNav();
}

async function move(direction) {
  let nextIndex = state.selectedIndex + direction;
  if (nextIndex >= 0 && nextIndex < state.items.length) {
    window.location.href = imageHref(state.items[nextIndex].id);
    return;
  }

  const nextPage = state.page + direction;
  const maxPage = Math.ceil(state.total / state.perPage);
  if (nextPage < 1 || nextPage > maxPage) return;

  const data = await fetchImagesPage(nextPage);
  const pageIndex = direction > 0 ? 0 : data.items.length - 1;
  if (data.items[pageIndex]) {
    window.location.href = imageHref(data.items[pageIndex].id, nextPage);
  }
}

prevImage.addEventListener("click", () => move(-1));
nextImage.addEventListener("click", () => move(1));
sizeMode.addEventListener("click", () => {
  state.originalSize = !state.originalSize;
  updateSizeMode();
});
previewImage.addEventListener("click", () => {
  state.originalSize = !state.originalSize;
  updateSizeMode();
});
document.addEventListener("keydown", (event) => {
  if (event.target.closest("input, textarea")) return;
  if (event.key === "ArrowLeft") move(-1);
  if (event.key === "ArrowRight") move(1);
});

loadContext(imageId)
  .then(() => loadDetail(imageId))
  .catch((error) => {
    detail.innerHTML = `<p class="errorBox">${escapeHtml(error.message)}</p>`;
  });
