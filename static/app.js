const state = {
  page: Number(new URLSearchParams(window.location.search).get("page") || 1),
  perPage: Number(new URLSearchParams(window.location.search).get("per_page") || 48),
  total: 0,
  q: new URLSearchParams(window.location.search).get("q") || "",
  source: new URLSearchParams(window.location.search).get("source") || "",
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
      return `
        <a class="card" href="${imageHref(item.id)}">
          <img class="thumb" src="${item.thumb_url}" alt="">
          <div class="meta">
            <p class="name" title="${escapeHtml(item.relative_path)}">${escapeHtml(item.file_name)}</p>
            <div class="sub">
              <span class="badge${statusClass}">${escapeHtml(item.source)}</span>
              <span>${escapeHtml(fmtDate(item.mtime))}</span>
            </div>
          </div>
        </a>
      `;
    })
    .join("");
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
