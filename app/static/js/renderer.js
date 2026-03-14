// static/js/renderer.js
// Creates card HTML elements for entries

export function extractLatexUrl(text){
  if(!text) return null

  const latexMatch = text.match(/\\url\{([^}]+)\}/i)
  if (latexMatch) return latexMatch[1]

  const plainMatch = text.match(/https?:\/\/[^\s}>]+/i)
  if (!plainMatch) return null

  return plainMatch[0].replace(/[}>]+$/, "")
}

function extractArxiv(fields){
  if(!fields.archiveprefix || !fields.eprint)
      return null
  if(fields.archiveprefix.toLowerCase() !== "arxiv")
      return null
  return `https://arxiv.org/abs/${fields.eprint}`
}

// Shared icon mapping
const iconMap = {
  article: "fa-file-text",
  book: "fa-book",
  booklet: "fa-book",
  incollection: "fa-book",
  inproceedings: "fa-file-text",
  conference: "fa-file-text",
  manual: "fa-cogs",
  mastersthesis: "fa-graduation-cap",
  phdthesis: "fa-graduation-cap",
  misc: "fa-ellipsis-h",
  unpublished: "fa-file-text-o",
  techreport: "fa-file-text",
  online: "fa-globe",
};

export function getIconClass(type){
  return iconMap[(type || "").toLowerCase()] || "fa-file-text";
}

export function createCard(entry) {
  const type = (entry.type || "").toLowerCase();
  const iconClass = getIconClass(type);

  const card = document.createElement("div");
  card.className = "card";

  const f = entry.fields;

  const title = cleanLatex(f.title) || "(No Title)";
  const author = formatAuthors(cleanLatex(f.author || f.editor || ""));
  const venue = cleanLatex(f.journal || f.booktitle || "");
  const publisher = cleanLatex(f.publisher || "");
  const year = f.year || "";

  card.innerHTML = `
    <b><span class="text-muted small">
      <i class="fa ${iconClass}" aria-hidden="true" title="${type}"></i>
    </span>&nbsp;${title}</b><br>
    ${author}<br>
    ${venue}<br>
    <span class="text-muted">${publisher}</span><br>
    ${year}
  `;

  const actions = document.createElement("div");
  actions.className = "actions";

  if (entry.has_pdf) {
    const a = document.createElement("a");
    a.href = `/pdf/${entry.key}.pdf`;
    a.target = "_blank";
    a.className = "btn btn-danger btn-sm";
    a.innerText = "PDF";
    actions.appendChild(a);
  }

  const links = []

  if (f.url)
      links.push({label:"URL", url:f.url})

  const latexUrl =
      extractLatexUrl(f.howpublished) ||
      extractLatexUrl(f.note)

  if (latexUrl)
      links.push({label:"URL", url:latexUrl})

  const arxivUrl = extractArxiv(f)

  if (arxivUrl)
      links.push({label:"arXiv", url:arxivUrl})

  const seen = new Set()

  links.forEach(link => {

      if (seen.has(link.url))
          return

      seen.add(link.url)

      const a = document.createElement("a")

      a.href = link.url
      a.target = "_blank"
      a.className = "btn btn-primary btn-sm"

      a.innerText = link.label

      actions.appendChild(a)

  })

  if (f.doi){
    const a = document.createElement("a")
    a.href = `https://doi.org/${f.doi}`
    a.target = "_blank"
    a.className = "btn btn-info btn-sm"
    a.innerText = "DOI"
    actions.appendChild(a)
  }

  // Copy button
  const copyBtn = document.createElement("button")
  copyBtn.className = "btn btn-outline-dark btn-sm"
  copyBtn.innerText = "COPY"
  copyBtn.onclick = async ()=>{
    copyBtn.onclick = async ()=>{
      await navigator.clipboard.writeText(entry.raw)
    }
    const data = await res.json()
    await navigator.clipboard.writeText(data.raw)
  }

  //actions.appendChild(copyBtn)

  card.appendChild(actions);
  return card;
}


// List view entry
function createListEntry(entry) {

  const f = entry.fields
  const type = (entry.type || "").toLowerCase()
  const iconClass = getIconClass(type)

  // authors
  let authors = cleanLatex(f.author || "")
  if (authors) {
    const parts = authors.split(" and ")
    if (parts.length > 1)
      authors = parts.slice(0, -1).join(", ") + ", & " + parts.slice(-1)
  }

  const year = f.year ? ` (${f.year})` : ""
  const title = cleanLatex(f.title) || "(No Title)"

  // build source
  let source = ""

  if (f.journal) {
    source = `<i>${cleanLatex(f.journal)}</i>`
    if (f.volume) source += `, ${f.volume}`
    if (f.number) source += `(${f.number})`
    if (f.pages) source += `, pp. ${f.pages}`
  }
  else if (f.booktitle) {
    source = `<i>${cleanLatex(f.booktitle)}</i>`
    if (f.publisher) source += `, ${cleanLatex(f.publisher)}`
    if (f.pages) source += `, pp. ${f.pages}`
  }
  else if (f.publisher) {
    source = cleanLatex(f.publisher)
  }

  const parts = []

  if (authors) parts.push(authors + year)
  if (!authors && year) parts.push(year.trim())

  if (title) parts.push(`<b>${title}</b>`)

  if (source) parts.push(source)

  let citation = parts.join(". ")

  if (f.doi)
    citation += `. DOI: ${f.doi}`

  if (!citation.endsWith("."))
    citation += "."

  const div = document.createElement("div")
  div.className = "mb-2"

  div.innerHTML = `
    <span class="text-muted small">
      <i class="fa ${iconClass}" aria-hidden="true" title="${type}"></i>
    </span>&nbsp;${citation}
  `

  return div
}

// Utility to clean LaTeX from strings
function cleanLatex(text) {
  if (!text) return "";
  return text
    .replace(/\\[a-zA-Z]+\{([^}]*)\}/g, "$1")
    .replace(/\\[a-zA-Z]+/g, "")
    .replace(/[{}]/g, "");
}

function formatAuthors(authorField){
  if(!authorField) return ""
  const authors = authorField.split(" and ")
  if(authors.length <= 3)
      return authors.join(", ")
  const first = authors[0]
  return `${first} et al.`
}

let viewMode = "grid";

document.addEventListener("DOMContentLoaded", () => {

  document.getElementById("viewToggleBtn").addEventListener("click", () => {

    viewMode = (viewMode === "grid") ? "list" : "grid";

    const icon = document.querySelector("#viewToggleBtn i");

    if (viewMode === "list") {
      icon.className = "fa fa-th-large";
    } else {
      icon.className = "fa fa-list";
    }

    filterAndRender();
  });

});

function filterAndRender() {

  const searchEl = document.getElementById("search");
  const q = searchEl && typeof searchEl.value === "string" ? searchEl.value.trim() : "";

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

  const grid = document.getElementById("grid");
  const list = document.getElementById("list");

  list.style.display = "none";
  grid.style.display = "grid";

  grid.innerHTML = "";

  for (const e of filteredEntries) {
    grid.appendChild(createCard(e));
  }

}

function renderList() {

  const grid = document.getElementById("grid");
  const list = document.getElementById("list");

  grid.style.display = "none";
  list.style.display = "";

  list.innerHTML = "";

  for (const e of filteredEntries) {
    list.appendChild(createListEntry(e));
  }

}
