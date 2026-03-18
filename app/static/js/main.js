// static/js/main.js
// Improved formatting, null-safety, and moved import handler into initUI()

import {
  fetchEntries,
  fetchScanServices,
  runScan,
  applyScanItem,
  rejectScanItem,
  clearScanRejections,
  fetchBibFiles,
  selectBibFile,
  undoLast,
  previewImportFile,
  importEntries as importSelectedEntries,
  exportEntries as requestExportEntries,
  fetchHistory,
  restoreHistory,
} from "./api.js";
import { buildIndex, applyFilters } from "./filters.js";
import { createCard, createStatusIcons, getIconClass, extractLatexUrl } from "./renderer.js";

let allEntries = [];
let filteredEntries = [];
let currentEntry = null;

// Elements (set in initUI)
let grid = null;
let editor = null;
let searchInput = null;

let sortDir = "desc";
let viewMode = "grid"; // "grid" or "list"
let renderToken = 0;
let pickerState = null;
let scanState = {
  services: [],
  running: false,
  currentService: "",
  statusText: "",
};
let dragDepth = 0;
let toastTimer = null;
let lastSelectedCardKey = null;
let resizeTimer = null;

const RENDER_BATCH_SIZE = 80;
const GRID_COLUMN_MIN_WIDTH = 260;
const GRID_COLUMN_GAP = 12;

function escapeHtml(value = "") {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatAbsoluteTimestamp(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatRelativeTimestamp(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";

  const diffMs = date.getTime() - Date.now();
  const absSeconds = Math.abs(diffMs) / 1000;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

  if (absSeconds < 45) return "just now";
  if (absSeconds < 90) return rtf.format(Math.round(diffMs / 60000), "minute");
  if (absSeconds < 2700) return rtf.format(Math.round(diffMs / 3600000), "hour");
  if (absSeconds < 64800) return rtf.format(Math.round(diffMs / 86400000), "day");
  if (absSeconds < 1944000) return rtf.format(Math.round(diffMs / 2592000000), "month");
  return rtf.format(Math.round(diffMs / 31536000000), "year");
}

function formatTimestampLabel(value) {
  const absolute = formatAbsoluteTimestamp(value);
  const relative = formatRelativeTimestamp(value);
  if (absolute && relative) {
    return `${absolute} (${relative})`;
  }
  return absolute || relative || value || "";
}

async function loadEntries() {
  allEntries = await fetchEntries();
  buildIndex(allEntries);
  filterAndRender();
}

async function refreshBibFileButton() {
  const btn = getEl("bibFileBtn");
  if (!btn) return;
  const res = await fetchBibFiles();
  if (!res.ok) {
    throw new Error(res.description || res.error || "Failed to load bib files");
  }
  const current = (res.items || []).find((item) => item.selected);
  btn.textContent = current ? current.filename : "main.bib";
}

function findEntryByKey(key) {
  return allEntries.find((entry) => entry.key === key) || null;
}

function showToast(message) {
  const toast = getEl("toastHint");
  if (!toast) return;
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    toast.classList.remove("show");
  }, 1800);
}

function filteredPickerItems() {
  if (!pickerState) return [];
  const query = pickerState.query.trim().toLowerCase();
  if (!query) return pickerState.items;
  return pickerState.items.filter((item) => item.searchText.includes(query));
}

function updatePickerInfo() {
  if (!pickerState) return;
  const info = getEl("pickerSelectionInfo");
  if (!info) return;
  const selectedCount = pickerState.items.filter((item) => item.selected).length;
  if (pickerState.singleSelect) {
    info.textContent = selectedCount ? "1 selected" : "No selection";
    return;
  }
  info.textContent = `${selectedCount} selected`;
}

function updatePickerPreview() {
  const preview = getEl("pickerPreview");
  const previewLabel = getEl("pickerPreviewLabel");
  const previewText = getEl("pickerPreviewText");
  const body = preview ? preview.parentElement : null;
  if (!preview || !previewLabel || !previewText) return;

  if (!pickerState || !pickerState.showPreview) {
    preview.classList.remove("open");
    body?.classList.remove("preview-split");
    previewLabel.textContent = "";
    previewText.innerHTML = "";
    return;
  }

  const selectedItem = pickerState.items.find((item) => item.selected) || null;
  preview.classList.add("open");
  body?.classList.add("preview-split");
  previewLabel.textContent = pickerState.previewLabel || "";
  if (!selectedItem) {
    previewText.innerHTML = `<div class="text-muted small">${escapeHtml(pickerState.previewEmptyText || "")}</div>`;
    return;
  }

  if (pickerState.previewRenderer) {
    previewText.innerHTML = pickerState.previewRenderer(selectedItem);
    return;
  }

  previewText.innerHTML = `<div class="small"><pre>${escapeHtml(selectedItem.preview || pickerState.previewEmptyText || "")}</pre></div>`;
}

function splitDiffWords(text = "") {
  const tokens = String(text).match(/\S+|\s+/g) || [];
  return tokens;
}

function diffMarkup(before = "", after = "") {
  const a = splitDiffWords(before);
  const b = splitDiffWords(after);
  const dp = Array.from({ length: a.length + 1 }, () => Array(b.length + 1).fill(0));

  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  let i = 0;
  let j = 0;
  let beforeHtml = "";
  let afterHtml = "";
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      const shared = escapeHtml(a[i]);
      beforeHtml += shared;
      afterHtml += shared;
      i += 1;
      j += 1;
      continue;
    }
    if (dp[i + 1][j] >= dp[i][j + 1]) {
      beforeHtml += `<span class="history-before">${escapeHtml(a[i]) || "&nbsp;"}</span>`;
      i += 1;
    } else {
      afterHtml += `<span class="history-after">${escapeHtml(b[j]) || "&nbsp;"}</span>`;
      j += 1;
    }
  }
  while (i < a.length) {
    beforeHtml += `<span class="history-before">${escapeHtml(a[i]) || "&nbsp;"}</span>`;
    i += 1;
  }
  while (j < b.length) {
    afterHtml += `<span class="history-after">${escapeHtml(b[j]) || "&nbsp;"}</span>`;
    j += 1;
  }

  return {
    before: beforeHtml || escapeHtml(before),
    after: afterHtml || escapeHtml(after),
  };
}

function historyChangeIcon(changeType) {
  if (changeType === "added") return { icon: "fa-plus-circle", color: "#198754", label: "Added" };
  if (changeType === "removed") return { icon: "fa-minus-circle", color: "#dc3545", label: "Removed" };
  return { icon: "fa-pencil", color: "#fd7e14", label: "Edited" };
}

function importStatusBadge(status) {
  if (status === "new") return "New";
  if (status === "same") return "Unchanged";
  if (status === "conflict") return "Conflict";
  return "";
}

function qualityStatusBadge(item) {
  const count = item.patch?.changed_fields?.length || 0;
  const labels = [];
  if ((item.status_flags || []).includes("retracted")) labels.push("Retracted");
  if ((item.status_flags || []).includes("withdrawn")) labels.push("Withdrawn");
  if (count) labels.push(`${count} field${count === 1 ? "" : "s"}`);
  return labels.join(" • ") || "Patch";
}

function getSelectedPickerItem() {
  if (!pickerState) return null;
  return pickerState.items.find((item) => item.selected) || null;
}

function removePickerItem(itemId) {
  if (!pickerState) return;
  const nextItems = pickerState.items.filter((item) => item.id !== itemId);
  pickerState.items = nextItems;
  if (nextItems.length && !nextItems.some((item) => item.selected)) {
    nextItems[0].selected = true;
  }
  if (!nextItems.length) {
    closePicker();
    return;
  }
  renderPickerList();
}

function renderImportPreview(item) {
  if (item.status === "new") {
    return `<div class="text-muted small">This entry is new and will be added if selected.</div>`;
  }

  if (item.status === "same") {
    return `<div class="text-muted small">This entry matches the current bibliography entry exactly.</div>`;
  }

  const conflict = item.conflict;
  if (!conflict || !Array.isArray(conflict.changed_fields) || !conflict.changed_fields.length) {
    return `<div class="text-muted small">This entry differs from the current bibliography entry.</div>`;
  }

  const summary = pickerMeta([
    conflict.existing?.author || conflict.incoming?.author,
    conflict.existing?.year || conflict.incoming?.year,
    conflict.existing?.type || conflict.incoming?.type,
  ]);

  const fieldsHtml = conflict.changed_fields.slice(0, 6).map((field) => {
    const fragments = diffMarkup(field.before || "", field.after || "");
    return `
      <div class="history-field">
        <span class="text-muted">${escapeHtml(field.field)}:</span>
        <span>${fragments.before || "&nbsp;"}</span>
        <span class="history-arrow">→</span>
        <span>${fragments.after || "&nbsp;"}</span>
      </div>
    `;
  }).join("");

  return `
    <div class="fw-semibold">${escapeHtml(conflict.incoming?.title || item.title || "(No title)")}</div>
    <div class="picker-meta mb-2">${escapeHtml(summary || "")}</div>
    <div class="history-change-fields">${fieldsHtml}</div>
  `;
}

function renderQualityPreview(item) {
  const changes = item.patch?.changed_fields || [];
  const statusFlags = item.status_flags || [];
  const statusHtml = statusFlags.length
    ? `<div class="picker-meta mb-2">${escapeHtml(statusFlags.join(" • "))}</div>`
    : "";
  const fieldsHtml = changes.length
    ? changes.map((field) => {
      const fragments = diffMarkup(field.before || "", field.after || "");
      return `
        <div class="history-field">
          <span class="text-muted">${escapeHtml(field.field)}:</span>
          <span>${fragments.before || "&nbsp;"}</span>
          <span class="history-arrow">→</span>
          <span>${fragments.after || "&nbsp;"}</span>
        </div>
      `;
    }).join("")
    : `<div class="text-muted small">No field-level changes were proposed.</div>`;

  const provenanceBits = [
    item.provenance?.source || item.source,
    item.provenance?.identifier_used,
    formatTimestampLabel(item.provenance?.scanned_at || ""),
  ].filter(Boolean).join(" • ");

  return `
    <div class="fw-semibold">${escapeHtml(item.title || "(No title)")}</div>
    <div class="picker-meta">${escapeHtml(item.summary || "")}</div>
    ${statusHtml}
    ${provenanceBits ? `<div class="picker-meta mb-2">${escapeHtml(provenanceBits)}</div>` : ""}
    <div class="history-change-fields mb-3">${fieldsHtml}</div>
    <div class="scan-preview-raw">
      <div>
        <div class="small text-muted mb-1">Current</div>
        <pre>${escapeHtml(item.current_raw || "")}</pre>
      </div>
      <div>
        <div class="small text-muted mb-1">Proposed</div>
        <pre>${escapeHtml(item.proposed_raw || "")}</pre>
      </div>
    </div>
  `;
}

function renderHistoryChanges(item) {
  const changes = item.changes || [];
  if (!changes.length) {
    return `<div class="text-muted small">No entry-level change summary is available for this revision.</div>`;
  }

  return changes.map((change) => {
    const icon = historyChangeIcon(change.change_type);
    const title = change.title_after || change.title_before || "(No title)";
    const meta = pickerMeta([
      change.author_after || change.author_before,
      change.year_after || change.year_before,
      change.entry_type,
    ]);

    let fieldsHtml = "";
    if (change.change_type === "edited" && Array.isArray(change.changed_fields) && change.changed_fields.length) {
      const rows = change.changed_fields.slice(0, 4).map((field) => {
        const fragments = diffMarkup(field.before || "", field.after || "");
        return `
          <div class="history-field">
            <span class="text-muted">${escapeHtml(field.field)}:</span>
            <span>${fragments.before || "&nbsp;"}</span>
            <span class="history-arrow">→</span>
            <span>${fragments.after || "&nbsp;"}</span>
          </div>
        `;
      }).join("");
      fieldsHtml = `<div class="history-change-fields">${rows}</div>`;
    }

    return `
      <div class="history-change">
        <div class="history-change-icon" title="${escapeHtml(icon.label)}">
          <i class="fa ${icon.icon}" style="color:${icon.color}" aria-hidden="true"></i>
        </div>
        <div class="picker-item-main">
          <div class="d-flex flex-wrap align-items-center gap-2">
            <span class="picker-key">${escapeHtml(change.key || "(no key)")}</span>
            <span class="badge text-bg-light picker-badge">${escapeHtml(icon.label)}</span>
          </div>
          <div class="fw-semibold">${escapeHtml(title)}</div>
          <div class="picker-meta">${escapeHtml(meta || "")}</div>
          ${fieldsHtml}
        </div>
      </div>
    `;
  }).join("");
}

function renderPickerList() {
  if (!pickerState) return;
  const list = getEl("pickerList");
  if (!list) return;

  const items = filteredPickerItems();
  list.innerHTML = "";

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "text-muted small py-3";
    empty.textContent = pickerState.emptyMessage || "No entries found.";
    list.appendChild(empty);
    updatePickerInfo();
    updatePickerPreview();
    return;
  }

  const frag = document.createDocumentFragment();
  for (const item of items) {
    const row = document.createElement("label");
    row.className = "picker-item";
    const selectorType = pickerState?.singleSelect ? "radio" : "checkbox";
    row.innerHTML = `
      <input type="${selectorType}" class="form-check-input mt-1" ${pickerState?.singleSelect ? 'name="pickerSelection"' : ""}>
      <div class="picker-item-main">
        <div class="d-flex flex-wrap align-items-center gap-2">
          <span class="picker-key">${escapeHtml(item.key || "(no key)")}</span>
          ${item.badge ? `<span class="badge text-bg-light picker-badge">${escapeHtml(item.badge)}</span>` : ""}
        </div>
        <div class="fw-semibold">${escapeHtml(item.title || "(No title)")}</div>
        <div class="picker-meta">${escapeHtml(item.meta || "")}</div>
      </div>
    `;

    const checkbox = row.querySelector("input");
    checkbox.checked = Boolean(item.selected);
    checkbox.addEventListener("change", () => {
      if (pickerState?.singleSelect && checkbox.checked) {
        for (const candidate of pickerState.items) {
          candidate.selected = false;
        }
      }
      item.selected = checkbox.checked;
      renderPickerList();
    });

    frag.appendChild(row);
  }

  list.appendChild(frag);
  updatePickerInfo();
  updatePickerPreview();
}

function renderPickerActions() {
  const actions = getEl("pickerActions");
  if (!actions) return;
  actions.querySelectorAll(".picker-action-custom").forEach((node) => node.remove());

  const customActions = pickerState?.actions || [];
  const confirmBtn = getEl("pickerConfirmBtn");
  if (!customActions.length) {
    if (confirmBtn) confirmBtn.style.display = "";
    return;
  }

  if (confirmBtn) confirmBtn.style.display = "none";

  customActions.forEach((action) => {
    const button = document.createElement("button");
    button.className = `picker-action-custom ${action.className || "btn btn-sm btn-outline-secondary"}`;
    button.textContent = action.label;
    button.addEventListener("click", async () => {
      const selectedItem = getSelectedPickerItem();
      if (action.requiresSelection !== false && !selectedItem) {
        alert("Select an entry.");
        return;
      }

      button.disabled = true;
      try {
        await action.onClick(selectedItem);
      } catch (err) {
        console.error(`${pickerState?.mode || "picker"} action failed:`, err);
        alert(err.message || "Action failed");
      } finally {
        button.disabled = false;
      }
    });
    actions.appendChild(button);
  });
}

function closePicker() {
  pickerState = null;
  const backdrop = getEl("pickerBackdrop");
  if (backdrop) {
    backdrop.classList.remove("open");
    backdrop.setAttribute("aria-hidden", "true");
  }
  const search = getEl("pickerSearch");
  if (search) search.value = "";
  const confirmBtn = getEl("pickerConfirmBtn");
  if (confirmBtn) confirmBtn.style.display = "";
  const actions = getEl("pickerActions");
  if (actions) {
    actions.querySelectorAll(".picker-action-custom").forEach((node) => node.remove());
  }
  updatePickerPreview();
}

function renderScanModal() {
  const backdrop = getEl("scanBackdrop");
  const list = getEl("scanServiceList");
  const status = getEl("scanStatus");
  const clearBtn = getEl("scanClearRejectionsBtn");
  const closeBtn = getEl("scanCloseBtn");
  if (!backdrop || !list || !status || !clearBtn || !closeBtn) return;

  list.innerHTML = "";
  const fragment = document.createDocumentFragment();
  for (const service of scanState.services) {
    const row = document.createElement("div");
    row.className = "scan-service";

    const info = document.createElement("div");
    info.className = "scan-service-info";
    info.innerHTML = `
      <div class="fw-semibold">${escapeHtml(service.label || service.name)}</div>
      <div class="small text-muted">${escapeHtml(service.reason || "")}</div>
    `;

    const button = document.createElement("button");
    button.className = "btn btn-sm btn-primary";
    button.textContent = scanState.running && scanState.currentService === service.name
      ? "Scan underway..."
      : `Run ${service.label || service.name}`;
    button.disabled = scanState.running || service.available === false;
    button.addEventListener("click", async () => {
      try {
        await startScanFromModal(service);
      } catch (err) {
        console.error("Scan failed:", err);
        alert(err.message || "Scan failed");
      }
    });

    row.appendChild(info);
    row.appendChild(button);
    fragment.appendChild(row);
  }
  list.appendChild(fragment);

  status.textContent = scanState.statusText || "Choose a scan source.";
  clearBtn.disabled = scanState.running;
  closeBtn.disabled = scanState.running;
}

function openScanModal() {
  const backdrop = getEl("scanBackdrop");
  if (!backdrop) return;
  backdrop.classList.add("open");
  backdrop.setAttribute("aria-hidden", "false");
  renderScanModal();
}

function closeScanModal() {
  const backdrop = getEl("scanBackdrop");
  if (!backdrop) return;
  backdrop.classList.remove("open");
  backdrop.setAttribute("aria-hidden", "true");
  scanState.running = false;
  scanState.currentService = "";
  scanState.statusText = "";
  renderScanModal();
}

async function loadScanServices() {
  const res = await fetchScanServices();
  if (!res.ok) {
    throw new Error(res.description || res.error || "Failed to load scan services");
  }
  scanState.services = res.items || [];
  renderScanModal();
}

function openPicker(config) {
  pickerState = {
    mode: config.mode,
    title: config.title,
    subtitle: config.subtitle || "",
    confirmText: config.confirmText || "Confirm",
    emptyMessage: config.emptyMessage || "No entries found.",
    items: config.items,
    query: "",
    onConfirm: config.onConfirm,
    singleSelect: Boolean(config.singleSelect),
    showPreview: Boolean(config.showPreview),
    previewLabel: config.previewLabel || "",
    previewEmptyText: config.previewEmptyText || "",
    previewRenderer: config.previewRenderer || null,
    actions: config.actions || [],
  };

  const backdrop = getEl("pickerBackdrop");
  const title = getEl("pickerTitle");
  const subtitle = getEl("pickerSubtitle");
  const search = getEl("pickerSearch");
  const confirmBtn = getEl("pickerConfirmBtn");
  const selectVisibleBtn = getEl("pickerSelectVisibleBtn");
  const clearBtn = getEl("pickerClearBtn");

  if (title) title.textContent = pickerState.title;
  if (subtitle) subtitle.textContent = pickerState.subtitle;
  if (search) search.value = "";
  if (confirmBtn) confirmBtn.textContent = pickerState.confirmText;
  if (selectVisibleBtn) {
    selectVisibleBtn.style.display = pickerState.singleSelect ? "none" : "";
  }
  if (clearBtn) {
    clearBtn.textContent = pickerState.singleSelect ? "Clear Selection" : "Clear";
  }
  if (backdrop) {
    backdrop.classList.add("open");
    backdrop.setAttribute("aria-hidden", "false");
  }

  renderPickerList();
  renderPickerActions();
}

function getSortField() {
  const active = document.querySelector(".sort-btn.active");
  return active ? active.dataset.sort : "year";
}

function filterAndRender() {
  const q = searchInput ? searchInput.value : "";
  const field = getSortField();
  const dir = sortDir;

  filteredEntries = applyFilters(q, field, dir);

  if (viewMode === "grid") {
    renderGrid();
  } else {
    renderList();
  }
}

function renderEmptyState(container) {
  if (!container) return;
  container.classList.add("view-empty");
  const hasQuery = Boolean((searchInput ? searchInput.value : "").trim());
  const message = allEntries.length === 0
    ? "This bibliography is empty."
    : hasQuery
      ? "No entries match the current search."
      : "No entries to display.";
  container.innerHTML = `<div class="empty-state">${escapeHtml(message)}</div>`;
}

function clearEmptyState(container) {
  if (!container) return;
  container.classList.remove("view-empty");
}

function getGridColumnCount() {
  if (!grid) return 1;
  const width = grid.clientWidth || 0;
  if (!width) return 1;
  return Math.max(1, Math.floor((width + GRID_COLUMN_GAP) / (GRID_COLUMN_MIN_WIDTH + GRID_COLUMN_GAP)));
}

function buildGridColumns() {
  if (!grid) return [];
  const count = getGridColumnCount();
  const columns = [];
  grid.innerHTML = "";
  for (let index = 0; index < count; index += 1) {
    const column = document.createElement("div");
    column.className = "grid-column";
    grid.appendChild(column);
    columns.push(column);
  }
  return columns;
}

function renderGrid() {
  if (!grid) return;

  const list = document.getElementById("list");
  if (list) list.style.display = "none";
  grid.style.display = "";
  clearEmptyState(grid);

  lastSelectedCardKey = null;
  if (!filteredEntries.length) {
    renderEmptyState(grid);
    return;
  }
  const token = ++renderToken;
  const columns = buildGridColumns();
  renderGridInBatches(filteredEntries, columns, token, (e) => {
    const card = createCard(e);
    card.addEventListener("click", () => selectEntry(e));
    return card;
  });
}

function renderList() {
  const list = document.getElementById("list");
  if (!list) {
    renderGrid();
    return;
  }

  if (grid) grid.style.display = "none";
  list.style.display = "block";
  clearEmptyState(list);

  list.innerHTML = "";
  if (!filteredEntries.length) {
    renderEmptyState(list);
    return;
  }
  const token = ++renderToken;
  renderInBatches(filteredEntries, list, token, createListEntry);
}

function renderInBatches(entries, container, token, buildNode) {
  let index = 0;

  function appendBatch() {
    if (token !== renderToken) return;

    const frag = document.createDocumentFragment();
    const limit = Math.min(index + RENDER_BATCH_SIZE, entries.length);

    for (; index < limit; index += 1) {
      frag.appendChild(buildNode(entries[index]));
    }

    container.appendChild(frag);
    updateSelectedCardState();

    if (index < entries.length) {
      requestAnimationFrame(appendBatch);
    }
  }

  requestAnimationFrame(appendBatch);
}

function renderGridInBatches(entries, columns, token, buildNode) {
  let index = 0;

  function appendBatch() {
    if (token !== renderToken || !columns.length) return;

    const limit = Math.min(index + RENDER_BATCH_SIZE, entries.length);
    for (; index < limit; index += 1) {
      const column = columns[index % columns.length];
      column.appendChild(buildNode(entries[index]));
    }

    updateSelectedCardState();

    if (index < entries.length) {
      requestAnimationFrame(appendBatch);
    }
  }

  requestAnimationFrame(appendBatch);
}

function createListEntry(entry) {

  const f = entry.fields || {};
  const type = (entry.type || "").toLowerCase();
  const iconClass = getIconClass(type);

  // ----- authors -----

  let authors = cleanLatex((f.author || f.editor || "").replace(/\n/g, " "));
  if (authors) {
    const parts = authors.split(/\s+and\s+/i).map(s => s.trim()).filter(Boolean);
    if (parts.length > 1) {
      authors = parts.slice(0, -1).join(", ") + ", & " + parts.slice(-1);
    }
  }

  const year = f.year ? ` (${cleanLatex(f.year)})` : "";
  const title = cleanLatex(f.title || "(No title)");

  // ----- source -----

  let source = "";

  if (f.journal) {
    source = `<i>${cleanLatex(f.journal)}</i>`;
    if (f.volume) source += `, ${cleanLatex(f.volume)}`;
    if (f.number) source += `(${cleanLatex(f.number)})`;
    if (f.pages) source += `, pp. ${cleanLatex(f.pages)}`;
  } else if (f.booktitle) {
    source = `<i>${cleanLatex(f.booktitle)}</i>`;
    if (f.publisher) source += `, ${cleanLatex(f.publisher)}`;
    if (f.pages) source += `, pp. ${cleanLatex(f.pages)}`;
  } else if (f.publisher) {
    source = cleanLatex(f.publisher);
  }

  // ----- build citation safely (prevents ". .") -----

  const parts = [];

  if (authors || year) {
    parts.push(`<span class="bib-entry-meta">${authors}${year}`.trim() + `</span>`);
  }

  if (title) {
    parts.push(`<span class="bib-entry-title">${title}</span>`);
  }

  if (source) {
    parts.push(`<span class="bib-entry-muted">${source}</span>`);
  }

  let citation = parts.join(". ");

  if (f.doi) {
    citation += `. DOI: ${cleanLatex(f.doi)}`;
  }

  if (citation && !citation.endsWith(".")) {
    citation += ".";
  }

  // ----- container -----

  const div = document.createElement("div");
  div.className = "mb-2";

  const iconSpan = document.createElement("span");
  iconSpan.className = "text-muted small me-1";
  iconSpan.innerHTML = `<i class="fa ${iconClass}" aria-hidden="true"></i>`;
  div.appendChild(iconSpan);

  const text = document.createElement("span");
  text.innerHTML = citation;
  div.appendChild(text);

  const statusIcons = createStatusIcons(entry.statuses || []);
  if (statusIcons) {
    statusIcons.classList.add("ms-2");
    div.appendChild(statusIcons);
  }

  // ----- action icons (PDF / URL / arXiv / DOI) -----

  const actions = document.createElement("span")
  actions.className = "ms-2 text-muted"

  function addIcon(href, icon, title, color=null) {

    const a = document.createElement("a")
    a.href = href
    a.target = "_blank"
    a.title = title
    a.className = "ms-1 text-decoration-none"

    const i = document.createElement("i")
    i.className = `fa ${icon}`

    if (color) i.style.color = color

    a.appendChild(i)

    // prevent click from selecting entry
    a.addEventListener("click", e => e.stopPropagation())

    actions.appendChild(a)
  }

  if (entry.has_pdf) {
    addIcon(`/pdf/${entry.key}.pdf`, "fa-file-pdf-o", "PDF", "#dc3545")
  }

  const url =
    f.url ||
    extractLatexUrl(f.howpublished) ||
    extractLatexUrl(f.note)

  if (url) {
    addIcon(url, "fa-link", "URL")
  }

  if (f.archiveprefix && f.archiveprefix.toLowerCase() === "arxiv" && f.eprint) {
    addIcon(`https://arxiv.org/abs/${f.eprint}`, "fa-external-link", "arXiv")
  }

  if (f.doi) {
    addIcon(`https://doi.org/${f.doi}`, "fa-bookmark", "DOI")
  }

  if (actions.children.length > 0)
    div.appendChild(actions)

  div.addEventListener("click", () => selectEntry(entry));

  return div;
}

// small helper used by createListEntry
function cleanLatex(s = "") {
  return String(s)
    .replace(/[{}`~^\\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function selectEntry(entry) {
  currentEntry = entry;
  if (editor) editor.value = entry.raw || "";
  updateSelectedCardState();
}

function updateSelectedCardState() {
  if (!grid) return;
  const selectedKey = currentEntry ? currentEntry.key : null;
  if (lastSelectedCardKey === selectedKey) {
    const existing = selectedKey ? grid.querySelector(`.bib-card[data-entry-key="${escapeSelector(selectedKey)}"]`) : null;
    if (selectedKey && existing) return;
  }

  if (lastSelectedCardKey) {
    const previous = grid.querySelector(`.bib-card[data-entry-key="${escapeSelector(lastSelectedCardKey)}"]`);
    previous?.classList.remove("is-selected");
  }
  if (selectedKey) {
    const next = grid.querySelector(`.bib-card[data-entry-key="${escapeSelector(selectedKey)}"]`);
    next?.classList.add("is-selected");
  }
  lastSelectedCardKey = selectedKey;
}

async function saveEntry() {
  try {
    if (!currentEntry) return;

    const previousKey = currentEntry.key;
    const raw = editor ? editor.value.trim() : "";
    if (raw === "") {
      if (!confirm("Delete this entry?")) return;
    }

    const res = await fetch(`/api/entry/${currentEntry.key}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw }),
    });
    if (!res.ok) {
      const message = await res.text();
      throw new Error(message || "Failed to save entry");
    }

    const data = await res.json().catch(() => ({}));
    await loadEntries();
    currentEntry = findEntryByKey(data.key || previousKey);
    if (currentEntry && editor) {
      editor.value = currentEntry.raw || "";
    } else if (editor && raw === "") {
      editor.value = "";
    }
    if (data.deleted) {
      showToast(`Deleted ${previousKey}`);
    } else {
      showToast(`Saved ${data.key || previousKey}`);
    }
  } catch (err) {
    console.error("Save failed:", err);
    alert(err.message || "Failed to save entry");
  }
}

function cancelEdit() {
  if (currentEntry && editor) editor.value = currentEntry.raw || "";
}

async function addEntry() {
  try {
    const raw = editor ? editor.value.trim() : "";
    if (!raw) {
      alert("No entry content");
      return;
    }

    const res = await fetch("/api/entry", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ raw }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.description || data.error || "Failed to add entry");
    }

    await loadEntries();
    currentEntry = findEntryByKey(data.key);
    if (currentEntry && editor) {
      editor.value = currentEntry.raw || "";
    }
    showToast(`Added ${data.key}`);
  } catch (err) {
    console.error("Add failed:", err);
    alert(err.message || "Failed to add entry");
  }
}

async function handleUndo() {
  try {
    const res = await undoLast();
    if (res && res.ok) {
      const selectedKey = currentEntry ? currentEntry.key : null;
      await loadEntries();
      currentEntry = selectedKey ? findEntryByKey(selectedKey) : null;
      if (editor) {
        editor.value = currentEntry ? currentEntry.raw || "" : "";
      }
      showToast("Undid last change");
      return;
    }

    throw new Error((res && (res.description || res.error)) || "Undo failed");
  } catch (err) {
    console.error("Undo failed:", err);
    alert(err.message || "Undo failed");
  }
}

function copyCurrent() {
  if (!currentEntry) return;
  navigator.clipboard.writeText(currentEntry.raw || "");
}

function pickerMeta(titleParts) {
  return titleParts.filter(Boolean).join(" • ");
}

function escapeSelector(value = "") {
  if (window.CSS && typeof window.CSS.escape === "function") {
    return window.CSS.escape(value);
  }
  return String(value).replace(/["\\]/g, "\\$&");
}

function formatImportToast(importedCount = 0, updatedCount = 0) {
  const parts = [];
  if (importedCount) {
    parts.push(`Imported ${importedCount} item${importedCount === 1 ? "" : "s"}`);
  }
  if (updatedCount) {
    parts.push(`updated ${updatedCount} item${updatedCount === 1 ? "" : "s"}`);
  }
  if (!parts.length) {
    return "No entries changed";
  }
  return parts.join(" and ");
}

function buildImportItems(entries) {
  return entries.map((entry, index) => ({
    id: `import-${index}`,
    key: entry.key || "",
    title: entry.title || "",
    meta: pickerMeta([entry.author, entry.year, entry.type]),
    raw: entry.raw || "",
    selected: Boolean(entry.selected),
    badge: importStatusBadge(entry.status),
    status: entry.status || (entry.exists ? "same" : "new"),
    conflict: entry.conflict || null,
    searchText: [
      entry.key,
      entry.title,
      entry.author,
      entry.year,
      entry.type,
      entry.status,
      ...(entry.conflict?.changed_fields || []).flatMap((field) => [field.field, field.before, field.after]),
    ]
      .join(" ")
      .toLowerCase(),
  }));
}

function buildScanReviewItems(items) {
  return items.map((item, index) => ({
    ...item,
    id: item.id,
    key: item.key || "",
    title: item.title || "",
    meta: item.summary || "",
    selected: index === 0,
    badge: qualityStatusBadge(item),
    searchText: [
      item.key,
      item.title,
      item.summary,
      item.source,
      item.provenance?.identifier_used,
      ...(item.status_flags || []),
      ...((item.patch?.changed_fields || []).flatMap((field) => [field.field, field.before, field.after])),
    ]
      .join(" ")
      .toLowerCase(),
  }));
}

function buildExportItems() {
  return allEntries.map((entry) => ({
    id: `export-${entry.key}`,
    key: entry.key || "",
    title: cleanLatex(entry.fields?.title || ""),
    meta: pickerMeta([
      cleanLatex(entry.fields?.author || entry.fields?.editor || ""),
      cleanLatex(entry.fields?.year || ""),
      entry.type || "",
    ]),
    raw: entry.raw || "",
    selected: false,
    badge: "",
    searchText: [
      entry.key,
      entry.fields?.title,
      entry.fields?.author,
      entry.fields?.editor,
      entry.fields?.year,
      entry.type,
    ]
      .join(" ")
      .toLowerCase(),
  }));
}

function buildHistoryItems(items) {
  return items.map((item) => ({
    id: item.id,
    key: item.id,
    title: formatTimestampLabel(item.timestamp) || item.id,
    meta: pickerMeta([
      item.action || "save",
      `${item.entries_before} → ${item.entries_after} entries`,
      `${item.added_count || 0} added`,
      `${item.edited_count || 0} edited`,
      `${item.removed_count || 0} removed`,
    ]),
    selected: false,
    badge: "Revision",
    changes: item.changes || [],
    searchText: [
      item.id,
      item.timestamp,
      item.action,
      item.entries_before,
      item.entries_after,
      item.added_count,
      item.edited_count,
      item.removed_count,
      ...(item.changes || []).flatMap((change) => [
        change.key,
        change.title_before,
        change.title_after,
        change.author_before,
        change.author_after,
        change.year_before,
        change.year_after,
        ...(change.changed_fields || []).flatMap((field) => [field.field, field.before, field.after]),
      ]),
    ]
      .join(" ")
      .toLowerCase(),
  }));
}

function buildBibFileItems(items) {
  return items.map((item) => ({
    id: item.filename,
    key: item.filename,
    title: item.filename,
    meta: pickerMeta([
      `${item.entry_count} entries`,
      `created ${formatTimestampLabel(item.created_at)}`,
      `updated ${formatTimestampLabel(item.modified_at)}`,
    ]),
    selected: Boolean(item.selected),
    badge: item.selected ? "Current" : "",
    searchText: [item.filename, item.entry_count, item.created_at, item.modified_at]
      .join(" ")
      .toLowerCase(),
  }));
}

async function openScanReviewPicker(scanResult) {
  const items = buildScanReviewItems(scanResult.items || []);
  if (!items.length) {
    showToast(`No actionable ${scanResult.label || "scan"} updates found`);
    return;
  }

  openPicker({
    mode: "quality-scan",
    title: `${scanResult.label || "Scan"} Review`,
    subtitle: "Review each proposed BibLaTeX amendment. Nothing is written until you accept or save an edited proposal.",
    confirmText: "Apply",
    emptyMessage: "No actionable entries in this scan.",
    items,
    singleSelect: true,
    showPreview: true,
    previewLabel: "Proposed amendment",
    previewEmptyText: "Select an entry to preview its proposed amendment.",
    previewRenderer: renderQualityPreview,
    actions: [
      {
        label: "Accept",
        className: "btn btn-sm btn-primary",
        onClick: async (selected) => {
          const applyRes = await applyScanItem(selected);
          if (!applyRes.ok) {
            throw new Error(applyRes.description || applyRes.error || "Failed to apply patch");
          }
          await loadEntries();
          currentEntry = findEntryByKey(selected.key);
          if (currentEntry && editor) {
            editor.value = currentEntry.raw || "";
          }
          removePickerItem(selected.id);
          showToast(`Applied scan patch for ${selected.key}`);
        },
      },
      {
        label: "Edit",
        className: "btn btn-sm btn-outline-secondary",
        onClick: async (selected) => {
          currentEntry = findEntryByKey(selected.key);
          if (editor) {
            editor.value = selected.proposed_raw || "";
          }
          updateSelectedCardState();
          closePicker();
          showToast(`Loaded proposed patch for ${selected.key}`);
        },
      },
      {
        label: "Reject",
        className: "btn btn-sm btn-outline-danger",
        onClick: async (selected) => {
          const suppress = window.confirm("Suppress this exact suggestion on future scans?\nChoose OK to suppress or Cancel to dismiss it for now.");
          const rejectRes = await rejectScanItem(selected, suppress);
          if (!rejectRes.ok) {
            throw new Error(rejectRes.description || rejectRes.error || "Failed to reject suggestion");
          }
          removePickerItem(selected.id);
          showToast(suppress ? `Suppressed ${selected.key}` : `Rejected ${selected.key}`);
        },
      },
      {
        label: "Close",
        className: "btn btn-sm btn-outline-secondary",
        requiresSelection: false,
        onClick: async () => {
          closePicker();
        },
      },
    ],
  });
}

async function startScanFromModal(service) {
  scanState.running = true;
  scanState.currentService = service.name;
  scanState.statusText = `${service.label || service.name} scan underway... this can take a while.`;
  renderScanModal();

  try {
    const res = await runScan(service.name);
    if (!res.ok) {
      throw new Error(res.description || res.error || "Scan failed");
    }
    scanState.running = false;
    scanState.currentService = "";
    scanState.statusText = `${service.label || service.name} scan finished.`;
    renderScanModal();
    closeScanModal();
    await openScanReviewPicker(res);
  } catch (err) {
    scanState.running = false;
    scanState.currentService = "";
    scanState.statusText = err.message || "Scan failed";
    renderScanModal();
    throw err;
  }
}

async function handleClearScanRejections() {
  const res = await clearScanRejections();
  if (!res.ok) {
    throw new Error(res.description || res.error || "Failed to clear past rejections");
  }
  scanState.statusText = res.cleared
    ? `Cleared ${res.cleared} past rejection${res.cleared === 1 ? "" : "s"}.`
    : "No past rejections were stored.";
  renderScanModal();
  showToast(scanState.statusText);
}

async function runImport(file) {
  const preview = await previewImportFile(file);
  if (!preview.ok) {
    throw new Error(preview.description || preview.error || "Failed to read import file");
  }

  const items = buildImportItems(preview.entries || []);
  if (!items.length) {
    throw new Error("No BibTeX entries found in file");
  }

  openPicker({
    mode: "import",
    title: "Import Entries",
    subtitle: `${file.name} • new entries are preselected, conflicts require explicit selection`,
    confirmText: "Import",
    emptyMessage: "No importable entries found.",
    items,
    showPreview: true,
    previewLabel: "Import comparison",
    previewEmptyText: "Select an entry to preview differences.",
    previewRenderer: renderImportPreview,
    onConfirm: async (selectedItems) => {
      const res = await importSelectedEntries(selectedItems.map((item) => item.raw));
      if (!res.ok) {
        throw new Error(res.description || res.error || "Import failed");
      }
      await loadEntries();
      showToast(formatImportToast(res.imported_count || 0, res.updated_count || 0));
    },
  });
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function openExportPicker() {
  openPicker({
    mode: "export",
    title: "Export Entries",
    subtitle: "Search and select the entries to export.",
    confirmText: "Export",
    emptyMessage: "No entries available to export.",
    items: buildExportItems(),
    onConfirm: async (selectedItems) => {
      const res = await requestExportEntries(selectedItems.map((item) => item.key));
      if (!res.ok) {
        throw new Error(res.error || "Export failed");
      }
      downloadBlob(res.blob, "export.bib");
      showToast(`Exported ${res.exportedCount || selectedItems.length} items`);
    },
  });
}

async function openHistoryPicker() {
  const res = await fetchHistory();
  if (!res.ok) {
    throw new Error(res.description || res.error || "Failed to load history");
  }

  const items = buildHistoryItems(res.items || []);
  openPicker({
    mode: "history",
    title: "History",
    subtitle: "Select one revision to inspect and restore.",
    confirmText: "Restore",
    emptyMessage: "No history available yet.",
    items,
    singleSelect: true,
    showPreview: true,
    previewLabel: "Changed entries",
    previewEmptyText: "Select a revision to preview its changed entries.",
    previewRenderer: renderHistoryChanges,
    onConfirm: async (selectedItems) => {
      const [selected] = selectedItems;
      const restoreRes = await restoreHistory(selected.id);
      if (!restoreRes.ok) {
        throw new Error(restoreRes.description || restoreRes.error || "Restore failed");
      }
      const selectedKey = currentEntry ? currentEntry.key : null;
      await loadEntries();
      currentEntry = selectedKey ? findEntryByKey(selectedKey) : null;
      if (editor) {
        editor.value = currentEntry ? currentEntry.raw || "" : "";
      }
      showToast(`Restored ${selected.title}`);
    },
  });
}

async function openBibFilePicker() {
  const res = await fetchBibFiles();
  if (!res.ok) {
    throw new Error(res.description || res.error || "Failed to load bib files");
  }

  openPicker({
    mode: "bib-file",
    title: "Select Bib File",
    subtitle: "Choose the active bibliography from bib/.",
    confirmText: "Use File",
    emptyMessage: "No bib files found in bib/.",
    items: buildBibFileItems(res.items || []),
    singleSelect: true,
    onConfirm: async (selectedItems) => {
      const [selected] = selectedItems;
      const selectRes = await selectBibFile(selected.id);
      if (!selectRes.ok) {
        throw new Error(selectRes.description || selectRes.error || "Failed to switch bib file");
      }
      currentEntry = null;
      if (editor) editor.value = "";
      await loadEntries();
      await refreshBibFileButton();
      showToast(`Switched to ${selected.id}`);
    },
  });
}

async function openImportFilePicker() {
  const input = getEl("bibFileInput");
  if (!input) return;
  input.value = "";
  input.click();
}

function getEl(id) {
  return document.getElementById(id);
}

function initUI() {

  grid = getEl("grid");
  editor = getEl("editRaw");
  searchInput = getEl("search");

  const sortBtns = document.querySelectorAll(".sort-btn");
  if (sortBtns && sortBtns.length) {
    sortBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        sortBtns.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        filterAndRender();
      });
    });
  }

  if (searchInput) {
    searchInput.addEventListener("input", filterAndRender);
  }

  const sortFieldRadios = document.querySelectorAll('input[name="sortField"]');
  if (sortFieldRadios && sortFieldRadios.length) {
    sortFieldRadios.forEach((r) => r.addEventListener("change", filterAndRender));
  }

  const sortDirBtn = getEl("sortDirBtn");
  if (sortDirBtn) {
    sortDirBtn.textContent = sortDir === "asc" ? "↑" : "↓";
    sortDirBtn.addEventListener("click", () => {
      sortDir = sortDir === "asc" ? "desc" : "asc";
      sortDirBtn.textContent = sortDir === "asc" ? "↑" : "↓";
      filterAndRender();
    });
  }

  getEl("saveBtn")?.addEventListener("click", saveEntry);
  getEl("cancelBtn")?.addEventListener("click", cancelEdit);
  getEl("addBtn")?.addEventListener("click", addEntry);
  getEl("undoBtn")?.addEventListener("click", handleUndo);
  getEl("copyBtn")?.addEventListener("click", copyCurrent);
  getEl("bibFileBtn")?.addEventListener("click", async () => {
    try {
      await openBibFilePicker();
    } catch (err) {
      console.error("Bib file switcher failed:", err);
      alert(err.message || "Failed to load bib files");
    }
  });
  getEl("importToolbarBtn")?.addEventListener("click", openImportFilePicker);
  getEl("scanToolbarBtn")?.addEventListener("click", async () => {
    try {
      await loadScanServices();
      scanState.statusText = "";
      openScanModal();
    } catch (err) {
      console.error("Scan launcher failed:", err);
      alert(err.message || "Failed to load scan services");
    }
  });
  getEl("exportToolbarBtn")?.addEventListener("click", openExportPicker);
  getEl("historyToolbarBtn")?.addEventListener("click", async () => {
    try {
      await openHistoryPicker();
    } catch (err) {
      console.error("History failed:", err);
      alert(err.message || "Failed to load history");
    }
  });

  const viewToggleBtn = getEl("viewToggleBtn");
  if (viewToggleBtn) {
    viewToggleBtn.addEventListener("click", () => {
      viewMode = viewMode === "grid" ? "list" : "grid";
      const icon = viewToggleBtn.querySelector("i");
      if (icon) {
        icon.className = viewMode === "list" ? "fa fa-th-large" : "fa fa-list";
      }
      filterAndRender();
    });
  }

  const fileInput = getEl("bibFileInput");
  if (fileInput) {
    fileInput.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      try {
        await runImport(file);
      } catch (err) {
        console.error("Import failed:", err);
        alert(err.message || "Import failed");
      }
    });
  }

  getEl("pickerCloseBtn")?.addEventListener("click", closePicker);
  getEl("scanCloseBtn")?.addEventListener("click", closeScanModal);
  getEl("scanBackdrop")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget && !scanState.running) {
      closeScanModal();
    }
  });
  getEl("scanClearRejectionsBtn")?.addEventListener("click", async () => {
    try {
      await handleClearScanRejections();
    } catch (err) {
      console.error("Clear rejections failed:", err);
      alert(err.message || "Failed to clear past rejections");
    }
  });
  getEl("pickerBackdrop")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      closePicker();
    }
  });
  getEl("pickerSearch")?.addEventListener("input", (event) => {
    if (!pickerState) return;
    pickerState.query = event.target.value || "";
    renderPickerList();
  });
  getEl("pickerSelectVisibleBtn")?.addEventListener("click", () => {
    if (!pickerState) return;
    for (const item of filteredPickerItems()) {
      item.selected = true;
    }
    renderPickerList();
  });
  getEl("pickerClearBtn")?.addEventListener("click", () => {
    if (!pickerState) return;
    for (const item of pickerState.items) {
      item.selected = false;
    }
    renderPickerList();
  });
  getEl("pickerConfirmBtn")?.addEventListener("click", async () => {
    if (!pickerState) return;
    const selectedItems = pickerState.items.filter((item) => item.selected);
    if (!selectedItems.length) {
      alert("Select at least one entry.");
      return;
    }

    const confirmBtn = getEl("pickerConfirmBtn");
    const originalText = confirmBtn ? confirmBtn.textContent : "Confirm";
    if (confirmBtn) confirmBtn.disabled = true;

    try {
      await pickerState.onConfirm(selectedItems);
      closePicker();
    } catch (err) {
      console.error(`${pickerState.mode} failed:`, err);
      alert(err.message || `${pickerState.mode} failed`);
    } finally {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = originalText;
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && pickerState) {
      closePicker();
      return;
    }
    if (event.key === "Escape" && getEl("scanBackdrop")?.classList.contains("open") && !scanState.running) {
      closeScanModal();
    }
  });

  const dropOverlay = getEl("dropOverlay");
  document.addEventListener("dragenter", (event) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    dragDepth += 1;
    dropOverlay?.classList.add("open");
  });
  document.addEventListener("dragover", (event) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    event.preventDefault();
  });
  document.addEventListener("dragleave", (event) => {
    if (!event.dataTransfer?.types?.includes("Files")) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) {
      dropOverlay?.classList.remove("open");
    }
  });
  document.addEventListener("drop", async (event) => {
    if (!event.dataTransfer?.files?.length) return;
    event.preventDefault();
    dragDepth = 0;
    dropOverlay?.classList.remove("open");
    const [file] = event.dataTransfer.files;
    try {
      await runImport(file);
    } catch (err) {
      console.error("Drop import failed:", err);
      alert(err.message || "Import failed");
    }
  });

  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
      if (viewMode === "grid" && grid && grid.offsetParent !== null) {
        renderGrid();
      }
    }, 120);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initUI();
  Promise.all([loadEntries(), refreshBibFileButton()]).catch((err) => {
    console.error("Failed to load entries:", err);
    alert(err.message || "Failed to load bibliography");
  });
});
