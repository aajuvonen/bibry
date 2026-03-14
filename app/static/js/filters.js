// static/js/filters.js
// Implements search and sort filtering

let entries = [];
let filtered = [];
let indexList = [];

// Build a search index from all fields of each entry
export function buildIndex(data) {
  entries = data;
  indexList = entries.map(e => ({
    entry: e,
    text: Object.values(e.fields || {}).join(" ").toLowerCase()
  }));
  filtered = [...entries];
}

// Apply search and sort to produce filtered[]
export function applyFilters(query, field, dir){
  const q = (query||"").toLowerCase()
  filtered = indexList
    .filter(i => !q || i.text.includes(q))
    .map(i => i.entry)
  sortEntries(field, dir)
  return filtered
}

function sortEntries(field, dir){
  const safe = v => (v || "").toString().toLowerCase()
  filtered.sort((a,b)=>{
    const va = safe(a.fields[field])
    const vb = safe(b.fields[field])
    if(dir === "asc")
        return va.localeCompare(vb)
    return vb.localeCompare(va)
  })
}