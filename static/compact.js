(function () {
  var DEFAULT_PER_PAGE = 24;
  var query = parseQuery(window.location.search);
  var state = {
    page: toPositiveInt(query.page, 1),
    perPage: toPositiveInt(query.per_page, DEFAULT_PER_PAGE),
    q: query.q || "",
    source: query.source || "",
    total: 0
  };

  var gallery = document.getElementById("gallery");
  var count = document.getElementById("count");
  var pageLabel = document.getElementById("pageLabel");
  var prev = document.getElementById("prev");
  var next = document.getElementById("next");
  var filters = document.getElementById("filters");
  var search = document.getElementById("search");
  var source = document.getElementById("source");
  var errorBox = document.getElementById("errorBox");
  var emptyBox = document.getElementById("emptyBox");

  search.value = state.q;
  source.value = state.source;

  function parseQuery(searchText) {
    var text = searchText || "";
    var result = {};
    var pairText;
    var pairs;
    var i;
    var pair;
    var parts;
    var key;
    if (text.charAt(0) === "?") {
      text = text.substring(1);
    }
    if (!text) {
      return result;
    }
    pairs = text.split("&");
    for (i = 0; i < pairs.length; i += 1) {
      pairText = pairs[i];
      if (!pairText) {
        continue;
      }
      parts = pairText.split("=");
      key = decode(parts.shift() || "");
      result[key] = decode(parts.join("="));
    }
    return result;
  }

  function decode(value) {
    return decodeURIComponent(String(value || "").replace(/\+/g, " "));
  }

  function encode(value) {
    return encodeURIComponent(String(value));
  }

  function toPositiveInt(value, fallback) {
    var parsed = parseInt(value, 10);
    if (isNaN(parsed) || parsed < 1) {
      return fallback;
    }
    return parsed;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function buildQuery(page) {
    var parts = [];
    parts.push("page=" + encode(page));
    parts.push("per_page=" + encode(state.perPage));
    if (state.q) {
      parts.push("q=" + encode(state.q));
    }
    if (state.source) {
      parts.push("source=" + encode(state.source));
    }
    return parts.join("&");
  }

  function imageHref(id) {
    return "/compact/image/" + encode(id) + "?" + buildQuery(state.page);
  }

  function apiUrl(page) {
    return "/api/images?" + buildQuery(page);
  }

  function syncUrl() {
    var url = "/compact?" + buildQuery(state.page);
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, document.title, url);
    }
  }

  function requestJson(url, callback) {
    var xhr = new XMLHttpRequest();
    xhr.open("GET", url, true);
    xhr.onreadystatechange = function () {
      var payload;
      if (xhr.readyState !== 4) {
        return;
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        callback(new Error("Request failed: " + xhr.status));
        return;
      }
      try {
        payload = JSON.parse(xhr.responseText);
      } catch (error) {
        callback(new Error("Invalid server response"));
        return;
      }
      callback(null, payload);
    };
    xhr.send(null);
  }

  function formatDate(seconds, generatedAt) {
    var sourceText = generatedAt || "";
    var match;
    var date;
    if (sourceText) {
      match = String(sourceText).match(/(20\d{2})\D*(\d{1,2})\D*(\d{1,2})/);
      if (match) {
        return match[1] + "-" + pad2(match[2]) + "-" + pad2(match[3]);
      }
      date = new Date(sourceText);
      if (!isNaN(date.getTime())) {
        return formatDateParts(date);
      }
    }
    if (!seconds) {
      return "-";
    }
    date = new Date(seconds * 1000);
    if (isNaN(date.getTime())) {
      return "-";
    }
    return formatDateParts(date);
  }

  function formatDateTime(seconds, generatedAt) {
    var sourceText = generatedAt || "";
    var date;
    if (sourceText) {
      date = new Date(sourceText);
      if (!isNaN(date.getTime())) {
        return formatDateParts(date) + " " + pad2(date.getHours()) + ":" + pad2(date.getMinutes());
      }
      return sourceText;
    }
    if (!seconds) {
      return "-";
    }
    date = new Date(seconds * 1000);
    if (isNaN(date.getTime())) {
      return "-";
    }
    return formatDateParts(date) + " " + pad2(date.getHours()) + ":" + pad2(date.getMinutes());
  }

  function formatDateParts(date) {
    return date.getFullYear() + "-" + pad2(date.getMonth() + 1) + "-" + pad2(date.getDate());
  }

  function pad2(value) {
    var text = String(value);
    if (text.length < 2) {
      return "0" + text;
    }
    return text;
  }

  function showError(message) {
    errorBox.innerHTML = escapeHtml(message);
    errorBox.className = "messageBox errorBox";
  }

  function hideError() {
    errorBox.innerHTML = "";
    errorBox.className = "messageBox errorBox hidden";
  }

  function showEmpty(show) {
    emptyBox.className = show ? "messageBox" : "messageBox hidden";
  }

  function renderItems(items) {
    var html = "";
    var i;
    var item;
    var title;
    if (!items || !items.length) {
      gallery.innerHTML = "";
      showEmpty(true);
      return;
    }
    showEmpty(false);
    for (i = 0; i < items.length; i += 1) {
      item = items[i];
      title = item.title || item.file_name;
      html += ''
        + '<div class="cardWrap">'
        + '<a class="cardLink" href="' + imageHref(item.id) + '">'
        + '<div class="thumbFrame" style="background-image:url(\'' + escapeAttributeUrl(item.thumb_url) + '\')"></div>'
        + '<div class="cardMeta">'
        + '<span class="cardTitle">' + escapeHtml(title) + '</span>'
        + '<div class="cardSub">' + escapeHtml(item.source) + ' | ' + escapeHtml(formatDate(item.mtime, item.generated_at)) + '</div>'
        + '</div>'
        + '</a>'
        + '</div>';
    }
    gallery.innerHTML = html;
  }

  function escapeAttributeUrl(value) {
    return String(value == null ? "" : value).replace(/'/g, "%27");
  }

  function updatePager(page, perPage, total) {
    var start = total === 0 ? 0 : (page - 1) * perPage + 1;
    var end = page * perPage;
    state.page = page;
    state.perPage = perPage;
    if (end > total) {
      end = total;
    }
    pageLabel.innerHTML = "Page " + page;
    count.innerHTML = start + "-" + end + " of " + total + " images";
    prev.disabled = page <= 1;
    next.disabled = end >= total;
    syncUrl();
  }

  function loadImages() {
    hideError();
    showEmpty(false);
    gallery.innerHTML = "";
    count.innerHTML = "Loading images...";
    requestJson(apiUrl(state.page), function (error, data) {
      if (error) {
        count.innerHTML = "Unable to load";
        showError(error.message);
        return;
      }
      state.total = data.total || 0;
      renderItems(data.items || []);
      updatePager(data.page || state.page, data.per_page || state.perPage, state.total);
    });
  }

  filters.onsubmit = function (event) {
    if (event && event.preventDefault) {
      event.preventDefault();
    }
    state.q = trim(search.value);
    state.source = source.value;
    state.page = 1;
    loadImages();
    return false;
  };

  prev.onclick = function () {
    if (state.page <= 1) {
      return;
    }
    state.page -= 1;
    loadImages();
  };

  next.onclick = function () {
    if (state.page * state.perPage >= state.total) {
      return;
    }
    state.page += 1;
    loadImages();
  };

  function trim(value) {
    return String(value || "").replace(/^\s+|\s+$/g, "");
  }

  loadImages();
}());
