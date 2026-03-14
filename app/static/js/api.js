// static/js/api.js
// Handles AJAX calls to the Flask API

export async function fetchEntries() {
  const res = await fetch("/api/entries");
  return await res.json();
}

export async function fetchRawEntry(key) {
  const res = await fetch(`/api/entry/${key}`);
  return await res.json();
}

export async function undoLast() {
  const res = await fetch("/api/undo", { method: "POST" });
  const text = await res.text();
  let data = {};
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }
  }
  return { ok: res.ok, status: res.status, ...data };
}
