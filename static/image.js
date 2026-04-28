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
          title="${escapeHtml(item.file_name)}"
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

async function loadDetail(id) {
  detail.innerHTML = '<p class="empty">Loading...</p>';
  const response = await fetch(`/api/images/${id}`);
  if (!response.ok) {
    detail.innerHTML = '<p class="errorBox">Image detail failed to load.</p>';
    return;
  }

  const item = await response.json();
  const prompt = item.longest_prompt_detail?.text || "";
  document.title = `${item.file_name} - Prompt Viewer`;
  previewImage.src = item.media_url;

  detail.innerHTML = `
    <h1>${escapeHtml(item.file_name)}</h1>
    ${item.parse_error ? `<div class="errorBox">${escapeHtml(item.parse_error)}</div>` : ""}
    <dl class="kv">
      <dt>Source</dt><dd>${escapeHtml(item.source)}</dd>
      <dt>Parser</dt><dd>${escapeHtml(item.parser || "-")}</dd>
      <dt>Path</dt><dd>${escapeHtml(item.relative_path)}</dd>
      <dt>Dimensions</dt><dd>${escapeHtml(item.width || "?")} x ${escapeHtml(item.height || "?")}</dd>
      <dt>Size</dt><dd>${escapeHtml(item.size_bytes)} bytes</dd>
      <dt>Modified</dt><dd>${escapeHtml(fmtDate(item.mtime))}</dd>
      <dt>Metadata keys</dt><dd>${escapeHtml(item.metadata_keys.join(", ") || "none")}</dd>
    </dl>
    <h2 class="sectionTitle">Longest Prompt</h2>
    <p class="prompt">${escapeHtml(prompt || "None")}</p>
    <h2 class="sectionTitle">Models</h2>
    ${renderList(item.models, "model")}
    <h2 class="sectionTitle">LoRAs</h2>
    ${renderList(item.loras, "lora")}
    <h2 class="sectionTitle">Raw Metadata Keys</h2>
    <ul class="list">
      ${Object.keys(item.raw_metadata)
        .map((key) => `<li><strong>${escapeHtml(key)}</strong></li>`)
        .join("") || "<li>None</li>"}
    </ul>
  `;
  updateSizeMode();
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
  if (event.key === "ArrowLeft") move(-1);
  if (event.key === "ArrowRight") move(1);
});

loadContext(imageId)
  .then(() => loadDetail(imageId))
  .catch((error) => {
    detail.innerHTML = `<p class="errorBox">${escapeHtml(error.message)}</p>`;
  });
