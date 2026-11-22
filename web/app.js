// Panel JaysUncle — zakładka "Dokumenty" + nagłówek (stan API, zakładki, motyw).
// Kontrakt API: odwolania2json/api/app.py. Bez frameworków i bez kroku budowania.

import {
  $, $$, esc, uid, api, toast, pct, secs, trunc, squash, fmtValue, setClassOrder, badgeHtml, methodLabel,
  classificationMethod, highlightJson, downloadText, copyText, plural, fieldLabel, confLevel, confWord,
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
  sub: "json",
  tab: "docs",
  closedGroups: new Set(), // field groups the user folded in "Pola i źródła" (kept across documents / re-renders)
};
let running = 0;

const current = () => state.docs.find((d) => d.key === state.selected) || null;
const currentPayload = () => current()?.payload || null;

// =================================================================== status indicator (+ technical details popover)
const MODE_NAMES = { rules: "reguły", llm: "model językowy", hybrid: "reguły + model językowy" };

function llmLabel(h) {
  if (!h.llm_model) return "brak (tylko reguły)";
  if (h.llm_status === "unavailable") return `${h.llm_model} — niedostępny (brak klucza lub internetu)`;
  if (h.llm_status === "standby") return `${h.llm_model} — gotowy dla typów odczytywanych przez model`;
  return h.llm_model;
}

function healthDetails(h, error) {
  const rows = h
    ? [
        ["Sposób odczytu danych", MODE_NAMES[h.mode] || h.mode || "—"],
        ["Model językowy", llmLabel(h)],
        ["Odczyt skanów (OCR)", h.ocr_engine || "—"],
        ["Typy dokumentów", (h.doc_types || []).map((id) => h.doc_type_names?.[id] || id).join(", ") || "—"],
        ["Zapisywanie typów", h.readonly ? "wyłączone" : "włączone"],
        ["Wersja programu", h.version || "—"],
      ]
    : [["Błąd", error || "brak połączenia"]];
  return `<h2 class="pop-title">Szczegóły techniczne</h2><dl class="pop-list">${rows
    .map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`)
    .join("")}</dl>`;
}

export async function loadHealth() {
  const box = $("#health");
  const text = $("#health-text");
  const pop = $("#health-pop");
  try {
    const h = await api("/api/health");
    state.health = h;
    if (h.status !== "ok") {
      box.className = "health error";
      text.textContent = "Błąd konfiguracji";
      pop.innerHTML = healthDetails(null, h.error);
      return h;
    }
    setClassOrder(h.doc_types);
    box.className = "health ok";
    const n = h.doc_types.length;
    text.innerHTML = `Gotowe <span class="health-sub">· ${n} ${plural(n, ["typ dokumentu", "typy dokumentów", "typów dokumentów"])}</span>`;
    pop.innerHTML = healthDetails(h);
    return h;
  } catch (e) {
    state.health = null;
    box.className = "health error";
    text.textContent = "Brak połączenia z serwerem";
    pop.innerHTML = healthDetails(null, e.message);
    return null;
  }
}

function initHealth() {
  const btn = $("#health-info");
  const pop = $("#health-pop");
  const toggle = (open) => {
    pop.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
  };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggle(pop.hidden);
  });
  document.addEventListener("click", (e) => {
    if (!pop.hidden && !pop.contains(e.target)) toggle(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !pop.hidden) {
      toggle(false);
      btn.focus();
    }
  });
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
  // logo + name = home ("Dokumenty")
  $("#brand-home").addEventListener("click", (e) => {
    e.preventDefault();
    switchTab("docs", { focus: true });
  });
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
  zone.addEventListener("click", () => input.click()); // the whole zone is clickable; the button inside is the keyboard target
  $("#add-files").addEventListener("click", () => input.click());
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
    if (!state.docs.length) $("#pick-files").focus();
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
function docStatus(d) {
  if (d.status === "queued") return { cls: "wait", text: "W kolejce…" };
  if (d.status === "processing") return { cls: "wait", text: "Przetwarzanie…", spin: true };
  if (d.status === "error") return { cls: "err", text: "Błąd" };
  const p = d.payload;
  if (p.errors?.length) return { cls: "err", text: "Błąd odczytu" };
  if (!p.doc_type) return { cls: "none", text: "Nie rozpoznano — pusty JSON {}" };
  return { cls: "ok", text: "Rozpoznano" };
}

function docTypeLine(d) {
  if (d.status === "error") return `<span class="err-text">${esc(d.error)}</span>`;
  if (d.status !== "done") return "";
  const p = d.payload;
  if (!p.doc_type) return `<span class="muted">Nieznany rodzaj dokumentu</span>`;
  const extra = [p.scanned ? "skan" : "", p.test_doc_type ? "test" : ""].filter(Boolean).join(" · ");
  return `<span class="doc-type">${esc(p.doc_type_name || p.doc_type)}</span>${extra ? `<span class="muted"> · ${extra}</span>` : ""}`;
}

function renderDocList() {
  const has = state.docs.length > 0;
  $("#docs-empty").hidden = has;
  $("#docs-work").hidden = !has;
  $("#doc-count").textContent = state.docs.length;
  $("#clear-docs").hidden = !has;
  $("#doc-list").innerHTML = state.docs
    .map((d) => {
      const st = docStatus(d);
      return `<li>
        <button class="doc-item${d.key === state.selected ? " active" : ""}" data-key="${d.key}" type="button"
          aria-current="${d.key === state.selected ? "true" : "false"}">
          <span class="doc-name" title="${esc(d.filename)}">${esc(d.filename)}</span>
          <span class="doc-type-line">${docTypeLine(d)}</span>
          <span class="status ${st.cls}">${st.spin ? '<span class="spinner" aria-hidden="true"></span>' : ""}${esc(st.text)}</span>
        </button>
        <button class="doc-remove" data-remove="${d.key}" type="button" title="Usuń z listy" aria-label="Usuń ${esc(d.filename)} z listy">×</button>
      </li>`;
    })
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
        : `<p class="muted"><span class="spinner" aria-hidden="true"></span> ${d.status === "queued" ? "Czeka w kolejce…" : "Trwa rozpoznawanie dokumentu i odczyt danych…"}</p>`;
    head.innerHTML = `<div class="doc-title"><h2>${esc(d.filename)}</h2></div>${msg}`;
    return;
  }
  const p = d.payload;
  const filled = p.fields.filter((f) => f.value !== null).length;
  const ranked = Object.entries(p.scores || {}).sort((a, b) => b[1] - a[1]);
  const names = state.health?.doc_type_names || {};
  const lvl = confLevel(p.confidence);
  const st = docStatus(d);
  const pages = p.pages;
  const tech = [
    p.doc_type && lvl ? `<div><dt>Pewność rozpoznania</dt><dd>${lvl.text} (${pct(p.confidence)}, ${esc(classificationMethod(p.method))})</dd></div>` : "",
    p.doc_type ? `<div><dt>Znalezione pola</dt><dd>${filled} z ${p.fields.length}</dd></div>` : "",
    `<div><dt>Strony</dt><dd>${pages}${p.page_count > p.pages ? ` z ${p.page_count}` : ""}${p.scanned ? " (skan)" : ""}</dd></div>`,
    ranked.length
      ? `<div><dt>Punkty rozpoznawania</dt><dd>${ranked.map(([k, v]) => `${esc(names[k] || k)}: ${v > 0 ? "+" : ""}${v}`).join(" · ")}</dd></div>`
      : "",
    p.reason ? `<div><dt>Uzasadnienie</dt><dd>${esc(p.reason)}</dd></div>` : "",
    `<div><dt>Czas przetwarzania</dt><dd>${secs(p.total_time)}</dd></div>`,
  ].join("");

  const alerts = [];
  if (p.errors?.length) alerts.push(`<div class="alert err" role="alert">${p.errors.map(esc).join("<br>")}</div>`);
  if (p.test_doc_type)
    alerts.push(`<div class="alert info">To wynik <b>testu</b> typu dokumentu z edytora — zmiany w typie nie zostały zapisane.</div>`);
  if (p.page_count > p.pages)
    alerts.push(`<div class="alert warn">Odczytano tylko pierwsze ${p.pages} z ${p.page_count} stron (limit stron w ustawieniach).</div>`);

  head.innerHTML = `
    <div class="doc-title">
      <h2>${esc(p.filename)}</h2>
      ${p.doc_type ? badgeHtml(p.doc_type, p.doc_type_name, true) : ""}
      <span class="status ${st.cls}">${esc(st.text)}</span>
      <span class="spacer"></span>
      <details class="tech stats"><summary>Statystyki</summary><dl class="tech-list">${tech}</dl></details>
    </div>
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
      const label = `${fieldLabel(f.path).label}: ${f.value === null ? `odrzucono niepewną wartość „${fmtValue(f.raw_value)}”` : fmtValue(f.value)}${b.kind === "snippet" ? " (przybliżone położenie)" : ""}`;
      html += `<div class="${cls.join(" ")}" data-idx="${i}" title="${esc(label)}"
        style="left:${((l / w) * 100).toFixed(3)}%;top:${((t / h) * 100).toFixed(3)}%;width:${(((r - l) / w) * 100).toFixed(3)}%;height:${(((btm - t) / h) * 100).toFixed(3)}%"></div>`;
    }
  });
  return html;
}

function pageCaption(pg) {
  if (pg.source === "ocr") {
    const words = pg.words ? "" : " — bez zaznaczeń na stronie";
    return `Strona ${pg.number} · skan odczytany automatycznie${words}`;
  }
  if (pg.source === "empty") return `Strona ${pg.number} · nie udało się odczytać tekstu`;
  return `Strona ${pg.number}`;
}

function renderPages(p) {
  const wrap = $("#pages");
  wrap.style.setProperty("--zoom", state.zoom);
  wrap.classList.toggle("hide-boxes", !$("#show-all-boxes").checked);
  const located = p.fields.filter((f) => f.boxes?.length).length;
  const info = $("#pages-info");
  const n = p.page_sizes.length;
  info.textContent = n ? `${n} ${plural(n, ["strona", "strony", "stron"])}` : "";
  if (!p.page_sizes.length) {
    wrap.innerHTML = `<div class="no-pages">Brak podglądu — nie udało się otworzyć dokumentu.</div>`;
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
  state.zoom = Math.round(Math.min(3, Math.max(0.5, z)) * 100) / 100;
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
      div.textContent = "Nie udało się wczytać podglądu strony — dodaj plik ponownie.";
      e.target.replaceWith(div);
    },
    true,
  );
  // grab and drag the page with the left mouse button (the pane scrolls with it); a click without movement still
  // selects a highlighted field. Touch keeps native scrolling.
  let drag = null;
  let swallowClick = false;
  wrap.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse" || e.button !== 0) return;
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, left: wrap.scrollLeft, top: wrap.scrollTop, moved: false };
  });
  wrap.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dx = e.clientX - drag.x;
    const dy = e.clientY - drag.y;
    if (!drag.moved) {
      if (Math.hypot(dx, dy) < 5) return;
      drag.moved = true;
      wrap.classList.add("grabbing");
      wrap.setPointerCapture(e.pointerId);
    }
    wrap.scrollLeft = drag.left - dx;
    wrap.scrollTop = drag.top - dy;
  });
  const endDrag = () => {
    if (drag?.moved) {
      wrap.classList.remove("grabbing");
      swallowClick = true; // the click that ends a drag must not select a field
      setTimeout(() => (swallowClick = false), 0);
    }
    drag = null;
  };
  wrap.addEventListener("pointerup", endDrag);
  wrap.addEventListener("pointercancel", endDrag);
  wrap.addEventListener("dragstart", (e) => e.preventDefault());

  // trackpad pinch zooms the page preview, not the whole browser page: Chrome / Edge / Firefox send it as
  // wheel + ctrlKey (also Ctrl + mouse wheel), Safari as gesture events. The point under the fingers stays put.
  const zoomAt = (z, clientX, clientY) => {
    const old = state.zoom;
    const r = wrap.getBoundingClientRect();
    const x = clientX - r.left;
    const y = clientY - r.top;
    setZoom(z);
    const k = state.zoom / old;
    if (k === 1) return;
    wrap.scrollLeft = (wrap.scrollLeft + x) * k - x;
    wrap.scrollTop = (wrap.scrollTop + y) * k - y;
  };
  wrap.addEventListener(
    "wheel",
    (e) => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      const d = Math.max(-25, Math.min(25, e.deltaY)); // a mouse wheel notch (±100) must not jump 3×
      zoomAt(state.zoom * Math.exp(-d * 0.01), e.clientX, e.clientY);
    },
    { passive: false },
  );
  let gestureBase = 1;
  wrap.addEventListener("gesturestart", (e) => {
    e.preventDefault();
    gestureBase = state.zoom;
  });
  wrap.addEventListener("gesturechange", (e) => {
    e.preventDefault();
    zoomAt(gestureBase * e.scale, e.clientX, e.clientY);
  });
  wrap.addEventListener(
    "click",
    (e) => {
      if (swallowClick) e.stopImmediatePropagation();
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

// =================================================================== detail: fields (human labels, plain confidence, details on demand)
function valueHtml(f) {
  // derived = looked up in a dictionary from another field; raw_value is then that field's value (not document text)
  const derived = f.method === "derived";
  if (f.value === null) {
    let s = `<span class="null" title="Dokument nie zawiera tej informacji — w JSON: null">Brak w dokumencie</span>`;
    const shown = f.raw_value !== null && f.raw_value !== undefined ? esc(trunc(squash(fmtValue(f.raw_value)), 70)) : "";
    if (shown && derived) s += `<span class="rejected">brak pozycji w słowniku dla „${shown}”</span>`;
    else if (shown && f.method) s += `<span class="rejected">pominięto niepewną wartość „${shown}”</span>`;
    return s;
  }
  let v = f.value;
  if (typeof v === "boolean") v = v ? "tak" : "nie";
  let s = `<span class="val">${esc(fmtValue(v))}</span>`;
  if (f.issues?.length) s += `<span class="issues" title="${esc(f.issues.join("\n"))}">⚠ do sprawdzenia</span>`;
  return s;
}

// compact confidence marker for field rows (the word is in "Szczegóły" → Pewność and in the tooltip)
function confDot(c) {
  const l = confLevel(c);
  if (!l) return "";
  const icon = l.key === "hi" ? "●" : l.key === "mid" ? "◐" : "○";
  return `<span class="conf-dot ${l.key}" title="Pewność odczytu: ${l.text.toLowerCase()}"><span aria-hidden="true">${icon}</span><span class="sr-only">Pewność: ${l.text}</span></span>`;
}

function sourceHtml(f) {
  const ev = f.evidence;
  const boxes = f.boxes || [];
  const page = boxes[0]?.page ?? ev?.page;
  if (!page && !ev?.snippet) return f.method === "derived" && f.value !== null ? `<span class="src">Ze słownika</span>` : "";
  const where = page ? `Strona ${page}` : "";
  const snip = ev?.snippet ? `<span class="snip">„${esc(trunc(squash(ev.snippet), 110))}”</span>` : "";
  return `<span class="src">${where}${where && snip ? " · " : ""}${snip}</span>`;
}

function moreHtml(f, derived = []) {
  const rows = [];
  if (f.description) rows.push(["Opis pola", esc(f.description)]);
  if (f.method) rows.push(["Metoda", esc(methodLabel(f.method))]);
  if (f.method && f.confidence !== null && f.confidence !== undefined) rows.push(["Pewność", pct(f.confidence)]);
  const raw = f.raw_value !== null && f.raw_value !== undefined ? squash(fmtValue(f.raw_value)) : "";
  if (raw && raw !== fmtValue(f.value)) rows.push([f.method === "derived" ? "Wartość źródłowa" : "Tekst w dokumencie", esc(trunc(raw, 200))]);
  if (f.issues?.length) rows.push(["Uwagi", f.issues.map(esc).join("<br>")]);
  const src = sourceHtml(f);
  if (src) rows.push(["Źródło", src]);
  if (f.candidates > 1 && f.candidates_detail?.length)
    rows.push([
      "Inne kandydatury",
      `<ul>${f.candidates_detail
        .map((c) => `<li>${esc(trunc(fmtValue(c.value), 80))} — ${esc(methodLabel(c.method))}, ${pct(c.confidence)}${c.page ? `, str. ${c.page}` : ""}</li>`)
        .join("")}</ul>`,
    ]);
  rows.push(["Ścieżka w JSON", `<code>${esc(f.path)}</code>`]);
  for (const d of derived) rows.push([`${esc(fieldLabel(d.path).label)} (ze słownika)`, `${esc(fmtValue(d.value))} · <code>${esc(d.path)}</code>`]);
  return `<details class="field-more"><summary title="Szczegóły"><span class="sr-only">Szczegóły</span></summary><dl>${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl></details>`;
}

function renderFields(p) {
  const panel = $("#fields-panel");
  const filled = p.fields.filter((f) => f.value !== null).length;
  $("#sub-fields").innerHTML = `Pola i źródła${p.doc_type ? ` <span class="count">${filled}/${p.fields.length}</span>` : ""}`;
  $("#only-filled-wrap").hidden = !p.doc_type;
  if (!p.doc_type) {
    panel.innerHTML = `<div class="panel-note">${
      p.errors?.length
        ? "Dokument nie został przetworzony — szczegóły błędu powyżej."
        : "Dokument nie został rozpoznany, więc nie odczytano z niego danych."
    }</div>`;
    return;
  }
  const onlyFilled = $("#only-filled").checked;
  // dictionary-derived fields (e.g. opis from powód id) are shown inside their source field, not as own rows
  const parentOf = (path) => path.slice(0, Math.max(0, path.lastIndexOf(".")));
  const derivedOf = new Map(); // source path → [derived field]
  const attached = new Set();
  for (const f of p.fields) {
    if (!f.derived_from && f.method !== "derived") continue;
    const parent = parentOf(f.path);
    const src = f.derived_from ? (parent ? `${parent}.${f.derived_from}` : f.derived_from) : null;
    if (src && p.fields.some((x) => x.path === src)) {
      if (!derivedOf.has(src)) derivedOf.set(src, []);
      derivedOf.get(src).push(f);
      attached.add(f);
    }
  }
  // one foldable block per group ("Odwołanie KIO", "Odwołujący — nr 1", …) with "found / all" in its header
  const stats = new Map();
  for (const f of p.fields) {
    if (attached.has(f)) continue;
    const g = fieldLabel(f.path).group || "";
    const s = stats.get(g) || { found: 0, all: 0 };
    s.all += 1;
    if (f.value !== null) s.found += 1;
    stats.set(g, s);
  }
  // a single group (e.g. a bank transfer: everything under "Przelew bankowy") → a plain list, no group header
  const grouped = stats.size > 1;
  let html = grouped ? "" : `<ul class="fields">`;
  let group = null;
  p.fields.forEach((f, i) => {
    if (attached.has(f) || (onlyFilled && f.value === null)) return;
    const { group: gRaw, label } = fieldLabel(f.path);
    const g = gRaw || "";
    if (grouped && g !== group) {
      if (group !== null) html += `</ul></details>`;
      group = g;
      const s = stats.get(g);
      html += `<details class="fgroup" data-group="${esc(g)}"${state.closedGroups.has(g) ? "" : " open"}>
        <summary class="field-group"><span class="fgroup-name">${esc(g || "Dane dokumentu")}</span>
          <span class="fgroup-count" title="znalezione / wszystkie pola">${s.found}/${s.all}</span></summary>
        <ul class="fields">`;
    }
    const derived = (derivedOf.get(f.path) || []).filter((d) => d.value !== null);
    const extra = derived.map((d) => ` — ${esc(fmtValue(d.value))}`).join("");
    const tag = derived.length ? `<span class="from-dict">· ze słownika</span>` : "";
    if (f.value === null && (f.raw_value === null || f.raw_value === undefined)) {
      // genuinely not found: one quiet line, no details
      html += `<li class="field empty" data-idx="${i}">
        <div class="field-quiet"><span class="field-label">${esc(label)}</span><span class="null" title="Dokument nie zawiera tej informacji — w JSON: null">brak w dokumencie</span></div>
      </li>`;
      return;
    }
    if (f.method === "derived" && !f.boxes?.length) {
      // derived without a visible source field: plain line, not clickable
      html += `<li class="field derived" data-idx="${i}">
        <div class="field-quiet"><span class="field-label">${esc(label)}</span><span>${esc(fmtValue(f.value))} <span class="from-dict">· ze słownika</span></span></div>
      </li>`;
      return;
    }
    const lvl = f.value !== null && f.method && f.method !== "derived" ? confDot(f.confidence) : "";
    html += `<li class="field${i === state.activeField ? " active" : ""}${f.value === null ? " empty" : ""}" data-idx="${i}">
      <button class="field-main" type="button" data-idx="${i}" aria-pressed="${i === state.activeField}">
        <span class="field-label">${esc(label)}</span>
        <span class="field-value">${valueHtml(f)}${extra ? `<span class="val-extra">${extra}</span>` : ""} ${tag}</span>
        ${lvl}
      </button>
      ${moreHtml(f, derived)}
    </li>`;
  });
  if (grouped && group !== null) html += `</ul></details>`;
  if (!grouped) html = html === `<ul class="fields">` ? "" : `${html}</ul>`;
  panel.innerHTML = html || `<div class="panel-note">Nie znaleziono żadnej wartości — odznacz „Tylko znalezione”, aby zobaczyć wszystkie pola.</div>`;
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
  const group = $(`#fields-panel li.field[data-idx="${i}"]`)?.closest("details.fgroup");
  if (group && !group.open) group.open = true;
  for (const li of $$("#fields-panel li.field")) {
    const on = +li.dataset.idx === i;
    li.classList.toggle("active", on);
    li.querySelector(".field-main")?.setAttribute("aria-pressed", String(on));
  }
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
      if (page) $(`#pages .page[data-page="${page}"]`)?.scrollIntoView({ block: "start", behavior }); // no exact place → just the page
    }
  }
  if (scrollRow) $(`#fields-panel li.field[data-idx="${i}"]`)?.scrollIntoView({ block: "nearest", behavior });
}

function initFields() {
  // remember folded groups ("toggle" does not bubble → capture)
  $("#fields-panel").addEventListener(
    "toggle",
    (e) => {
      const d = e.target;
      if (!d.classList?.contains("fgroup")) return;
      if (d.open) state.closedGroups.delete(d.dataset.group);
      else state.closedGroups.add(d.dataset.group);
    },
    true,
  );
  const panel = $("#fields-panel");
  panel.addEventListener("click", (e) => {
    const btn = e.target.closest(".field-main");
    if (btn) selectField(+btn.dataset.idx);
  });
  panel.addEventListener("keydown", (e) => {
    const btn = e.target.closest(".field-main");
    if (!btn || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
    e.preventDefault();
    const all = $$(".field-main", panel);
    const next = all[all.indexOf(btn) + (e.key === "ArrowDown" ? 1 : -1)];
    if (next) next.focus();
  });
  panel.addEventListener("mouseover", (e) => {
    const li = e.target.closest("li.field");
    setHover(li ? +li.dataset.idx : null);
  });
  panel.addEventListener("mouseleave", () => setHover(null));
  panel.addEventListener("focusin", (e) => {
    const li = e.target.closest("li.field");
    if (li) setHover(+li.dataset.idx);
  });
  $("#only-filled").addEventListener("change", () => currentPayload() && renderFields(currentPayload()));
}

// =================================================================== detail: JSON
function renderJson(p) {
  const panel = $("#json-panel");
  const text = p.json_text;
  const toolbar = `<div class="json-toolbar">
      <span class="fname">${esc(p.json_filename)}</span>
      <span class="spacer"></span>
      <button class="btn small" type="button" data-act="copy"${text ? "" : " disabled"}>Kopiuj</button>
      <button class="btn small primary" type="button" data-act="download"${text === null ? " disabled" : ""}>Pobierz plik JSON</button>
    </div>`;
  let body;
  if (text === null) body = `<div class="empty-json">Dla tego dokumentu nie tworzy się pliku wynikowego (tak ustawiono w konfiguracji).</div>`;
  else if (!text)
    body = `<div class="empty-json"><strong>Pusty plik JSON</strong>
      <p>Dokument nie został rozpoznany jako żaden z typów dokumentów, dlatego — zgodnie z zasadami — wynikiem jest pusty plik.
      Taki sam plik powstanie przy przetwarzaniu wsadowym.</p></div>`;
  else {
    body = `<pre class="json" tabindex="0" aria-label="Wynik w formacie JSON"><code>${highlightJson(text)}</code></pre>`;
    if (!p.doc_type)
      body = `<div class="empty-json"><strong>Pusty JSON {}</strong>
        <p>Dokument nie został rozpoznany jako żaden z typów dokumentów, dlatego — zgodnie z zasadami — wynikiem jest pusty obiekt JSON.
        Taki sam plik powstanie przy przetwarzaniu wsadowym.</p></div>${body}`;
  }
  panel.innerHTML = toolbar + body;
}

function initJson() {
  $("#json-panel").addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-act]");
    const p = currentPayload();
    if (!btn || !p) return;
    if (btn.dataset.act === "copy") toast((await copyText(p.json_text || "")) ? "Skopiowano do schowka" : "Nie udało się skopiować");
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
  $("#detail-empty").hidden = !!d;
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
initHealth();
$("#pick-files").addEventListener("click", (e) => {
  e.stopPropagation(); // the zone's own click handler would open the dialog a second time
  $("#file-input").click();
});
initWorkshop({ onOpenDocument: openPayload, onSaved: loadHealth, getHealth: () => state.health });
initUpload();
initDocList();
initPages();
initFields();
initJson();
initSubtabs();
initTabs();
renderDocList();
loadHealth();
