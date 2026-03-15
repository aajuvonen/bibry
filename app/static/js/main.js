// static/js/main.js
// Improved formatting, null-safety, and moved import handler into initUI()

import {
  fetchEntries,
  undoLast,
  previewImportFile,
  importEntries as importSelectedEntries,
  exportEntries as requestExportEntries,
} from "./api.js";
import { buildIndex, applyFilters } from "./filters.js";
import { createCard, getIconClass, extractLatexUrl } from "./renderer.js";

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
let dragDepth = 0;
let toastTimer = null;

const RENDER_BATCH_SIZE = 80;

function escapeHtml(value = "") {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function loadEntries() {
  allEntries = await fetchEntries();
  buildIndex(allEntries);
  filterAndRender();
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
  info.textContent = `${selectedCount} selected`;
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
    return;
  }

  const frag = document.createDocumentFragment();
  for (const item of items) {
    const row = document.createElement("label");
    row.className = "picker-item";
    row.innerHTML = `
      <input type="checkbox" class="form-check-input mt-1">
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
      item.selected = checkbox.checked;
      updatePickerInfo();
    });

    frag.appendChild(row);
  }

  list.appendChild(frag);
  updatePickerInfo();
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
  };

  const backdrop = getEl("pickerBackdrop");
  const title = getEl("pickerTitle");
  const subtitle = getEl("pickerSubtitle");
  const search = getEl("pickerSearch");
  const confirmBtn = getEl("pickerConfirmBtn");

  if (title) title.textContent = pickerState.title;
  if (subtitle) subtitle.textContent = pickerState.subtitle;
  if (search) search.value = "";
  if (confirmBtn) confirmBtn.textContent = pickerState.confirmText;
  if (backdrop) {
    backdrop.classList.add("open");
    backdrop.setAttribute("aria-hidden", "false");
  }

  renderPickerList();
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

function renderGrid() {
  if (!grid) return;

  const list = document.getElementById("list");
  if (list) list.style.display = "none";
  grid.style.display = "";

  grid.innerHTML = "";
  const token = ++renderToken;
  renderInBatches(filteredEntries, grid, token, (e) => {
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

  list.innerHTML = "";
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
    parts.push(`${authors}${year}`.trim());
  }

  if (title) {
    parts.push(`<b>${title}</b>`);
  }

  if (source) {
    parts.push(source);
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
}

async function saveEntry() {
  try {
    if (!currentEntry) return;

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

    const previousKey = currentEntry.key;
    await loadEntries();
    currentEntry = findEntryByKey(previousKey);
    if (currentEntry && editor) {
      editor.value = currentEntry.raw || "";
    } else if (editor && raw === "") {
      editor.value = "";
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

function buildImportItems(entries) {
  return entries.map((entry, index) => ({
    id: `import-${index}`,
    key: entry.key || "",
    title: entry.title || "",
    meta: pickerMeta([entry.author, entry.year, entry.type]),
    raw: entry.raw || "",
    selected: Boolean(entry.selected),
    badge: entry.exists ? "Already in library" : "New",
    searchText: [entry.key, entry.title, entry.author, entry.year, entry.type]
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
    subtitle: `${file.name} • new entries are preselected`,
    confirmText: "Import",
    emptyMessage: "No importable entries found.",
    items,
    onConfirm: async (selectedItems) => {
      const res = await importSelectedEntries(selectedItems.map((item) => item.raw));
      if (!res.ok) {
        throw new Error(res.description || res.error || "Import failed");
      }
      await loadEntries();
      showToast(`Imported ${res.imported_count} items`);
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
  getEl("importToolbarBtn")?.addEventListener("click", openImportFilePicker);
  getEl("exportToolbarBtn")?.addEventListener("click", openExportPicker);

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
}

document.addEventListener("DOMContentLoaded", () => {
  initUI();
  loadEntries().catch((err) => {
    console.error("Failed to load entries:", err);
    alert(err.message || "Failed to load entries");
  });
});
