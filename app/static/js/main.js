// static/js/main.js
// Improved formatting, null-safety, and moved import handler into initUI()

import {
  fetchEntries,
  fetchScanServices,
  runScan,
  startScanJob,
  fetchScanJob,
  cancelScanJob,
  applyScanItem,
  rejectScanItem,
  clearScanRejections,
  attachPdfToEntry,
  markNoPdfExpected,
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
  currentLabel: "",
  statusText: "",
  progressText: "",
  jobId: "",
  cursor: 0,
  actionableCount: 0,
  total: 0,
  scanned: 0,
  items: [],
  pollTimer: null,
};
let pdfCoverageState = {
  items: [],
  filtered: [],
  counts: { high: 0, medium: 0, low: 0 },
  filter: "all",
  sort: "priority",
};
let exportState = {
  format: "bib",
  htmlView: "list",
};
let dialogResolver = null;
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

function refreshScanToolbarButton() {
  const button = getEl("scanToolbarBtn");
  if (!button) return;
  if (scanState.running) {
    button.innerHTML = `<i class="fa fa-refresh scan-rotating me-1" aria-hidden="true"></i>Scan`;
  } else {
    button.textContent = "Scan";
  }
}

function setScanEditLock(locked) {
  const editorEl = getEl("editRaw");
  if (editorEl) editorEl.readOnly = locked;
  ["saveBtn", "addBtn", "cancelBtn"].forEach((id) => {
    const button = getEl(id);
    if (button) button.disabled = locked;
  });
}

function closeDialog(result = null) {
  const backdrop = getEl("dialogBackdrop");
  if (!backdrop) return;
  backdrop.classList.remove("open");
  backdrop.setAttribute("aria-hidden", "true");
  const resolver = dialogResolver;
  dialogResolver = null;
  if (resolver) resolver(result);
}

function showDialog({ title, body, actions }) {
  return new Promise((resolve) => {
    dialogResolver = resolve;
    const backdrop = getEl("dialogBackdrop");
    const titleEl = getEl("dialogTitle");
    const bodyEl = getEl("dialogBody");
    const actionsEl = getEl("dialogActions");
    if (!backdrop || !titleEl || !bodyEl || !actionsEl) {
      resolve(null);
      return;
    }
    titleEl.textContent = title || "Message";
    bodyEl.textContent = body || "";
    actionsEl.innerHTML = "";
    (actions || [{ label: "Close", value: true, className: "btn btn-sm btn-primary" }]).forEach((action) => {
      const button = document.createElement("button");
      button.className = action.className || "btn btn-sm btn-outline-secondary";
      button.textContent = action.label;
      button.addEventListener("click", () => closeDialog(action.value));
      actionsEl.appendChild(button);
    });
    backdrop.classList.add("open");
    backdrop.setAttribute("aria-hidden", "false");
  });
}

function showMessageDialog(title, body) {
  return showDialog({
    title,
    body,
    actions: [{ label: "Close", value: true, className: "btn btn-sm btn-primary" }],
  });
}

function showConfirmDialog(title, body, confirmLabel = "Confirm", confirmVariant = "btn-danger") {
  return showDialog({
    title,
    body,
    actions: [
      { label: "Cancel", value: false, className: "btn btn-sm btn-outline-secondary" },
      { label: confirmLabel, value: true, className: `btn btn-sm ${confirmVariant}` },
    ],
  });
}

function formatUiError(err, fallback = "Action failed") {
  if (!err) return fallback;
  const message = String(err.message || err || fallback)
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return message || fallback;
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
  if (pickerState.mode === "export") {
    pickerState.statusLine = exportStatusLine();
    renderPickerExtras();
  }
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
    if (pickerState.mode === "quality-scan") {
      const textarea = previewText.querySelector("[data-role='proposed-raw']");
      if (textarea) {
        textarea.addEventListener("input", (event) => {
          selectedItem.proposed_raw = event.target.value || "";
        });
      }
    }
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
        <div class="small text-muted mb-1">Current entry (read-only)</div>
        <pre class="scan-current-raw">${escapeHtml(item.current_raw || "")}</pre>
      </div>
      <div>
        <div class="small text-muted mb-1">Proposed entry (editable before accept)</div>
        <textarea class="form-control form-control-sm scan-proposed-textarea" data-role="proposed-raw">${escapeHtml(item.proposed_raw || "")}</textarea>
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
        await showMessageDialog("Selection Required", "Select an entry first.");
        return;
      }

      button.disabled = true;
      try {
        await action.onClick(selectedItem);
      } catch (err) {
        console.error(`${pickerState?.mode || "picker"} action failed:`, err);
        await showMessageDialog("Action Failed", formatUiError(err));
      } finally {
        button.disabled = false;
      }
    });
    actions.appendChild(button);
  });
}

function renderPickerExtras() {
  const extra = getEl("pickerExtraActions");
  const statusLine = getEl("pickerStatusLine");
  const search = getEl("pickerSearch");
  const selectVisibleBtn = getEl("pickerSelectVisibleBtn");
  const clearBtn = getEl("pickerClearBtn");
  if (!extra || !statusLine) return;
  extra.innerHTML = "";
  statusLine.textContent = pickerState?.statusLine || "";
  if (!pickerState?.extraActions?.length) {
    if (search) search.style.display = "";
    if (selectVisibleBtn) selectVisibleBtn.style.display = pickerState?.singleSelect ? "none" : "";
    if (clearBtn) clearBtn.style.display = "";
    return;
  }

  if (search) search.style.display = "";
  if (selectVisibleBtn) selectVisibleBtn.style.display = pickerState.singleSelect ? "none" : "";
  if (clearBtn) clearBtn.style.display = "";

  pickerState.extraActions.forEach((action) => {
    if (action.type === "buttonGroup" && Array.isArray(action.options)) {
      const group = document.createElement("div");
      group.className = action.className || "btn-group btn-group-sm";
      group.setAttribute("role", "group");
      action.options.forEach((option) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = `btn btn-sm ${option.value === action.value ? "btn-primary active" : "btn-outline-secondary"}`;
        button.textContent = option.label;
        button.disabled = Boolean(action.disabled);
        button.addEventListener("click", () => action.onChange?.(option.value));
        group.appendChild(button);
      });
      extra.appendChild(group);
      return;
    }

    const button = document.createElement(action.tagName || "button");
    button.className = action.className || "btn btn-sm btn-outline-secondary";
    if (action.tagName === "select" && Array.isArray(action.options)) {
      action.options.forEach((option) => {
        const node = document.createElement("option");
        node.value = option.value;
        node.textContent = option.label;
        if (option.value === action.value) node.selected = true;
        button.appendChild(node);
      });
      button.addEventListener("change", (event) => action.onChange?.(event.target.value));
    } else {
      button.textContent = action.label;
      button.disabled = Boolean(action.disabled);
      button.addEventListener("click", async () => {
        try {
          await action.onClick?.();
        } catch (err) {
          console.error("Picker extra action failed:", err);
          await showMessageDialog("Action Failed", err.message || "Action failed");
        }
      });
    }
    extra.appendChild(button);
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
  const extra = getEl("pickerExtraActions");
  if (extra) extra.innerHTML = "";
  const statusLine = getEl("pickerStatusLine");
  if (statusLine) statusLine.textContent = "";
  updatePickerPreview();
}

function scanStatusLine() {
  if (scanState.running) {
    return `${scanState.statusText}${scanState.actionableCount ? ` • ${scanState.actionableCount} actionable` : ""}`;
  }
  if (scanState.currentLabel && scanState.items.length) {
    return `${scanState.currentLabel} ready • ${scanState.items.length} actionable`;
  }
  return "Choose Crossref or WorldCat to start a rolling scan. PDF Coverage opens a separate report.";
}

function scanExtraActions() {
  const actions = scanState.services
    .filter((service) => service.name !== "pdf-coverage")
    .map((service) => ({
      label: scanState.running && scanState.currentService === service.name ? `${service.label} Running` : `Run ${service.label}`,
      className: "btn btn-sm btn-outline-primary",
      disabled: scanState.running && scanState.currentService !== service.name,
      onClick: async () => {
        if (!scanState.running || scanState.currentService !== service.name) {
          await startScanFromModal(service);
        }
      },
    }));
  const pdfCoverageService = scanState.services.find((service) => service.name === "pdf-coverage");
  if (pdfCoverageService) {
    actions.push({
      label: pdfCoverageService.label || "PDF Coverage",
      className: "btn btn-sm btn-outline-secondary",
      disabled: scanState.running,
      onClick: async () => {
        await startScanFromModal(pdfCoverageService);
      },
    });
  }
  actions.push({
    label: "Stop",
    className: "btn btn-sm btn-outline-danger",
    disabled: !scanState.running,
    onClick: async () => {
      await stopCurrentScan();
    },
  });
  actions.push({
    label: "Clear Rejections",
    className: "btn btn-sm btn-outline-secondary",
    onClick: async () => {
      await handleClearScanRejections();
    },
  });
  return actions;
}

function ensureScanWorkspaceOpen() {
  if (pickerState?.mode === "quality-scan") {
    pickerState.extraActions = scanExtraActions();
    pickerState.statusLine = scanStatusLine();
    renderPickerExtras();
    return;
  }
  openPicker({
    mode: "quality-scan",
    title: "Scan Review",
    subtitle: "Rolling review queue for Crossref and WorldCat suggestions.",
    confirmText: "Apply",
    emptyMessage: "No actionable entries yet. Start a scan to populate the queue.",
    items: buildScanReviewItems(scanState.items),
    singleSelect: true,
    showPreview: true,
    previewLabel: "Proposed amendment",
    previewEmptyText: "Select a scanned entry to review differences.",
    previewRenderer: renderQualityPreview,
    extraActions: scanExtraActions(),
    statusLine: scanStatusLine(),
    actions: scanReviewActions(),
  });
}

function pdfPriorityRank(priority) {
  if (priority === "high") return 0;
  if (priority === "medium") return 1;
  return 2;
}

function applyPdfCoverageFilters() {
  let items = [...pdfCoverageState.items];
  if (pdfCoverageState.filter !== "all") {
    items = items.filter((item) => item.priority === pdfCoverageState.filter);
  }
  if (pdfCoverageState.sort === "title") {
    items.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
  } else if (pdfCoverageState.sort === "type") {
    items.sort((a, b) => (a.type || "").localeCompare(b.type || ""));
  } else {
    items.sort((a, b) => {
      const priorityDiff = pdfPriorityRank(a.priority) - pdfPriorityRank(b.priority);
      if (priorityDiff) return priorityDiff;
      return (a.key || "").localeCompare(b.key || "");
    });
  }
  pdfCoverageState.filtered = items;
}

function renderPdfCoverageCounts() {
  const countsEl = getEl("pdfCoverageCounts");
  if (!countsEl) return;
  const counts = pdfCoverageState.counts || { high: 0, medium: 0, low: 0 };
  countsEl.innerHTML = `
    <span class="badge text-bg-danger">High ${counts.high || 0}</span>
    <span class="badge text-bg-warning">Medium ${counts.medium || 0}</span>
    <span class="badge text-bg-secondary">Low ${counts.low || 0}</span>
  `;
}

function renderPdfCoverageList() {
  applyPdfCoverageFilters();
  renderPdfCoverageCounts();

  const list = getEl("pdfCoverageList");
  if (!list) return;
  list.innerHTML = "";

  if (!pdfCoverageState.filtered.length) {
    list.innerHTML = `<div class="text-muted small">No entries match the current PDF coverage filter.</div>`;
    return;
  }

  const fragment = document.createDocumentFragment();
  for (const item of pdfCoverageState.filtered) {
    const row = document.createElement("div");
    row.className = `pdf-coverage-item pdf-priority-${item.priority}`;
    row.innerHTML = `
      <div class="pdf-coverage-main">
        <div class="d-flex flex-wrap align-items-center gap-2">
          <span class="picker-key">${escapeHtml(item.key || "(no key)")}</span>
          <span class="badge text-bg-light picker-badge">${escapeHtml(item.priority_label || item.priority || "Low")}</span>
          <span class="badge text-bg-light picker-badge">${escapeHtml(item.type || "misc")}</span>
        </div>
        <div class="fw-semibold">${escapeHtml(item.title || "(No title)")}</div>
        <div class="picker-meta">${escapeHtml(item.summary || "")}</div>
        <div class="small text-muted">${escapeHtml(item.reason || "")}</div>
        <div class="small text-muted">Expected path: ${escapeHtml(item.expected_path || "")}</div>
      </div>
      <div class="pdf-coverage-actions">
        <button class="btn btn-sm btn-outline-secondary" data-action="open">Open Entry</button>
        <button class="btn btn-sm btn-primary" data-action="attach">Attach PDF</button>
        <button class="btn btn-sm btn-outline-secondary" data-action="suppress">No PDF Expected</button>
      </div>
    `;

    row.querySelector('[data-action="open"]')?.addEventListener("click", () => {
      const entry = findEntryByKey(item.key);
      if (entry) {
        selectEntry(entry);
      }
      closePdfCoverageModal();
    });

    row.querySelector('[data-action="attach"]')?.addEventListener("click", () => {
      const input = getEl("pdfUploadInput");
      if (!input) return;
      input.value = "";
      input.dataset.entryKey = item.key || "";
      input.click();
    });

    row.querySelector('[data-action="suppress"]')?.addEventListener("click", async () => {
      try {
        const res = await markNoPdfExpected(item.key, true);
        if (!res.ok) {
          throw new Error(res.description || res.error || "Failed to mark no PDF expected");
        }
        pdfCoverageState.items = pdfCoverageState.items.filter((candidate) => candidate.key !== item.key);
        const counts = { high: 0, medium: 0, low: 0 };
        for (const candidate of pdfCoverageState.items) {
          counts[candidate.priority] = (counts[candidate.priority] || 0) + 1;
        }
        pdfCoverageState.counts = counts;
        renderPdfCoverageList();
        showToast(`Marked ${item.key} as no PDF expected`);
      } catch (err) {
        console.error("No PDF expected failed:", err);
        await showMessageDialog("Update Failed", formatUiError(err, "Failed to update PDF expectation"));
      }
    });

    fragment.appendChild(row);
  }
  list.appendChild(fragment);
}

function openPdfCoverageModal(scanResult) {
  pdfCoverageState.items = scanResult.items || [];
  pdfCoverageState.counts = scanResult.counts || { high: 0, medium: 0, low: 0 };
  pdfCoverageState.filter = "all";
  pdfCoverageState.sort = "priority";

  const backdrop = getEl("pdfCoverageBackdrop");
  if (!backdrop) return;
  backdrop.classList.add("open");
  backdrop.setAttribute("aria-hidden", "false");

  const filter = getEl("pdfCoverageFilter");
  const sort = getEl("pdfCoverageSort");
  if (filter) filter.value = "all";
  if (sort) sort.value = "priority";
  renderPdfCoverageList();
}

function closePdfCoverageModal() {
  const backdrop = getEl("pdfCoverageBackdrop");
  if (!backdrop) return;
  backdrop.classList.remove("open");
  backdrop.setAttribute("aria-hidden", "true");
}

async function loadScanServices() {
  const res = await fetchScanServices();
  if (!res.ok) {
    throw new Error(res.description || res.error || "Failed to load scan services");
  }
  scanState.services = res.items || [];
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
    extraActions: config.extraActions || [],
    statusLine: config.statusLine || "",
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
  renderPickerExtras();
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
      const confirmed = await showConfirmDialog("Delete Entry", "Delete this entry?", "Delete", "btn-danger");
      if (!confirmed) return;
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
    await showMessageDialog("Save Failed", formatUiError(err, "Failed to save entry"));
  }
}

function cancelEdit() {
  if (currentEntry && editor) editor.value = currentEntry.raw || "";
}

async function addEntry() {
  try {
    const raw = editor ? editor.value.trim() : "";
    if (!raw) {
      await showMessageDialog("Nothing To Add", "Enter BibLaTeX for the new entry first.");
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
    await showMessageDialog("Add Failed", formatUiError(err, "Failed to add entry"));
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
    await showMessageDialog("Undo Failed", formatUiError(err, "Undo failed"));
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
    hasPdf: Boolean(entry.has_pdf),
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

function scanReviewActions() {
  return [
    {
      label: "Accept",
      className: "btn btn-sm btn-primary",
      onClick: async (selected) => {
        const applyRes = await applyScanItem(selected);
        if (!applyRes.ok) {
          throw new Error(applyRes.description || applyRes.error || "Failed to apply patch");
        }
        await loadEntries();
        currentEntry = findEntryByKey(applyRes.key || selected.key);
        if (currentEntry && editor) {
          editor.value = currentEntry.raw || "";
        }
        removePickerItem(selected.id);
        scanState.items = scanState.items.filter((item) => item.id !== selected.id);
        showToast(`Applied scan patch for ${applyRes.key || selected.key}`);
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
        const suppress = await showConfirmDialog(
          "Suppress Suggestion",
          "Suppress this exact suggestion on future scans? Choose Cancel to dismiss it for now.",
          "Suppress",
          "btn-danger",
        );
        const rejectRes = await rejectScanItem(selected, Boolean(suppress));
        if (!rejectRes.ok) {
          throw new Error(rejectRes.description || rejectRes.error || "Failed to reject suggestion");
        }
        removePickerItem(selected.id);
        scanState.items = scanState.items.filter((item) => item.id !== selected.id);
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
  ];
}

async function openScanReviewPicker(scanResult) {
  if (scanResult.service === "pdf-coverage") {
    openPdfCoverageModal(scanResult);
    return;
  }

  const items = buildScanReviewItems(scanResult.items || []);
  if (pickerState?.mode === "quality-scan") {
    const existingIds = new Set(pickerState.items.map((item) => item.id));
    const additions = items.filter((item) => !existingIds.has(item.id));
    if (additions.length) {
      const hasSelected = pickerState.items.some((item) => item.selected);
      additions.forEach((item, index) => {
        item.selected = !hasSelected && index === 0;
      });
      pickerState.items.push(...additions);
      const activePreviewEditor = document.activeElement?.matches?.("[data-role='proposed-raw']");
      if (!activePreviewEditor) {
        renderPickerList();
      } else {
        updatePickerInfo();
      }
    }
    pickerState.extraActions = scanExtraActions();
    pickerState.statusLine = scanStatusLine();
    renderPickerExtras();
    return;
  }
  ensureScanWorkspaceOpen();
}

async function startScanFromModal(service) {
  if (service.name === "pdf-coverage") {
    const res = await runScan(service.name);
    if (!res.ok) {
      throw new Error(res.description || res.error || "Scan failed");
    }
    await openScanReviewPicker(res);
    return;
  }

  const job = await startScanJob(service.name);
  if (!job.ok && job.status !== 202) {
    throw new Error(job.description || job.error || "Failed to start scan");
  }

  scanState.running = true;
  scanState.currentService = service.name;
  scanState.currentLabel = service.label || service.name;
  scanState.jobId = job.id;
  scanState.cursor = 0;
  scanState.items = [];
  scanState.actionableCount = 0;
  scanState.total = job.total || 0;
  scanState.scanned = 0;
  scanState.statusText = `${scanState.currentLabel} scan underway...`;
  setScanEditLock(true);
  refreshScanToolbarButton();
  ensureScanWorkspaceOpen();
  scheduleScanPoll();
}

function scheduleScanPoll() {
  if (!scanState.running || !scanState.jobId) return;
  window.clearTimeout(scanState.pollTimer);
  scanState.pollTimer = window.setTimeout(async () => {
    try {
      const res = await fetchScanJob(scanState.jobId, scanState.cursor);
      if (!res.ok) {
        throw new Error(res.description || res.error || "Failed to poll scan status");
      }

      scanState.cursor = res.cursor || scanState.cursor;
      scanState.scanned = res.scanned || 0;
      scanState.total = res.total || 0;
      scanState.actionableCount = res.actionable_count || 0;
      scanState.statusText = `Scanned ${scanState.scanned} of ${scanState.total || 0} entries.`;

      if (Array.isArray(res.items) && res.items.length) {
        scanState.items.push(...res.items);
        await openScanReviewPicker({
          service: scanState.currentService,
          label: scanState.currentLabel,
          items: res.items,
        });
      }

      if (res.status === "running") {
        if (pickerState?.mode === "quality-scan") {
          pickerState.statusLine = scanStatusLine();
          pickerState.extraActions = scanExtraActions();
          renderPickerExtras();
        }
        scheduleScanPoll();
        return;
      }

      scanState.running = false;
      setScanEditLock(false);
      refreshScanToolbarButton();
      if (pickerState?.mode === "quality-scan") {
        pickerState.statusLine = res.status === "completed"
          ? `${scanState.currentLabel} scan finished • ${scanState.items.length} actionable`
          : `${scanState.currentLabel} scan stopped`;
        pickerState.extraActions = scanExtraActions();
        renderPickerExtras();
      }
      if (res.status === "completed") {
        showToast(`${scanState.currentLabel} scan finished`);
      } else if (res.status === "cancelled") {
        showToast(`${scanState.currentLabel} scan stopped`);
      } else if (res.status === "failed") {
        await showMessageDialog("Scan Failed", res.message || "Scan failed");
      }
    } catch (err) {
      scanState.running = false;
      scanState.statusText = err.message || "Scan failed";
      setScanEditLock(false);
      refreshScanToolbarButton();
      if (pickerState?.mode === "quality-scan") {
        pickerState.statusLine = scanStatusLine();
        pickerState.extraActions = scanExtraActions();
        renderPickerExtras();
      }
      await showMessageDialog("Scan Failed", err.message || "Scan failed");
    }
  }, 1000);
}

async function stopCurrentScan() {
  if (!scanState.running || !scanState.jobId) return;
  const res = await cancelScanJob(scanState.jobId);
  if (!res.ok) {
    throw new Error(res.description || res.error || "Failed to stop scan");
  }
  scanState.statusText = "Stopping scan...";
  if (pickerState?.mode === "quality-scan") {
    pickerState.statusLine = scanStatusLine();
    renderPickerExtras();
  }
}

function openCurrentScanResults() {
  if (!scanState.items.length) {
    showToast("No scan results are ready yet");
    return;
  }
  openScanReviewPicker({
    service: scanState.currentService,
    label: scanState.currentLabel,
    items: scanState.items,
  });
}

function exportSelectedPdfCount(items) {
  return items.filter((item) => item.selected && item.hasPdf).length;
}

function exportStatusLine() {
  if (!pickerState || pickerState.mode !== "export") {
    return exportState.format === "zip"
      ? "Choose entries to export as a ZIP with PDFs and a static HTML index."
      : "Choose entries to export as BibLaTeX.";
  }
  const selectedCount = pickerState.items.filter((item) => item.selected).length;
  const pdfCount = exportSelectedPdfCount(pickerState.items);
  if (!selectedCount) {
    return exportState.format === "zip"
      ? "Choose entries to export as a ZIP with PDFs and a static HTML index."
      : "Choose entries to export as BibLaTeX.";
  }
  if (exportState.format === "zip") {
    return `${selectedCount} entries selected • ${pdfCount} PDFs available • ${exportState.htmlView === "cards" ? "card" : "list"} HTML index`;
  }
  return `${selectedCount} entries selected for BibLaTeX export`;
}

function exportExtraActions() {
  return [
    {
      type: "buttonGroup",
      className: "btn-group btn-group-sm",
      value: exportState.format,
      options: [
        { value: "bib", label: "BibLaTeX Only" },
        { value: "zip", label: "ZIP with PDFs" },
      ],
      onChange: (value) => {
        exportState.format = value || "bib";
        if (pickerState?.mode === "export") {
          pickerState.extraActions = exportExtraActions();
          pickerState.statusLine = exportStatusLine();
          renderPickerExtras();
          updatePickerInfo();
        }
      },
    },
    {
      type: "buttonGroup",
      className: "btn-group btn-group-sm",
      value: exportState.htmlView,
      options: [
        { value: "list", label: "HTML: List View" },
        { value: "cards", label: "HTML: Card View" },
      ],
      onChange: (value) => {
        exportState.htmlView = value || "list";
        if (pickerState?.mode === "export") {
          pickerState.extraActions = exportExtraActions();
          pickerState.statusLine = exportStatusLine();
          renderPickerExtras();
        }
      },
    },
  ];
}

async function handleClearScanRejections() {
  const res = await clearScanRejections();
  if (!res.ok) {
    throw new Error(res.description || res.error || "Failed to clear past rejections");
  }
  scanState.statusText = res.cleared
    ? `Cleared ${res.cleared} past rejection${res.cleared === 1 ? "" : "s"}.`
    : "No past rejections were stored.";
  if (pickerState?.mode === "quality-scan") {
    pickerState.statusLine = scanStatusLine();
    renderPickerExtras();
  }
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
  exportState = {
    format: exportState.format || "bib",
    htmlView: exportState.htmlView || "list",
  };
  openPicker({
    mode: "export",
    title: "Export Entries",
    subtitle: "Search and select entries, then export BibLaTeX or a ZIP with PDFs and an HTML index.",
    confirmText: "Export",
    emptyMessage: "No entries available to export.",
    items: buildExportItems(),
    extraActions: exportExtraActions(),
    statusLine: exportStatusLine(),
    onConfirm: async (selectedItems) => {
      const res = await requestExportEntries(selectedItems.map((item) => item.key), {
        format: exportState.format,
        htmlView: exportState.htmlView,
      });
      if (!res.ok) {
        throw new Error(res.error || "Export failed");
      }
      downloadBlob(res.blob, exportState.format === "zip" ? "export.zip" : "export.bib");
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
  refreshScanToolbarButton();
  setScanEditLock(false);

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
      await showMessageDialog("Bibliography Load Failed", formatUiError(err, "Failed to load bib files"));
    }
  });
  getEl("importToolbarBtn")?.addEventListener("click", openImportFilePicker);
  getEl("scanToolbarBtn")?.addEventListener("click", async () => {
    try {
      await loadScanServices();
      scanState.statusText = "";
      ensureScanWorkspaceOpen();
    } catch (err) {
      console.error("Scan launcher failed:", err);
      await showMessageDialog("Scan Services Failed", formatUiError(err, "Failed to load scan services"));
    }
  });
  getEl("exportToolbarBtn")?.addEventListener("click", openExportPicker);
  getEl("historyToolbarBtn")?.addEventListener("click", async () => {
    try {
      await openHistoryPicker();
    } catch (err) {
      console.error("History failed:", err);
      await showMessageDialog("History Failed", formatUiError(err, "Failed to load history"));
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
        await showMessageDialog("Import Failed", formatUiError(err, "Import failed"));
      }
    });
  }

  const pdfUploadInput = getEl("pdfUploadInput");
  if (pdfUploadInput) {
    pdfUploadInput.addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      const key = event.target.dataset.entryKey || "";
      if (!file || !key) return;
      try {
        const res = await attachPdfToEntry(key, file);
        if (!res.ok) {
          throw new Error(res.description || res.error || "Failed to attach PDF");
        }
        pdfCoverageState.items = pdfCoverageState.items.filter((item) => item.key !== key);
        const counts = { high: 0, medium: 0, low: 0 };
        for (const candidate of pdfCoverageState.items) {
          counts[candidate.priority] = (counts[candidate.priority] || 0) + 1;
        }
        pdfCoverageState.counts = counts;
        renderPdfCoverageList();
        await loadEntries();
        showToast(`Attached PDF for ${key}`);
      } catch (err) {
        console.error("PDF attach failed:", err);
        await showMessageDialog("PDF Attach Failed", formatUiError(err, "Failed to attach PDF"));
      } finally {
        event.target.value = "";
        event.target.dataset.entryKey = "";
      }
    });
  }

  getEl("pickerCloseBtn")?.addEventListener("click", closePicker);
  getEl("dialogCloseBtn")?.addEventListener("click", () => closeDialog(false));
  getEl("dialogBackdrop")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      closeDialog(false);
    }
  });
  getEl("pdfCoverageCloseBtn")?.addEventListener("click", closePdfCoverageModal);
  getEl("pdfCoverageBackdrop")?.addEventListener("click", (event) => {
    if (event.target === event.currentTarget) {
      closePdfCoverageModal();
    }
  });
  getEl("pdfCoverageFilter")?.addEventListener("change", (event) => {
    pdfCoverageState.filter = event.target.value || "all";
    renderPdfCoverageList();
  });
  getEl("pdfCoverageSort")?.addEventListener("change", (event) => {
    pdfCoverageState.sort = event.target.value || "priority";
    renderPdfCoverageList();
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
      await showMessageDialog("Selection Required", "Select at least one entry.");
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
      await showMessageDialog("Action Failed", formatUiError(err, `${pickerState.mode} failed`));
    } finally {
      if (confirmBtn) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = originalText;
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && getEl("dialogBackdrop")?.classList.contains("open")) {
      closeDialog(false);
      return;
    }
    if (event.key === "Escape" && pickerState) {
      closePicker();
      return;
    }
    if (event.key === "Escape" && getEl("pdfCoverageBackdrop")?.classList.contains("open")) {
      closePdfCoverageModal();
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
      await showMessageDialog("Import Failed", formatUiError(err, "Import failed"));
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
    showMessageDialog("Load Failed", formatUiError(err, "Failed to load bibliography"));
  });
});
