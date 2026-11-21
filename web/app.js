// Panel Odwołania2JSON — zakładka "Dokumenty" + nagłówek (stan API, zakładki, motyw).
// Kontrakt API: odwolania2json/api/app.py. Bez frameworków i bez kroku budowania.

import {
  $, $$, esc, uid, api, toast, pct, secs, trunc, squash, fmtValue, setClassOrder, badgeHtml,
  methodHtml, methodLabel, confHtml, classificationMethod, highlightJson, downloadText, copyText,
} from "./util.js";
import { initWorkshop, showWorkshop } from "./workshop.js";

const MAX_PARALLEL_UPLOADS = 2; // = server processing pool (api/store.py PdfWorker); further files wait here
const BOX_PAD = 1.5; // PDF points around highlighted words
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const state = {
  health: null,
  docs: [], // {key, filename, status: queued|processing|done|error, file, payload, error}
  selected: null,
  activeField: null,
  hoverField: null,
  zoom: 1,
  sub: "fields",
  tab: "docs",
};
let running = 0;

const current = () => state.docs.find((d) => d.key === state.selected) || null;
const currentPayload = () => current()?.payload || null;

// =================================================================== health line
export async function loadHealth() {
  const box = $("#health");
  const text = $("#health-text");
  try {
    const h = await api("/api/health");
    state.health = h;
    if (h.status !== "ok") {
      box.className = "health error";
      text.textContent = `Błąd konfiguracji: ${h.error}`;
      box.title = h.error || "";
      return h;
    }
    setClassOrder(h.doc_types);
    box.className = "health ok";
    text.innerHTML =
      `tryb: <b>${esc(h.mode)}</b> · LLM: <b>${esc(h.llm_model ?? "brak")}</b> · ` +
      `klasy: <b>${h.doc_types.map(esc).join(", ") || "—"}</b>`;
    box.title = `Tryb ekstrakcji: ${h.mode}\nModel LLM: ${h.llm_model ?? "brak (tylko reguły)"}\nOCR: ${h.ocr_engine}\nKlasy: ${h.doc_types.join(", ")}`;
    return h;
  } catch (e) {
    state.health = null;
    box.className = "health error";
    text.textContent = "API niedostępne — uruchom ./start.sh web";
    box.title = e.message;
    return null;
  }
}

// =================================================================== tabs + theme
function switchTab(tab, { focus = false } = {}) {
  state.tab = tab;
  for (const btn of $$(".tabs .tab")) {
    const on = btn.dataset.tab === tab;
    btn.setAttribute("aria-selected", String(on));
    btn.tabIndex = on ? 0 : -1;
    if (on && focus) btn.focus();
  }
  $("#tab-docs").hidden = tab !== "docs";
  $("#tab-classes").hidden = tab !== "classes";
  const hash = tab === "classes" ? "#klasy" : "#dokumenty";
  if (location.hash !== hash) history.replaceState(null, "", hash);
  if (tab === "classes") showWorkshop();
}

function initTabs() {
  const tabs = $$(".tabs .tab");
  for (const btn of tabs) {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    btn.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const i = tabs.indexOf(btn);
      const next = tabs[(i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length];
      switchTab(next.dataset.tab, { focus: true });
    });
  }
  switchTab(location.hash === "#klasy" ? "classes" : "docs");
}

const THEMES = ["auto", "light", "dark"];
const THEME_LABEL = { auto: "automatyczny (jak w systemie)", light: "jasny", dark: "ciemny" };
function initTheme() {
  const btn = $("#theme-toggle");
  const apply = (t) => {
    if (t === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    btn.title = `Motyw: ${THEME_LABEL[t]}`;
    try {
      localStorage.setItem("o2j-theme", t);
    } catch {
      /* private mode — theme just is not remembered */
    }
  };
  let theme = document.documentElement.dataset.theme || "auto";
  btn.title = `Motyw: ${THEME_LABEL[theme]}`;
  btn.addEventListener("click", () => {
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    apply(theme);
    toast(`Motyw: ${THEME_LABEL[theme]}`, 1500);
  });
}

// =================================================================== upload queue
function addFiles(fileList) {
  const items = [];
  for (const f of fileList) {
    const isPdf = /\.pdf$/i.test(f.name) || f.type === "application/pdf";
    items.push(
      isPdf
        ? { key: uid(), filename: f.name, status: "queued", file: f }
        : { key: uid(), filename: f.name, status: "error", error: "pominięto — to nie jest plik PDF" },
    );
  }
  if (!items.length) return;
  state.docs.unshift(...items);
  renderDocList();
  pump();
}

function pump() {
  while (running < MAX_PARALLEL_UPLOADS) {
    const queued = state.docs.filter((d) => d.status === "queued");
    const item = queued[queued.length - 1]; // oldest first (new uploads are inserted at the top)
    if (!item) return;
    running++;
    item.status = "processing";
    renderDocList();
    processItem(item).finally(() => {
      running--;
      pump();
    });
  }
}

async function processItem(item) {
  const body = new FormData();
  body.append("files", item.file, item.filename);
  try {
    const [payload] = await api("/api/process", { method: "POST", body });
    item.payload = payload;
    item.status = "done";
  } catch (e) {
    item.status = "error";
    item.error = e.message;
  }
  item.file = null;
  if (!state.docs.includes(item)) {
    // removed from the list while it was being processed → forget it on the server too
    if (item.payload?.id) api(`/api/doc/${item.payload.id}`, { method: "DELETE" }).catch(() => {});
    return;
  }
  if (!state.selected || state.selected === item.key) {
    select(item.key);
  } else {
    renderDocList();
  }
}

function removeDoc(key) {
  const i = state.docs.findIndex((d) => d.key === key);
  if (i < 0) return;
  const [doc] = state.docs.splice(i, 1);
  if (doc.status === "queued") doc.status = "removed";
  if (doc.payload?.id) api(`/api/doc/${doc.payload.id}`, { method: "DELETE" }).catch(() => {});
  if (state.selected === key) {
    const next = state.docs[i] || state.docs[i - 1];
    state.selected = null;
    if (next) select(next.key);
    else renderDetail();
  }
  renderDocList();
}

function initUpload() {
  const zone = $("#dropzone");
  const input = $("#file-input");
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", () => {
    addFiles(input.files);
    input.value = "";
  });
  const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes("Files");
  let depth = 0;
  window.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    depth++;
    document.body.classList.add("dragging");
  });
  window.addEventListener("dragleave", (e) => {
    if (!hasFiles(e)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) document.body.classList.remove("dragging");
  });
  window.addEventListener("dragover", (e) => {
    if (hasFiles(e)) e.preventDefault();
  });
  window.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0;
    document.body.classList.remove("dragging");
    if (state.tab !== "docs") switchTab("docs");
    addFiles(e.dataTransfer.files);
  });
  $("#clear-docs").addEventListener("click", () => {
    for (const d of state.docs) {
      if (d.status === "queued") d.status = "removed";
      if (d.payload?.id) api(`/api/doc/${d.payload.id}`, { method: "DELETE" }).catch(() => {});
    }
    state.docs = state.docs.filter((d) => d.status === "processing");
    state.selected = null;
    renderDocList();
    renderDetail();
  });
}

/** Show a document payload that was produced elsewhere (class workshop test). */
export function openPayload(payload) {
  const item = { key: uid(), filename: payload.filename, status: "done", payload };
  state.docs.unshift(item);
  switchTab("docs");
  select(item.key);
}

// =================================================================== document list
function docMeta(d) {
  if (d.status === "queued") return `<span>w kolejce…</span>`;
  if (d.status === "processing") return `<span class="spinner" aria-hidden="true"></span><span>przetwarzanie…</span>`;
  if (d.status === "error") return `<span class="err-text">${esc(d.error)}</span>`;
  const p = d.payload;
  const parts = [badgeHtml(p.doc_type, p.doc_type_name)];
  if (p.doc_type) parts.push(`<span title="pewność klasyfikacji">${pct(p.confidence)}</span>`);
  if (p.scanned) parts.push(`<span class="flag" title="skan — tekst odczytany przez OCR">OCR</span>`);
  if (p.test_doc_type) parts.push(`<span class="flag test" title="wynik testu niezapisanej klasy">TEST</span>`);
  if (p.errors?.length) parts.push(`<span class="err-text" title="${esc(p.errors.join("\n"))}">⚠ błąd</span>`);
  parts.push(`<span title="czas przetwarzania">${secs(p.total_time)}</span>`);
  return parts.join("");
}

function renderDocList() {
  $("#doc-count").textContent = state.docs.length;
  $("#clear-docs").hidden = !state.docs.length;
  $("#doc-list").innerHTML = state.docs
    .map(
      (d) => `<li>
        <button class="doc-item${d.key === state.selected ? " active" : ""}" data-key="${d.key}" type="button"
          aria-current="${d.key === state.selected}">
          <span class="doc-name" title="${esc(d.filename)}">${esc(d.filename)}</span>
          <span class="doc-meta">${docMeta(d)}</span>
        </button>
        <button class="doc-remove" data-remove="${d.key}" type="button" title="Usuń z listy" aria-label="Usuń ${esc(d.filename)} z listy">×</button>
      </li>`,
    )
    .join("");
}

function initDocList() {
  $("#doc-list").addEventListener("click", (e) => {
    const rm = e.target.closest("[data-remove]");
    if (rm) return removeDoc(rm.dataset.remove);
    const item = e.target.closest(".doc-item");
    if (item) select(item.dataset.key);
  });
}

function select(key) {
  state.selected = key;
  state.activeField = null;
  renderDocList();
  renderDetail();
}

// =================================================================== detail: header
function renderHead(d) {
  const head = $("#doc-head");
  if (d.status !== "done") {
    const msg =
      d.status === "error"
        ? `<div class="alert err">${esc(d.error)}</div>`
        : `<p class="muted"><span class="spinner" aria-hidden="true"></span> ${d.status === "queued" ? "W kolejce…" : "Przetwarzanie — rozpoznawanie rodzaju dokumentu i ekstrakcja pól…"}</p>`;
    head.innerHTML = `<div class="doc-title"><h2>${esc(d.filename)}</h2></div>${msg}`;
    return;
  }
  const p = d.payload;
  const ranked = Object.entries(p.scores || {}).sort((a, b) => b[1] - a[1]);
  const scores = ranked
    .map(([k, v], i) => `<span class="${i === 0 && p.doc_type === k ? "win" : ""}">${esc(k)} ${v > 0 ? "+" : ""}${v}</span>`)
    .join("");
  const filled = p.fields.filter((f) => f.value !== null).length;
  const facts = [];
  if (p.doc_type) facts.push(`<span>pewność klasyfikacji <b>${pct(p.confidence)}</b> (${esc(classificationMethod(p.method))})</span>`);
  facts.push(`<span>stron <b>${p.pages}</b>${p.page_count > p.pages ? ` z ${p.page_count}` : ""}${p.scanned ? " · <b>skan (OCR)</b>" : ""}</span>`);
  if (p.doc_type) facts.push(`<span>pola wypełnione <b>${filled}</b> z ${p.fields.length}</span>`);
  facts.push(`<span>czas <b>${secs(p.total_time)}</b></span>`);
  if (ranked.length) facts.push(`<span title="punkty sygnałów klasyfikacji (YAML)">punkty: <span class="scores">${scores}</span></span>`);

  const alerts = [];
  if (p.errors?.length) alerts.push(`<div class="alert err">${p.errors.map(esc).join("<br>")}</div>`);
  if (!p.doc_type && !p.errors?.length)
    alerts.push(
      `<div class="alert info">Dokument nie należy do żadnej z obsługiwanych klas (${esc(p.reason || "brak sygnałów")}). Zgodnie z wymaganiami wynikiem jest <b>pusty plik ${esc(p.json_filename)}</b>.</div>`,
    );
  if (p.test_doc_type)
    alerts.push(`<div class="alert info">Wynik <b>testu</b> klasy „${esc(p.test_doc_type)}” z edytora — konfiguracja nie została zapisana.</div>`);
  if (p.page_count > p.pages)
    alerts.push(`<div class="alert warn">Przetworzono pierwsze ${p.pages} z ${p.page_count} stron (limit <code>ingest.max_pages</code>).</div>`);

  head.innerHTML = `
    <div class="doc-title"><h2>${esc(p.filename)}</h2>${badgeHtml(p.doc_type, p.doc_type_name, true)}</div>
    <div class="doc-facts">${facts.join("")}</div>
    ${alerts.join("")}`;
}

// =================================================================== detail: pages with highlight boxes
const pageDpi = () => (state.zoom > 1.3 ? 165 : 110);

function boxesHtml(p, page) {
  const w = page.width || 595.28;
  const h = page.height || 841.89;
  let html = "";
  p.fields.forEach((f, i) => {
    for (const b of f.boxes || []) {
      if (b.page !== page.number) continue;
      const [x0, y0, x1, y1] = b.bbox;
      const l = Math.max(0, x0 - BOX_PAD);
      const t = Math.max(0, y0 - BOX_PAD);
      const r = Math.min(w, x1 + BOX_PAD);
      const btm = Math.min(h, y1 + BOX_PAD);
      const cls = ["hl", `kind-${b.kind}`];
      if (f.value === null) cls.push("rejected");
      if (i === state.activeField) cls.push("active");
      const label = `${f.path}: ${f.value === null ? `null (odrzucono: ${fmtValue(f.raw_value)})` : fmtValue(f.value)}${b.kind === "snippet" ? " — przybliżone położenie" : ""}`;
      html += `<div class="${cls.join(" ")}" data-idx="${i}" title="${esc(label)}"
        style="left:${((l / w) * 100).toFixed(3)}%;top:${((t / h) * 100).toFixed(3)}%;width:${(((r - l) / w) * 100).toFixed(3)}%;height:${(((btm - t) / h) * 100).toFixed(3)}%"></div>`;
    }
  });
  return html;
}

function pageCaption(pg) {
  if (pg.source === "ocr") {
    const engine = pg.ocr_engine ? ` (${esc(pg.ocr_engine)})` : "";
    const conf = pg.ocr_confidence !== null && pg.ocr_confidence !== undefined ? `, pewność OCR ${pct(pg.ocr_confidence)}` : "";
    const words = pg.words ? "" : " — bez współrzędnych słów, podświetlenia niedostępne";
    return `Strona ${pg.number} · skan, OCR${engine}${conf}${words}`;
  }
  if (pg.source === "empty") return `Strona ${pg.number} · brak tekstu (OCR niedostępny)`;
  return `Strona ${pg.number} · warstwa tekstowa PDF`;
}

function renderPages(p) {
  const wrap = $("#pages");
  wrap.style.setProperty("--zoom", state.zoom);
  wrap.classList.toggle("hide-boxes", !$("#show-all-boxes").checked);
  const located = p.fields.filter((f) => f.boxes?.length).length;
  const info = $("#pages-info");
  info.textContent = p.page_sizes.length ? `${p.page_sizes.length} str.${p.doc_type ? ` · ${located} pól na stronach` : ""}` : "";
  info.title = p.doc_type ? `Stron: ${p.page_sizes.length}; pola z położeniem na stronie: ${located} z ${p.fields.length}` : "";
  if (!p.page_sizes.length) {
    wrap.innerHTML = `<div class="no-pages">Brak podglądu — nie udało się odczytać dokumentu.</div>`;
    return;
  }
  const dpi = pageDpi();
  wrap.innerHTML = p.page_sizes
    .map((pg) => {
      const w = pg.width || 595.28;
      const h = pg.height || 841.89;
      return `<figure class="page" data-page="${pg.number}">
        <div class="page-inner" style="aspect-ratio:${w} / ${h}">
          <img src="/api/doc/${p.id}/page/${pg.number}.png?dpi=${dpi}" alt="Strona ${pg.number} dokumentu ${esc(p.filename)}"
            loading="lazy" decoding="async" width="${Math.round((w * dpi) / 72)}" height="${Math.round((h * dpi) / 72)}" draggable="false">
          ${boxesHtml(p, pg)}
        </div>
        <figcaption>${pageCaption(pg)}</figcaption>
      </figure>`;
    })
    .join("");
}

function setZoom(z) {
  const before = pageDpi();
  state.zoom = Math.round(Math.min(2.5, Math.max(0.5, z)) * 10) / 10;
  $("#zoom-val").textContent = `${Math.round(state.zoom * 100)}%`;
  const wrap = $("#pages");
  wrap.style.setProperty("--zoom", state.zoom);
  if (pageDpi() !== before) {
    for (const img of $$("img", wrap)) img.src = img.src.replace(/dpi=\d+/, `dpi=${pageDpi()}`);
  }
}

function initPages() {
  const wrap = $("#pages");
  wrap.addEventListener(
    "error",
    (e) => {
      if (e.target.tagName !== "IMG") return;
      const div = document.createElement("div");
      div.className = "page-error";
      div.textContent = "Nie udało się wczytać podglądu strony (sesja wygasła? wgraj plik ponownie)";
      e.target.replaceWith(div);
    },
    true,
  );
  wrap.addEventListener("click", (e) => {
    const box = e.target.closest(".hl");
    if (!box) return;
    if (state.sub !== "fields") setSub("fields");
    if ($("#only-filled").checked && currentPayload()?.fields[+box.dataset.idx]?.value === null) {
      $("#only-filled").checked = false;
      renderFields(currentPayload());
    }
    selectField(+box.dataset.idx, { scrollPage: false, scrollRow: true });
  });
  wrap.addEventListener("mouseover", (e) => {
    const box = e.target.closest(".hl");
    setHover(box ? +box.dataset.idx : null);
  });
  wrap.addEventListener("mouseleave", () => setHover(null));
  $("#zoom-in").addEventListener("click", () => setZoom(state.zoom + 0.2));
  $("#zoom-out").addEventListener("click", () => setZoom(state.zoom - 0.2));
  $("#show-all-boxes").addEventListener("change", (e) => wrap.classList.toggle("hide-boxes", !e.target.checked));
}

// =================================================================== detail: field table
function splitPath(path) {
  const i = path.lastIndexOf(".");
  return i < 0 ? { parent: "", leaf: path } : { parent: path.slice(0, i), leaf: path.slice(i + 1) };
}

function valueHtml(f) {
  // derived = looked up in a dictionary from another field; raw_value is then that field's value (not document text)
  const derived = f.method === "derived";
  if (f.value === null) {
    let s = `<span class="null">null</span>`;
    const shown = f.raw_value !== null && f.raw_value !== undefined ? esc(trunc(squash(fmtValue(f.raw_value)), 70)) : "";
    if (shown && derived) s += `<span class="rejected">brak pozycji słownika dla wartości „${shown}”</span>`;
    else if (shown && f.method)
      s += `<span class="rejected" title="${esc((f.issues || []).join("\n"))}">odrzucono „${shown}” — pewność ${pct(f.confidence)} poniżej progu</span>`;
    return s;
  }
  const raw = f.raw_value !== null && f.raw_value !== undefined && squash(fmtValue(f.raw_value)) !== fmtValue(f.value) ? squash(fmtValue(f.raw_value)) : "";
  let s = `<span${raw ? ` title="${derived ? "ze słownika, dla wartości" : "w dokumencie"}: ${esc(raw)}"` : ""}>${esc(fmtValue(f.value))}</span>`;
  if (f.issues?.length) s += `<span class="issues" title="${esc(f.issues.join("\n"))}" aria-label="uwagi">⚠</span>`;
  if (f.candidates > 1 && f.candidates_detail?.length) {
    const items = f.candidates_detail
      .map((c) => `<li>${esc(trunc(fmtValue(c.value), 80))} — ${esc(methodLabel(c.method))}, ${pct(c.confidence)}${c.page ? `, str. ${c.page}` : ""}</li>`)
      .join("");
    s += `<details><summary>${f.candidates} kandydatów</summary><ul>${items}</ul></details>`;
  }
  return s;
}

function sourceHtml(f) {
  const ev = f.evidence;
  const boxes = f.boxes || [];
  const page = boxes[0]?.page ?? ev?.page;
  if (!page && !ev?.snippet) return `<span class="muted">—</span>`;
  let s = page ? `<span class="pg">str. ${page}</span>` : "";
  if (boxes.length) {
    const approx = boxes[0].kind === "snippet";
    s += ` <span class="loc" title="${approx ? "przybliżone położenie (fragment tekstu)" : "dokładne położenie wartości na stronie"}">${approx ? "≈ fragment" : "◉ na stronie"}</span>`;
  }
  if (ev?.snippet) s += `<span class="snip" title="${esc(squash(ev.snippet))}">„${esc(trunc(squash(ev.snippet), 120))}”</span>`;
  return s;
}

function renderFields(p) {
  const panel = $("#fields-panel");
  $("#sub-fields").innerHTML = `Pola <span class="count">${p.doc_type ? p.fields.filter((f) => f.value !== null).length + "/" + p.fields.length : 0}</span>`;
  $("#only-filled-wrap").hidden = !p.doc_type;
  if (!p.doc_type) {
    panel.innerHTML = `<div class="panel-note">${
      p.errors?.length
        ? "Dokument nie został przetworzony — szczegóły błędu powyżej."
        : `Dokument nierozpoznany — nie wyodrębniono pól. Uzasadnienie klasyfikacji: <i>${esc(p.reason || "—")}</i>.`
    }</div>`;
    return;
  }
  const onlyFilled = $("#only-filled").checked;
  let rows = "";
  let group = null;
  p.fields.forEach((f, i) => {
    if (onlyFilled && f.value === null) return;
    const { parent, leaf } = splitPath(f.path);
    if (parent !== group) {
      group = parent;
      if (parent) rows += `<tr class="group"><td colspan="5">${esc(parent.replaceAll(".", " › "))}</td></tr>`;
    }
    const located = f.boxes?.length ? " located" : "";
    rows += `<tr class="field${i === state.activeField ? " active" : ""}${located}" data-idx="${i}" tabindex="0"
        aria-label="${esc(`${f.path}: ${fmtValue(f.value)}`)}">
      <td class="f-path" title="${esc(f.description ? `${f.path} — ${f.description}` : f.path)}"><span class="leaf">${esc(leaf).replace(/[_-]/g, "$&<wbr>")}</span></td>
      <td class="f-value">${valueHtml(f)}</td>
      <td>${methodHtml(f.method)}</td>
      <td>${f.method && !(f.method === "derived" && f.value === null) ? confHtml(f.confidence) : '<span class="muted">—</span>'}</td>
      <td class="f-src c-src">${sourceHtml(f)}</td>
    </tr>`;
  });
  panel.innerHTML = rows
    ? `<table class="fields-table">
        <colgroup><col class="c-path"><col class="c-value"><col class="c-method"><col class="c-conf"><col class="c-src"></colgroup>
        <thead><tr><th scope="col">Pole</th><th scope="col">Wartość</th><th scope="col">Metoda</th><th scope="col">Pewność</th><th scope="col" class="c-src">Źródło</th></tr></thead>
        <tbody>${rows}</tbody></table>`
    : `<div class="panel-note">Brak wypełnionych pól — odznacz „tylko wypełnione”, aby zobaczyć wszystkie (null).</div>`;
}

function setHover(i) {
  if (state.hoverField === i) return;
  state.hoverField = i;
  for (const el of $$("#pages .hl.hover")) el.classList.remove("hover");
  if (i === null) return;
  for (const el of $$(`#pages .hl[data-idx="${i}"]`)) el.classList.add("hover");
}

function selectField(i, { scrollPage = true, scrollRow = false } = {}) {
  const p = currentPayload();
  if (!p) return;
  state.activeField = i;
  for (const tr of $$("#fields-panel tr.field")) tr.classList.toggle("active", +tr.dataset.idx === i);
  for (const el of $$("#pages .hl.active")) el.classList.remove("active");
  const boxes = $$(`#pages .hl[data-idx="${i}"]`);
  for (const el of boxes) {
    void el.offsetWidth; // restart the pulse animation
    el.classList.add("active");
  }
  const behavior = reducedMotion ? "auto" : "smooth";
  if (scrollPage) {
    const f = p.fields[i];
    if (boxes.length) {
      boxes[0].scrollIntoView({ block: "center", inline: "nearest", behavior });
    } else {
      const page = f.evidence?.page;
      if (page) $(`#pages .page[data-page="${page}"]`)?.scrollIntoView({ block: "start", behavior });
      if (f.value !== null || f.raw_value !== null)
        toast(page ? `Wartość pochodzi ze strony ${page} — dokładne położenie nieznane` : "Brak położenia tej wartości w dokumencie (np. wartość wyliczona ze słownika)");
    }
  }
  if (scrollRow) $(`#fields-panel tr.field[data-idx="${i}"]`)?.scrollIntoView({ block: "nearest", behavior });
}

function initFields() {
  const panel = $("#fields-panel");
  panel.addEventListener("click", (e) => {
    if (e.target.closest("summary, details")) return;
    const tr = e.target.closest("tr.field");
    if (tr) selectField(+tr.dataset.idx);
  });
  panel.addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr.field");
    if (!tr) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      selectField(+tr.dataset.idx);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const rows = $$("tr.field", panel);
      const next = rows[rows.indexOf(tr) + (e.key === "ArrowDown" ? 1 : -1)];
      if (next) next.focus();
    }
  });
  panel.addEventListener("mouseover", (e) => {
    const tr = e.target.closest("tr.field");
    setHover(tr ? +tr.dataset.idx : null);
  });
  panel.addEventListener("mouseleave", () => setHover(null));
  panel.addEventListener("focusin", (e) => {
    const tr = e.target.closest("tr.field");
    if (tr) setHover(+tr.dataset.idx);
  });
  $("#only-filled").addEventListener("change", () => currentPayload() && renderFields(currentPayload()));
}

// =================================================================== detail: JSON
function renderJson(p) {
  const panel = $("#json-panel");
  const text = p.json_text;
  const toolbar = `<div class="json-toolbar">
      <span class="fname">${esc(p.json_filename)}</span>
      <span class="muted small">${text === null ? "" : text ? `${new Blob([text]).size} B` : "0 B"}</span>
      <span class="spacer"></span>
      <button class="btn small" type="button" data-act="copy"${text ? "" : " disabled"}>Kopiuj</button>
      <button class="btn small primary" type="button" data-act="download"${text === null ? " disabled" : ""}>Pobierz .json</button>
    </div>`;
  let body;
  if (text === null) body = `<div class="empty-json">Brak pliku wynikowego (ustawienie <code>output.unknown_output: none</code>).</div>`;
  else if (!text)
    body = `<div class="empty-json"><strong>Pusty plik .json (0 bajtów)</strong>
      <p>Dokument nie został rozpoznany jako żadna z obsługiwanych klas, dlatego — zgodnie z wymaganiami organizatora —
      zapisywany jest pusty plik <code>${esc(p.json_filename)}</code>. Taki sam plik zapisze przetwarzanie wsadowe (<code>./start.sh</code>).</p></div>`;
  else body = `<pre class="json" tabindex="0" aria-label="Wynikowy JSON"><code>${highlightJson(text)}</code></pre>`;
  panel.innerHTML = toolbar + body;
}

function initJson() {
  $("#json-panel").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    const p = currentPayload();
    if (!btn || !p) return;
    if (btn.dataset.act === "copy") toast((await copyText(p.json_text || "")) ? "Skopiowano JSON do schowka" : "Nie udało się skopiować");
    if (btn.dataset.act === "download") downloadText(p.json_text ?? "", p.json_filename);
  });
}

function setSub(sub) {
  state.sub = sub;
  for (const b of $$(".subtab")) {
    const on = b.dataset.sub === sub;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
  }
  $("#fields-panel").hidden = sub !== "fields";
  $("#json-panel").hidden = sub !== "json";
  $("#only-filled-wrap").style.visibility = sub === "fields" ? "visible" : "hidden";
}

function initSubtabs() {
  for (const b of $$(".subtab")) {
    b.addEventListener("click", () => setSub(b.dataset.sub));
    b.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const next = b.dataset.sub === "fields" ? "json" : "fields";
        setSub(next);
        $(`.subtab[data-sub="${next}"]`).focus();
      }
    });
  }
}

// =================================================================== detail
function renderDetail() {
  const d = current();
  $("#empty-state").hidden = !!d;
  $("#doc-view").hidden = !d;
  if (!d) return;
  renderHead(d);
  if (d.status !== "done") {
    $("#pages").innerHTML = "";
    $("#pages-info").textContent = "";
    $("#fields-panel").innerHTML = "";
    $("#json-panel").innerHTML = "";
    return;
  }
  const p = d.payload;
  renderPages(p);
  renderFields(p);
  renderJson(p);
  setSub(state.sub);
  $("#pages").scrollTop = 0;
}

// =================================================================== start
initTheme();
initWorkshop({ onOpenDocument: openPayload, onSaved: loadHealth, getHealth: () => state.health });
initUpload();
initDocList();
initPages();
initFields();
initJson();
initSubtabs();
initTabs();
loadHealth();
