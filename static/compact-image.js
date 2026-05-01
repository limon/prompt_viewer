(function () {
  var DEFAULT_PER_PAGE = 24;
  var imageId = extractImageId(window.location.pathname);
  var query = parseQuery(window.location.search);
  var state = {
    page: toPositiveInt(query.page, 1),
    perPage: toPositiveInt(query.per_page, DEFAULT_PER_PAGE),
    q: query.q || "",
    source: query.source || "",
    total: 0,
    items: [],
    selectedIndex: -1
  };

  var backLink = document.getElementById("backLink");
  var prevImage = document.getElementById("prevImage");
  var nextImage = document.getElementById("nextImage");
  var positionLabel = document.getElementById("positionLabel");
  var openOriginalLink = document.getElementById("openOriginalLink");
  var previewImage = document.getElementById("previewImage");
  var relatedBlock = document.getElementById("relatedBlock");
  var detail = document.getElementById("detail");
  var detailError = document.getElementById("detailError");

  function extractImageId(pathname) {
    var parts = String(pathname || "").split("/");
    return parts[parts.length - 1];
  }

  function parseQuery(searchText) {
    var text = searchText || "";
    var result = {};
    var pairs;
    var i;
    var pairText;
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

  function listHref(page) {
    return "/compact?" + buildQuery(page);
  }

  function imageHref(id, page) {
    return "/compact/image/" + encode(id) + "?" + buildQuery(page);
  }

  function apiListUrl(page) {
    return "/api/images?" + buildQuery(page);
  }

  function apiDetailUrl(id) {
    return "/api/images/" + encode(id);
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

  function formatSize(bytes) {
    var number = parseInt(bytes, 10);
    if (isNaN(number)) {
      return "-";
    }
    return Math.round(number / 1024) + " KB";
  }

  function showError(message) {
    detailError.innerHTML = escapeHtml(message);
    detailError.className = "messageBox errorBox";
  }

  function hideError() {
    detailError.innerHTML = "";
    detailError.className = "messageBox errorBox hidden";
  }

  function renderSimpleList(items, key) {
    var html = "";
    var i;
    var item;
    if (!items || !items.length) {
      return '<div class="contentBox">None</div>';
    }
    html += '<ul class="simpleList">';
    for (i = 0; i < items.length; i += 1) {
      item = items[i];
      html += '<li><strong>' + escapeHtml(item[key] || "") + '</strong><span class="itemHint">'
        + escapeHtml(item.class_type || "") + "." + escapeHtml(item.field || "") + " [" + escapeHtml(item.node || "") + "]"
        + '</span></li>';
    }
    html += "</ul>";
    return html;
  }

  function renderRelated(items, currentId) {
    var html = "";
    var i;
    var item;
    var title;
    if (!items || items.length <= 1) {
      return "";
    }
    html += '<div class="relatedList">';
    for (i = 0; i < items.length; i += 1) {
      item = items[i];
      title = item.title || item.file_name;
      if (String(item.id) === String(currentId)) {
        html += '<div class="relatedItem"><span class="relatedThumb"><img src="' + escapeHtml(item.thumb_url) + '" alt="' + escapeHtml(title) + '"></span><div class="relatedCurrent">Current</div></div>';
      } else {
        html += '<a class="relatedItem" href="' + imageHref(item.id, state.page) + '"><span class="relatedThumb"><img src="' + escapeHtml(item.thumb_url) + '" alt="' + escapeHtml(title) + '"></span></a>';
      }
    }
    html += "</div>";
    return html;
  }

  function renderDetail(item) {
    var prompt = "-";
    var title = item.title || item.file_name;
    var relatedHtml = "";
    if (item.longest_prompt_detail && item.longest_prompt_detail.text) {
      prompt = item.longest_prompt_detail.text;
    }
    document.title = title + " - Prompt Viewer Compact";
    previewImage.src = item.media_url;
    previewImage.alt = title;
    openOriginalLink.href = item.media_url;
    relatedHtml = item.related_images && item.related_images.length > 1
      ? '<h2 class="sectionTitle relatedTitle">Related</h2>' + renderRelated(item.related_images, item.id)
      : "";
    if (relatedHtml) {
      relatedBlock.innerHTML = relatedHtml;
      relatedBlock.className = "compactRelated";
    } else {
      relatedBlock.innerHTML = "";
      relatedBlock.className = "compactRelated hidden";
    }
    detail.innerHTML = ''
      + '<div class="detailHeader">'
      + '<h1>' + escapeHtml(title) + '</h1>'
      + '</div>'
      + '<table class="kvTable">'
      + '<tr><th>Source</th><td>' + escapeHtml(item.source) + '</td></tr>'
      + '<tr><th>Path</th><td>' + escapeHtml(item.relative_path) + '</td></tr>'
      + '<tr><th>Dimensions</th><td>' + escapeHtml(item.width || "?") + ' x ' + escapeHtml(item.height || "?") + '</td></tr>'
      + '<tr><th>Size</th><td>' + escapeHtml(formatSize(item.size_bytes)) + '</td></tr>'
      + '<tr><th>Date</th><td>' + escapeHtml(formatDate(item.mtime, item.generated_at)) + '</td></tr>'
      + '</table>'
      + (item.parse_error ? '<div class="messageBox errorBox">' + escapeHtml(item.parse_error) + '</div>' : '')
      + '<h2 class="sectionTitle">Prompt</h2>'
      + '<div class="contentBox">' + escapeHtml(prompt) + '</div>'
      + '<h2 class="sectionTitle">Models</h2>'
      + renderSimpleList(item.models, "model")
      + (item.source === "comfyui" ? '<h2 class="sectionTitle">LoRAs</h2>' + renderSimpleList(item.loras, "lora") : '');
  }

  function updateContext(data, id) {
    var page = data.page || state.page;
    var perPage = data.per_page || state.perPage;
    var total = data.total || 0;
    var items = data.items || [];
    var i;
    state.page = page;
    state.perPage = perPage;
    state.total = total;
    state.items = items;
    state.selectedIndex = -1;
    for (i = 0; i < items.length; i += 1) {
      if (String(items[i].id) === String(id)) {
        state.selectedIndex = i;
        break;
      }
    }
    backLink.href = listHref(state.page);
    updateNav();
  }

  function updateNav() {
    var absoluteIndex = 0;
    if (state.selectedIndex >= 0) {
      absoluteIndex = (state.page - 1) * state.perPage + state.selectedIndex + 1;
    }
    positionLabel.innerHTML = absoluteIndex ? absoluteIndex + " of " + state.total : "";
    prevImage.disabled = absoluteIndex <= 1;
    nextImage.disabled = absoluteIndex >= state.total;
  }

  function loadContext(id, callback) {
    requestJson(apiListUrl(state.page), function (error, data) {
      if (error) {
        callback(error);
        return;
      }
      updateContext(data, id);
      callback(null);
    });
  }

  function loadDetail(id, callback) {
    requestJson(apiDetailUrl(id), function (error, item) {
      if (error) {
        callback(error);
        return;
      }
      renderDetail(item);
      callback(null);
    });
  }

  function navigate(direction) {
    var nextIndex = state.selectedIndex + direction;
    var maxPage;
    if (nextIndex >= 0 && nextIndex < state.items.length) {
      window.location.href = imageHref(state.items[nextIndex].id, state.page);
      return;
    }
    maxPage = Math.ceil(state.total / state.perPage);
    if (direction < 0 && state.page > 1) {
      requestJson(apiListUrl(state.page - 1), function (error, data) {
        var items;
        if (error) {
          showError(error.message);
          return;
        }
        items = data.items || [];
        if (items.length) {
          window.location.href = imageHref(items[items.length - 1].id, data.page || (state.page - 1));
        }
      });
      return;
    }
    if (direction > 0 && state.page < maxPage) {
      requestJson(apiListUrl(state.page + 1), function (error, data) {
        var items;
        if (error) {
          showError(error.message);
          return;
        }
        items = data.items || [];
        if (items.length) {
          window.location.href = imageHref(items[0].id, data.page || (state.page + 1));
        }
      });
    }
  }

  prevImage.onclick = function () {
    navigate(-1);
  };

  nextImage.onclick = function () {
    navigate(1);
  };

  previewImage.onclick = function (event) {
    var rect;
    var midpoint;
    var clientX;
    if (!previewImage.src) {
      return;
    }
    rect = previewImage.getBoundingClientRect ? previewImage.getBoundingClientRect() : null;
    if (!rect) {
      return;
    }
    clientX = typeof event.clientX === "number" ? event.clientX : 0;
    midpoint = rect.left + ((rect.right - rect.left) / 2);
    if (clientX < midpoint) {
      navigate(-1);
      return;
    }
    navigate(1);
  };

  hideError();
  loadContext(imageId, function (contextError) {
    if (contextError) {
      showError(contextError.message);
      detail.innerHTML = '<div class="messageBox errorBox">Unable to load image context.</div>';
      return;
    }
    loadDetail(imageId, function (detailLoadError) {
      if (detailLoadError) {
        showError(detailLoadError.message);
        detail.innerHTML = '<div class="messageBox errorBox">Unable to load image detail.</div>';
      }
    });
  });
}());
