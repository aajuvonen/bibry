// static/js/editor.js
// Handles showing and editing entry details

import { fetchRawEntry } from './api.js';

export async function showEntry(key) {
  const res = await fetchRawEntry(key);
  if (res.error) {
    alert("Error loading entry");
    return;
  }
  document.getElementById("details").textContent = res.raw;
}

export async function openEditor(key){
  const modal = new bootstrap.Modal(
        document.getElementById("editModal"))
  const textarea =
      document.getElementById("editRaw")
  const res =
      await fetch(`/api/entry/${key}`)
  const data =
      await res.json()
  textarea.value = data.raw
  modal.show()
  document.getElementById("saveEdit").onclick =
      ()=>saveEdit(key, modal)
  document.getElementById("cancelEdit").onclick =
      ()=>modal.hide()
}

async function saveEdit(key, modal){
  const raw =
      document.getElementById("editRaw").value
  if(raw.trim()===""){
      if(!confirm("Delete this entry?"))
          return
  }
  await fetch(`/api/entry/${key}`,{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({raw})
  })
  modal.hide()
  location.reload()
}