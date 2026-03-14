// static/js/main.js
// Improved formatting, null-safety, and moved import handler into initUI()

import { fetchEntries } from "./api.js";
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

async function loadEntries() {
  allEntries = await fetchEntries();
  buildIndex(allEntries);
  filterAndRender();
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
  const frag = document.createDocumentFragment();

  for (const e of filteredEntries) {
    const card = createCard(e);
    card.addEventListener("click", () => selectEntry(e));
    frag.appendChild(card);
  }

  grid.appendChild(frag);
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
  const frag = document.createDocumentFragment();

  for (const e of filteredEntries) {
    frag.appendChild(createListEntry(e));
  }

  list.appendChild(frag);
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
  if (!currentEntry) return;

  const raw = editor ? editor.value.trim() : "";
  if (raw === "") {
    if (!confirm("Delete this entry?")) return;
  }

  await fetch(`/api/entry/${currentEntry.key}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw }),
  });

  await loadEntries();
}

function cancelEdit() {
  if (currentEntry && editor) editor.value = currentEntry.raw || "";
}

async function addEntry() {
  const raw = editor ? editor.value.trim() : "";
  if (!raw) {
    alert("No entry content");
    return;
  }

  await fetch("/api/entry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw }),
  });

  await loadEntries();
}

function copyCurrent() {
  if (!currentEntry) return;
  navigator.clipboard.writeText(currentEntry.raw || "");
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
  getEl("copyBtn")?.addEventListener("click", copyCurrent);

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

  const importBtn = getEl("importBtn");
  const doiInput = getEl("doiInput");

  if (importBtn) {
    importBtn.addEventListener("click", async () => {

      const doi = doiInput ? doiInput.value.trim() : "";
      if (!doi) return alert("Please enter a DOI");

      try {

        importBtn.disabled = true;
        importBtn.textContent = "Importing...";

        const res = await fetch(`/api/import?doi=${encodeURIComponent(doi)}`, { method: "POST" });
        const data = await res.json();

        if (data && data.success) {
          await loadEntries();
          filterAndRender();
        } else {
          alert("Import failed: " + (data && data.error ? data.error : "unknown error"));
        }

      } catch (err) {

        console.error("Import error", err);
        alert("Import failed: " + (err && err.message ? err.message : "unknown error"));

      } finally {

        importBtn.disabled = false;
        importBtn.textContent = "Import";

      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initUI();
  loadEntries().catch((err) => {
    console.error("Failed to load entries:", err);
  });
});