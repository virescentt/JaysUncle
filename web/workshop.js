// Zakładka "Klasy dokumentów": edycja YAML klas (config/doc_types/*.yaml) → Sprawdź / Testuj na PDF / Zapisz.
// Nowa klasa dokumentu = nowy plik YAML, bez zmian w kodzie.

import { $, esc, api, toast, pct, fmtValue, trunc, badgeHtml, methodHtml, confHtml } from "./util.js";

const ws = {
  classes: [],
  current: null, // {id, file, name, original}
  loaded: false,
  busy: false,
  errorLines: new Set(),
  hooks: { onOpenDocument: () => {}, onSaved: () => {}, getHealth: () => null },
};

const el = {};
const isDirty = () => !!ws.current && el.yaml.value !== ws.current.original;

// ------------------------------------------------------------------ public
export function initWorkshop(hooks) {
  Object.assign(ws.hooks, hooks);
  for (const id of ["yaml", "gutter", "check", "test", "test-file", "save", "revert", "new", "title", "file", "dirty", "msg", "result"])
    el[id.replace("-", "_")] = $(`#ws-${id}`);
  el.list = $("#class-list");

  el.list.addEventListener("click", (e) => {
    const item = e.target.closest(".class-item");
    if (item) openClass(item.dataset.id);
  });
  el.new.addEventListener("click", newClass);
  el.check.addEventListener("click", check);
  el.save.addEventListener("click", save);
  el.revert.addEventListener("click", revert);
  el.test.addEventListener("click", () => el.test_file.click());
  el.test_file.addEventListener("change", () => {
    const f = el.test_file.files[0];
    el.test_file.value = "";
    if (f) test(f);
  });
  el.msg.addEventListener("click", (e) => {
    const b = e.target.closest("[data-line]");
    if (b) goToLine(+b.dataset.line);
  });
  el.result.addEventListener("click", (e) => {
    if (e.target.closest("[data-open]") && ws.lastTest) ws.hooks.onOpenDocument(ws.lastTest);
  });
  initEditor();
  window.addEventListener("beforeunload", (e) => {
    if (isDirty()) e.preventDefault();
  });
}

/** Called whenever the tab becomes visible. */
export function showWorkshop() {
  if (!el.list) return;
  if (!ws.loaded) refreshClasses();
}

export async function refreshClasses() {
  try {
    ws.classes = await api("/api/doc-types?all=true");
    ws.loaded = true;
  } catch (e) {
    el.list.innerHTML = `<li class="err-text small">${esc(e.message)}</li>`;
    return;
  }
  renderList();
}

// ------------------------------------------------------------------ list
function renderList() {
  el.list.innerHTML = ws.classes
    .map((c) => {
      const flags = !c.valid
        ? `<span class="badge error">błąd w pliku</span>`
        : !c.enabled
          ? `<span class="badge unknown">wyłączona</span>`
          : badgeHtml(c.id, "aktywna");
      return `<li><button class="class-item${ws.current?.id === c.id ? " active" : ""}" data-id="${esc(c.id)}" type="button">
        <span class="ci-top"><span class="ci-name">${esc(c.name)}</span>${flags}</span>
        <span class="ci-meta">${esc(c.file)} · ${c.fields.length} pól · ${c.signals} sygn.</span>
      </button></li>`;
    })
    .join("");
  if (!ws.classes.length) el.list.innerHTML = `<li class="muted small">Brak plików w config/doc_types/.</li>`;
}

function confirmDiscard() {
  return !isDirty() || confirm("Masz niezapisane zmiany w edytorze. Porzucić je?");
}

async function openClass(id) {
  if (ws.current?.id === id || !confirmDiscard()) return;
  try {
    const text = await api(`/api/doc-types/${encodeURIComponent(id)}/yaml`);
    const c = ws.classes.find((x) => x.id === id);
    setEditor(text, { id, file: c?.file, name: c?.name || id });
  } catch (e) {
    showMessage("err", esc(e.message));
  }
}

async function newClass() {
  if (!confirmDiscard()) return;
  try {
    const text = await api("/api/doc-types/template");
    setEditor(text, { id: null, file: null, name: null });
    el.yaml.focus();
    toast("Szablon nowej klasy — zmień id, sygnały i pola, potem Testuj i Zapisz", 4000);
  } catch (e) {
    showMessage("err", esc(e.message));
  }
}

function setEditor(text, cur) {
  ws.current = { ...cur, original: text };
  el.yaml.value = text;
  el.yaml.disabled = false;
  el.yaml.scrollTop = 0;
  ws.errorLines = new Set();
  updateGutter();
  updateHeader();
  el.msg.innerHTML = "";
  el.result.innerHTML = "";
  ws.lastTest = null;
  renderList();
}

function updateHeader() {
  const cur = ws.current;
  if (!cur) return;
  el.title.textContent = cur.id ? `Klasa: ${cur.name || cur.id}` : "Nowa klasa (niezapisana)";
  el.file.textContent = cur.file ? `config/doc_types/${cur.file}` : "zostanie zapisana jako config/doc_types/<id>.yaml";
  const dirty = isDirty();
  el.dirty.hidden = !dirty;
  const readonly = !!ws.hooks.getHealth()?.readonly;
  el.check.disabled = el.test.disabled = ws.busy;
  el.save.disabled = ws.busy || readonly;
  el.save.title = readonly ? "Zapisywanie wyłączone na serwerze (ODWOLANIA2JSON_WEB_READONLY)" : "Zapisz do config/doc_types/ i przeładuj potok";
  el.revert.disabled = ws.busy || !dirty;
}

function setBusy(on, text = "") {
  ws.busy = on;
  updateHeader();
  if (on) showMessage("info", `<span class="spinner" aria-hidden="true"></span> ${esc(text)}`);
}

// ------------------------------------------------------------------ editor (textarea + line numbers)
function updateGutter() {
  const n = el.yaml.value.split("\n").length;
  let html = "";
  for (let i = 1; i <= n; i++) html += (ws.errorLines.has(i) ? `<span class="err-line">${i}</span>` : i) + "\n";
  el.gutter.innerHTML = html;
  el.gutter.scrollTop = el.yaml.scrollTop;
}

function goToLine(line) {
  const lines = el.yaml.value.split("\n");
  const idx = Math.max(0, Math.min(lines.length - 1, line - 1));
  let start = 0;
  for (let i = 0; i < idx; i++) start += lines[i].length + 1;
  el.yaml.focus();
  el.yaml.setSelectionRange(start, start + lines[idx].length);
  const lh = parseFloat(getComputedStyle(el.yaml).lineHeight) || 20;
  el.yaml.scrollTop = Math.max(0, idx * lh - el.yaml.clientHeight / 3);
}

function insertText(text) {
  // execCommand keeps the browser's undo history; setRangeText is the fallback
  if (!document.execCommand("insertText", false, text)) {
    el.yaml.setRangeText(text, el.yaml.selectionStart, el.yaml.selectionEnd, "end");
    el.yaml.dispatchEvent(new Event("input"));
  }
}

function initEditor() {
  let escaped = false;
  el.yaml.addEventListener("input", () => {
    updateGutter();
    updateHeader();
  });
  el.yaml.addEventListener("scroll", () => (el.gutter.scrollTop = el.yaml.scrollTop));
  el.yaml.addEventListener("keydown", (e) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "s") {
      e.preventDefault();
      save();
      return;
    }
    if (mod && e.key === "Enter") {
      e.preventDefault();
      check();
      return;
    }
    if (e.key === "Escape") {
      escaped = true;
      return;
    }
    if (e.key === "Tab" && !escaped && !mod && !e.altKey) {
      e.preventDefault();
      const { selectionStart: s, selectionEnd: t, value } = el.yaml;
      const lineStart = value.lastIndexOf("\n", s - 1) + 1;
      if (e.shiftKey) {
        if (value.startsWith("  ", lineStart)) {
          el.yaml.setSelectionRange(lineStart, lineStart + 2);
          insertText("");
          el.yaml.setSelectionRange(Math.max(lineStart, s - 2), Math.max(lineStart, t - 2));
        }
      } else if (s !== t && value.slice(s, t).includes("\n")) {
        const block = value.slice(lineStart, t);
        el.yaml.setSelectionRange(lineStart, t);
        insertText(block.replace(/^/gm, "  "));
      } else {
        insertText("  ");
      }
      return;
    }
    escaped = false;
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && !$("#tab-classes").hidden && ws.current) {
      e.preventDefault();
      save();
    }
  });
}

// ------------------------------------------------------------------ messages
function showMessage(kind, html) {
  el.msg.innerHTML = `<div class="alert ${kind}">${html}</div>`;
}

function issueList(items) {
  return `<ul>${items
    .map(
      (it) =>
        `<li>${it.line ? `<button class="line-link" type="button" data-line="${it.line}" title="przejdź do linii ${it.line}">linia ${it.line}</button> ` : ""}${esc(it.message)}</li>`,
    )
    .join("")}</ul>`;
}

function showReport(rep, okText) {
  ws.errorLines = new Set((rep.errors || []).map((e) => e.line).filter(Boolean));
  updateGutter();
  let html = "";
  if (rep.errors?.length) {
    html += `<div class="alert err"><strong>${rep.errors.length === 1 ? "Znaleziono 1 błąd" : `Znaleziono błędy: ${rep.errors.length}`}</strong>${issueList(rep.errors)}</div>`;
  } else if (okText) {
    html += `<div class="alert ok">${okText}</div>`;
  }
  if (rep.warnings?.length) html += `<div class="alert warn"><strong>Uwagi</strong>${issueList(rep.warnings)}</div>`;
  el.msg.innerHTML = html;
}

function summaryText(rep) {
  const s = rep.summary || {};
  return `✓ Konfiguracja poprawna — klasa „${esc(rep.name)}” (<code>${esc(rep.id)}</code>): ${s.fields} pól, ${s.signals} sygnałów rozpoznawania (próg ${s.min_score}).`;
}

// ------------------------------------------------------------------ actions
async function validate() {
  return api("/api/doc-types/validate", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ yaml: el.yaml.value }),
  });
}

async function check() {
  if (!ws.current || ws.busy) return;
  setBusy(true, "Sprawdzanie…");
  try {
    const rep = await validate();
    showReport(rep, summaryText(rep));
  } catch (e) {
    showMessage("err", esc(e.message));
  } finally {
    setBusy(false);
  }
}

async function test(file) {
  if (!ws.current || ws.busy) return;
  setBusy(true, `Testowanie na pliku ${file.name} (konfiguracja nie jest zapisywana)…`);
  el.result.innerHTML = "";
  const body = new FormData();
  body.append("yaml", el.yaml.value);
  body.append("file", file, file.name);
  try {
    const p = await api("/api/doc-types/test", { method: "POST", body });
    ws.lastTest = p;
    ws.errorLines = new Set();
    updateGutter();
    el.msg.innerHTML = p.test.warnings?.length ? `<div class="alert warn"><strong>Uwagi</strong>${issueList(p.test.warnings)}</div>` : "";
    renderTest(p);
    el.result.scrollIntoView({ block: "nearest", behavior: "smooth" });
  } catch (e) {
    if (e.body?.errors) showReport(e.body);
    else showMessage("err", esc(e.message));
  } finally {
    setBusy(false);
  }
}

function renderTest(p) {
  const t = p.test;
  const verdict = t.recognised
    ? `<p class="verdict ok-text">✓ Dokument rozpoznany jako „${esc(t.name)}” — pewność ${pct(p.confidence)}.</p>`
    : `<p class="verdict err-text">✗ Dokument nie został rozpoznany jako „${esc(t.name)}”. Wynik klasyfikacji: ${
        p.doc_type ? `„${esc(p.doc_type_name || p.doc_type)}”` : "nierozpoznany"
      }.</p>`;
  const rows = Object.entries(t.min_score)
    .sort((a, b) => (p.scores[b[0]] ?? 0) - (p.scores[a[0]] ?? 0))
    .map(([id, min]) => {
      const score = p.scores[id] ?? 0;
      return `<tr class="${id === t.doc_type ? "tested" : ""}"><td>${badgeHtml(id, id)}</td><td>${score}</td><td>${min}</td>
        <td>${score >= min ? '<span class="ok-text">≥ próg</span>' : '<span class="muted">poniżej progu</span>'}</td></tr>`;
    })
    .join("");
  const fields = (p.fields || [])
    .map(
      (f) => `<tr><td><code>${esc(f.path)}</code></td><td class="v">${f.value === null ? '<span class="muted">null</span>' : esc(trunc(fmtValue(f.value), 160))}</td>
        <td>${methodHtml(f.method)}</td><td>${f.method ? confHtml(f.confidence) : "—"}</td></tr>`,
    )
    .join("");
  el.result.innerHTML = `<section class="test-card" aria-label="Wynik testu">
      <h3>Wynik testu: ${esc(p.filename)} <span class="muted small">(${t.replaced ? "klasa zastąpiona" : "klasa dodana"} tymczasowo — nic nie zapisano)</span></h3>
      ${verdict}
      <table class="score-table"><thead><tr><th>klasa</th><th>punkty</th><th>próg (min_score)</th><th></th></tr></thead><tbody>${rows}</tbody></table>
      <p class="muted small">Uzasadnienie: ${esc(p.reason || "—")}</p>
      ${p.doc_type ? `<table class="mini-table"><thead><tr><th>pole</th><th>wartość</th><th>metoda</th><th>pewność</th></tr></thead><tbody>${fields}</tbody></table>` : ""}
      <div><button class="btn" type="button" data-open>Otwórz w podglądzie dokumentu (podświetlenia, JSON)</button></div>
    </section>`;
}

async function save() {
  if (!ws.current || ws.busy) return;
  if (ws.hooks.getHealth()?.readonly) return toast("Zapisywanie klas jest wyłączone na serwerze");
  setBusy(true, "Sprawdzanie przed zapisem…");
  try {
    const rep = await validate();
    if (!rep.ok) {
      showReport(rep);
      return;
    }
    const id = rep.id;
    const cur = ws.current;
    const question =
      cur.id && id !== cur.id
        ? `Zmieniono id klasy z „${cur.id}” na „${id}”.\nZapisać jako NOWĄ klasę „${id}”? Klasa „${cur.id}” pozostanie bez zmian.`
        : !cur.id && ws.classes.some((c) => c.id === id)
          ? `Klasa „${id}” już istnieje. Nadpisać ją?`
          : null;
    if (question && !confirm(question)) {
      el.msg.innerHTML = "";
      return;
    }
    setBusy(true, "Zapisywanie i przeładowanie potoku…");
    const text = el.yaml.value;
    const res = await api(`/api/doc-types/${encodeURIComponent(id)}/yaml`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ yaml: text }),
    });
    ws.current = { id, file: res.file, name: rep.name, original: text };
    await refreshClasses();
    await ws.hooks.onSaved();
    showReport(
      { errors: [], warnings: res.warnings },
      `✓ Zapisano <code>config/doc_types/${esc(res.file)}</code>${res.created ? " (nowa klasa)" : ""}. Potok przeładowany — aktywne klasy: ${res.doc_types.map((d) => `<code>${esc(d)}</code>`).join(", ")}.${
        res.active ? " Nowe dokumenty są już przetwarzane z tą konfiguracją." : " Klasa jest wyłączona (enabled: false)."
      }`,
    );
    updateHeader();
  } catch (e) {
    if (e.body?.errors) showReport(e.body);
    else showMessage("err", esc(e.message));
  } finally {
    setBusy(false);
    updateHeader();
  }
}

function revert() {
  if (!ws.current || !isDirty()) return;
  if (!confirm("Przywrócić ostatnio wczytaną wersję? Zmiany w edytorze zostaną utracone.")) return;
  el.yaml.value = ws.current.original;
  ws.errorLines = new Set();
  updateGutter();
  updateHeader();
  el.msg.innerHTML = "";
}
