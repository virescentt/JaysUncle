// PLACEHOLDER UI logic — wywołuje API i pokazuje wynik. TODO(ui): docelowy interfejs.

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

async function loadHealth() {
  try {
    const h = await (await fetch("/api/health")).json();
    $("#health").textContent = `tryb: ${h.mode} · LLM: ${h.llm_model ?? "brak"} · klasy: ${h.doc_types.join(", ")}`;
  } catch {
    $("#health").textContent = "API niedostępne";
  }
}

function renderResult(r) {
  const type = r.doc_type
    ? `<span class="badge ok">${esc(r.doc_type)}</span>`
    : `<span class="badge warn">nierozpoznany — brak JSON</span>`;
  const rows = (r.fields || [])
    .map((f) => `<tr>
        <td><code>${esc(f.path)}</code></td>
        <td>${f.value === null ? '<span class="muted">null</span>' : esc(f.value)}</td>
        <td>${esc(f.method ?? "")}</td>
        <td>${f.confidence ? f.confidence.toFixed(2) : ""}</td>
        <td class="muted">${f.evidence ? `str. ${esc(f.evidence.page)}: …${esc(f.evidence.snippet)}…` : ""}</td>
      </tr>`)
    .join("");
  return `<article class="card">
      <h2>${esc(r.filename)} ${type}</h2>
      <p class="muted">pewność ${r.confidence.toFixed(2)} (${esc(r.method)}) · stron: ${r.pages}${r.scanned ? " · OCR" : ""}
        ${r.errors.length ? `<br><span class="err">${esc(r.errors.join("; "))}</span>` : ""}</p>
      ${r.data ? `<details open><summary>JSON</summary><pre>${esc(JSON.stringify(r.data, null, 2))}</pre></details>` : ""}
      ${rows ? `<details><summary>Źródła wartości (przejrzystość)</summary>
        <table><thead><tr><th>pole</th><th>wartość</th><th>metoda</th><th>pewność</th><th>źródło</th></tr></thead>
        <tbody>${rows}</tbody></table></details>` : ""}
    </article>`;
}

$("#upload").addEventListener("submit", async (e) => {
  e.preventDefault();
  const files = $("#files").files;
  if (!files.length) return;
  const body = new FormData();
  for (const f of files) body.append("files", f);
  $("#status").textContent = `Przetwarzanie ${files.length} plik(ów)…`;
  $("#results").innerHTML = "";
  try {
    const res = await fetch("/api/process", { method: "POST", body });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    $("#results").innerHTML = data.map(renderResult).join("");
    $("#status").textContent = "Gotowe.";
  } catch (err) {
    $("#status").textContent = `Błąd: ${err.message}`;
  }
});

loadHealth();
