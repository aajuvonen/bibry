// static/js/api.js
// Handles AJAX calls to the Flask API

async function parseResponse(res) {
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

export async function fetchEntries() {
  const res = await fetch("/api/entries");
  return await res.json();
}

export async function fetchScanServices() {
  const res = await fetch("/api/scan/services");
  return await parseResponse(res);
}

export async function runScan(service) {
  const res = await fetch("/api/scan/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service }),
  });
  return await parseResponse(res);
}

export async function startScanJob(service) {
  const res = await fetch("/api/scan/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service }),
  });
  return await parseResponse(res);
}

export async function fetchScanJob(jobId, cursor = 0) {
  const res = await fetch(`/api/scan/jobs/${encodeURIComponent(jobId)}?cursor=${encodeURIComponent(cursor)}`);
  return await parseResponse(res);
}

export async function cancelScanJob(jobId) {
  const res = await fetch(`/api/scan/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
  });
  return await parseResponse(res);
}

export async function applyScanItem(item) {
  const res = await fetch("/api/scan/review/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: item.id,
      key: item.key,
      raw: item.proposed_raw,
      basis_signature: item.basis_signature,
      provenance: item.provenance || {},
    }),
  });
  return await parseResponse(res);
}

export async function rejectScanItem(item, suppress = false) {
  const res = await fetch("/api/scan/review/reject", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      id: item.id,
      key: item.key,
      fingerprint: item.patch?.fingerprint || "",
      suppress,
    }),
  });
  return await parseResponse(res);
}

export async function clearScanRejections(phase = "") {
  const res = await fetch("/api/scan/rejections/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phase }),
  });
  return await parseResponse(res);
}

export async function attachPdfToEntry(key, file) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`/api/entry/${encodeURIComponent(key)}/pdf`, {
    method: "POST",
    body: formData,
  });
  return await parseResponse(res);
}

export async function markNoPdfExpected(key, enabled = true) {
  const res = await fetch(`/api/entry/${encodeURIComponent(key)}/no-pdf-expected`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return await parseResponse(res);
}

export async function fetchBibFiles() {
  const res = await fetch("/api/bibs");
  return await parseResponse(res);
}

export async function selectBibFile(filename) {
  const res = await fetch("/api/bibs/select", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  return await parseResponse(res);
}

export async function fetchRawEntry(key) {
  const res = await fetch(`/api/entry/${key}`);
  return await res.json();
}

export async function undoLast() {
  const res = await fetch("/api/undo", { method: "POST" });
  return await parseResponse(res);
}

export async function previewImportFile(file) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch("/api/import/preview", {
    method: "POST",
    body: formData,
  });
  return await parseResponse(res);
}

export async function importEntries(entries) {
  const res = await fetch("/api/import", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entries }),
  });
  return await parseResponse(res);
}

export async function exportEntries(keys) {
  const res = await fetch("/api/export", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keys }),
  });
  const blob = await res.blob();
  const exportedCount = Number(res.headers.get("X-Bibry-Exported-Count") || "0");
  let error = "";
  if (!res.ok) {
    error = await blob.text();
  }
  return { ok: res.ok, status: res.status, blob, exportedCount, error };
}

export async function fetchHistory() {
  const res = await fetch("/api/history");
  return await parseResponse(res);
}

export async function restoreHistory(revisionId) {
  const res = await fetch(`/api/history/${encodeURIComponent(revisionId)}/restore`, {
    method: "POST",
  });
  return await parseResponse(res);
}
