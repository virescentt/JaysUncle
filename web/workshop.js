// Zakładka "Klasy dokumentów": edycja YAML klas (config/doc_types/*.yaml) → Sprawdź / Testuj na PDF / Zapisz.
// Nowa klasa dokumentu = nowy plik YAML, bez zmian w kodzie.
// Dwa tryby edycji tej samej klasy: "Formularz" (classform.js, dla osób nieznających YAML) i "YAML" (surowy tekst).
// Pole tekstowe YAML jest zawsze źródłem prawdy dla Sprawdź / Testuj / Zapisz: w trybie formularza zmiany są najpierw
// zamieniane na YAML (/api/doc-types/dump). Przełączenie bez zmian w formularzu nie zmienia tekstu YAML (komentarze zostają).

import { $, esc, api, toast, fmtValue, trunc, plural, fieldLabel, confWord, humanizeKey } from "./util.js";
import { buildModel, modelToJson, renderForm, canon } from "./classform.js";
import { openWizard, closeWizard, wizardOpen, wizardDirty } from "./wizard.js";

const ws = {
  classes: [],
  current: null, // {id, file, name, original}
  loaded: false,
  busy: false,
  errorLines: new Set(),
  mode: "form", // "form" | "yaml"
  meta: null, // GET /api/doc-types/meta
  form: null, // {model, snapshot (JSON of the model when loaded / last synced), losses}
  hooks: { onOpenDocument: () => {}, onSaved: () => {}, getHealth: () => null },
};

const el = {};
const formJson = () => (ws.form ? modelToJson(ws.form.model) : null);
const formChanged = () => ws.mode === "form" && !!ws.form && JSON.stringify(formJson().json) !== ws.form.snapshot;
const isDirty = () => wizardDirty() || (!!ws.current && (el.yaml.value !== ws.current.original || formChanged()));
const jsonPost = (path, body) =>
  api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
const jsonPut = (path, body) =>
  api(path, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

// ------------------------------------------------------------------ public
export function initWorkshop(hooks) {
  Object.assign(ws.hooks, hooks);
  for (const id of ["yaml", "gutter", "check", "test", "test-file", "save", "revert", "new", "title", "file", "dirty", "msg", "result",
    "form", "editor", "preview", "preview-dlg", "preview-text", "modes", "keys", "mode-toggle", "placeholder", "actions", "readonly-note", "wizard"])
    el[id.replaceAll("-", "_")] = $(`#ws-${id}`);
  try {
    ws.mode = localStorage.getItem("ws-mode") === "yaml" ? "yaml" : "form";
  } catch {
    /* storage unavailable */
  }
  el.modes.addEventListener("click", (e) => {
    const b = e.target.closest("[data-mode]");
    if (b) setMode(b.dataset.mode);
  });
  el.preview.addEventListener("click", previewYaml);
  showMode();
  el.list = $("#class-list");

  el.list.addEventListener("click", (e) => {
    const sw = e.target.closest("[data-toggle]");
    if (sw) return toggleEnabled(sw.dataset.toggle, sw);
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
  el.msg.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-line]");
    if (!b) return;
    if (ws.mode === "form" && !(await setMode("yaml"))) return;
    goToLine(+b.dataset.line);
  });
  el.result.addEventListener("click", (e) => {
    if (e.target.closest("[data-open]") && ws.lastTest) ws.hooks.onOpenDocument(ws.lastTest);
  });
  $("#dict-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-dict]");
    if (b) openDictionary(b.dataset.dict);
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
  if (!ws.dicts) loadDictionaries();
  loadMeta().catch(() => {});
}

async function loadMeta() {
  if (!ws.meta) ws.meta = await api("/api/doc-types/meta");
  return ws.meta;
}

// ------------------------------------------------------------------ form ↔ YAML
function showMode() {
  const form = ws.mode === "form";
  const wizard = wizardOpen();
  const open = !!ws.current && !wizard;
  el.wizard.hidden = !wizard;
  el.placeholder.hidden = open || wizard;
  el.actions.hidden = !open;
  el.form.hidden = !open || !form || !ws.form;
  el.editor.hidden = !open || (form && !!ws.form);
  const yamlShown = !el.editor.hidden;
  el.mode_toggle.hidden = !open;
  el.mode_toggle.dataset.mode = yamlShown ? "form" : "yaml";
  el.mode_toggle.setAttribute("aria-pressed", String(yamlShown));
  el.mode_toggle.textContent = yamlShown ? "← Wróć do formularza" : "Tryb zaawansowany (YAML)";
  el.preview.hidden = !open || yamlShown;
}

/** Fill the form from the YAML text. false (and errors shown) if the YAML cannot be read as data. */
async function loadForm() {
  const [meta, parsed] = await Promise.all([loadMeta(), jsonPost("/api/doc-types/parse", { yaml: el.yaml.value })]);
  if (!parsed.data) {
    showReport(parsed);
    showMessage("err", "Nie można otworzyć formularza, bo plik konfiguracji zawiera błędy. Popraw je w trybie zaawansowanym:" + issueList(parsed.errors || []));
    return false;
  }
  const model = buildModel(parsed.data);
  const { json } = modelToJson(model);
  const losses = [...(parsed.losses || [])];
  if (canon(json) !== canon(parsed.data)) losses.push("część ustawień ma postać, której formularz nie odwzorowuje dokładnie");
  ws.form = { model, snapshot: JSON.stringify(json), losses };
  renderForm(el.form, model, { meta, losses, onChange: updateHeader, isNew: !ws.current?.id });
  return true;
}

/** Write form changes into the YAML text (only if the form was changed). false if the form has problems. */
async function syncForm() {
  if (ws.mode !== "form" || !ws.form) return true;
  const { json, problems } = formJson();
  if (problems.length) {
    showMessage("err", `<strong>Uzupełnij formularz:</strong>${issueList(problems.map((message) => ({ message })))}`);
    return false;
  }
  if (JSON.stringify(json) === ws.form.snapshot) return true; // unchanged → keep the YAML text as it is
  // base = the current YAML text: the server merges the changes into it, so comments in the file stay
  const res = await jsonPost("/api/doc-types/dump", { data: json, base: el.yaml.value });
  el.yaml.value = res.yaml;
  ws.form.snapshot = JSON.stringify(json);
  ws.form.dumped = true;
  updateGutter();
  return true;
}

async function setMode(mode) {
  if (mode === ws.mode && (mode === "yaml" || ws.form)) return true;
  try {
    if (mode === "yaml") {
      if (!(await syncForm())) return false;
    } else if (ws.current && !(await loadForm())) {
      return false;
    }
  } catch (e) {
    showMessage("err", esc(e.message));
    return false;
  }
  ws.mode = mode;
  try {
    localStorage.setItem("ws-mode", mode);
  } catch {
    /* storage unavailable */
  }
  showMode();
  updateHeader();
  return true;
}

async function previewYaml() {
  try {
    if (!(await syncForm())) return;
    el.preview_text.textContent = el.yaml.value;
    el.preview_dlg.showModal();
  } catch (e) {
    showMessage("err", esc(e.message));
  }
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
const SWITCH_HINT = "Wyłączony typ nie jest rozpoznawany — takie dokumenty dostają pusty plik JSON";

function renderList() {
  const readonly = !!ws.hooks.getHealth()?.readonly;
  el.list.innerHTML = ws.classes
    .map((c) => {
      const on = !!c.enabled;
      const control = !c.valid
        ? `<span class="status err">błąd w konfiguracji</span>`
        : `<button class="switch" type="button" role="switch" aria-checked="${on}" data-toggle="${esc(c.id)}"
            ${readonly ? "disabled" : ""} title="${readonly ? "Zmiany zostały wyłączone przez administratora" : SWITCH_HINT}"
            aria-label="${esc(c.name || c.id)}: ${on ? "rozpoznawany" : "wyłączony"}">
            <span class="switch-track" aria-hidden="true"><span class="switch-thumb"></span></span>
            <span class="switch-label">${on ? "Rozpoznawany" : "Wyłączony"}</span>
          </button>`;
      const desc = String(c.description || "").replace(/\s+/g, " ").trim();
      const active = ws.current?.id === c.id;
      return `<li class="${c.valid && !on ? "off" : ""}">
        <button class="class-item${active ? " active" : ""}" data-id="${esc(c.id)}" type="button" aria-current="${active}">
          <span class="ci-name">${esc(c.name || c.id)}</span>
          ${desc ? `<span class="ci-desc">${esc(desc)}</span>` : ""}
        </button>
        <div class="ci-switch">${control}</div>
      </li>`;
    })
    .join("");
  if (!ws.classes.length) el.list.innerHTML = `<li class="muted small">Nie ma jeszcze żadnego typu dokumentu.</li>`;
}

async function toggleEnabled(id, btn) {
  const c = ws.classes.find((x) => x.id === id);
  if (!c || ws.busy) return;
  if (ws.hooks.getHealth()?.readonly) return toast("Zmiany zostały wyłączone przez administratora");
  if (ws.current?.id === id && isDirty()) return toast("Najpierw zapisz albo cofnij zmiany w tym typie dokumentu", 3500);
  const enabled = !c.enabled;
  btn.disabled = true;
  try {
    const res = await jsonPut(`/api/doc-types/${encodeURIComponent(id)}/enabled`, { enabled });
    if (res && res.ok === false) throw new Error(res.detail || "Nie udało się zmienić ustawienia");
    await refreshClasses();
    await ws.hooks.onSaved();
    if (ws.current?.id === id) {
      // reload the editor with the file as it is now (only the enabled line changed)
      const text = await api(`/api/doc-types/${encodeURIComponent(id)}/yaml`);
      await setEditor(text, { ...ws.current });
    }
    toast(enabled ? `„${c.name}” jest znowu rozpoznawany` : `„${c.name}” jest wyłączony — takie dokumenty dostaną pusty plik JSON`, 4000);
    el.list.querySelector(`[data-toggle="${CSS.escape(id)}"]`)?.focus();
  } catch (e) {
    btn.disabled = false;
    toast(e.message, 5000);
  }
}

// ------------------------------------------------------------------ dictionaries (read-only viewer)
async function loadDictionaries() {
  const list = $("#dict-list");
  try {
    ws.dicts = await api("/api/dictionaries");
  } catch (e) {
    list.innerHTML = `<li class="err-text small">${esc(e.message)}</li>`;
    return;
  }
  list.innerHTML = ws.dicts.length
    ? ws.dicts
        .map(
          (d) => `<li><button type="button" class="dict-item" data-dict="${esc(d.id)}">
            <span>${esc(humanizeKey(d.id))}</span><span class="muted small">${d.entries.length} ${plural(d.entries.length, ["pozycja", "pozycje", "pozycji"])}</span>
          </button></li>`,
        )
        .join("")
    : `<li class="muted small">Brak słowników.</li>`;
}

/** Regex keyword → readable phrase(s): 'odrzuceni\w*\s+(?:\w+\s+){0,2}ofert\w*' → "odrzuceni… ofert…". */
export function humanizePattern(p) {
  const top = [];
  let depth = 0;
  let cur = "";
  for (let i = 0; i < p.length; i++) {
    const c = p[i];
    if (c === "\\") {
      cur += c + (p[i + 1] ?? "");
      i++;
      continue;
    }
    if (c === "(" || c === "[") depth++;
    if (c === ")" || c === "]") depth--;
    if (c === "|" && depth === 0) {
      top.push(cur);
      cur = "";
    } else cur += c;
  }
  top.push(cur);
  return top.map((alt) =>
    alt
      .replace(/\(\?:\\w\+\\s\+\)\{\d*,?\d*\}/g, " … ")
      .replace(/\\[wW][*+]?(\{\d*,?\d*\})?/g, "…")
      .replace(/\\s[*+?]?/g, " ")
      .replace(/\\b/g, "")
      .replace(/\[([^\]\\])[^\]]*\]/g, "$1")
      .replace(/\(\?:/g, "(")
      .replace(/\\(.)/g, "$1")
      .replace(/[?*+^$]/g, "")
      .replace(/\(([^()]*)\)/g, (_, inner) => (inner.includes("|") ? `(${inner.split("|").map((x) => x.trim()).join(" / ")})` : inner))
      .replace(/\{\d*,?\d*\}/g, "")
      .replace(/…(\s*…)+/g, "…")
      .replace(/\s+/g, " ")
      .trim(),
  );
}

function openDictionary(id) {
  const d = ws.dicts?.find((x) => x.id === id);
  if (!d) return;
  const used = d.used_by
    .map((u) => `${u.fields.map((f) => `„${esc(fieldLabel(f.replace(/\[\]/g, "")).label)}”`).join(", ")} w typie „${esc(u.name)}”`)
    .join("; ");
  const rows = d.entries
    .map(
      (e) => `<tr>
        <td class="num">${esc(e.id)}</td>
        <td><strong>${esc(e.opis)}</strong>${e.hint ? `<div class="muted small">${esc(e.hint)}</div>` : ""}</td>
        <td>${e.keywords.length ? `<ul class="kw">${e.keywords.flatMap((k) => humanizePattern(k).map((h) => `<li title="${esc(k)}">${esc(h)}</li>`)).join("")}</ul>` : '<span class="muted">—</span>'}</td>
      </tr>`,
    )
    .join("");
  $("#dict-title").textContent = `Słownik: ${humanizeKey(d.id)}`;
  $("#dict-body").innerHTML = `
    ${d.description ? `<p>${esc(d.description)}</p>` : ""}
    ${used ? `<p class="muted">Na tej podstawie system ustala wartość pola ${used} oraz jego opis.</p>` : ""}
    <p class="dict-note">Słownik jest tylko do podglądu. Zmiany wprowadza informatyk w pliku słownika.</p>
    <table class="dict-table">
      <thead><tr><th scope="col">Nr</th><th scope="col">Opis</th><th scope="col">Słowa kluczowe</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $("#dict-dlg").showModal();
}

function confirmDiscard() {
  return !isDirty() || confirm("Masz niezapisane zmiany. Czy na pewno chcesz je porzucić?");
}

async function openClass(id) {
  if ((ws.current?.id === id && !wizardOpen()) || !confirmDiscard()) return;
  closeWizard();
  try {
    const text = await api(`/api/doc-types/${encodeURIComponent(id)}/yaml`);
    const c = ws.classes.find((x) => x.id === id);
    await setEditor(text, { id, file: c?.file, name: c?.name || id });
  } catch (e) {
    showMessage("err", esc(e.message));
  }
}

async function newClass() {
  if (!confirmDiscard()) return;
  ws.current = null;
  ws.form = null;
  el.msg.innerHTML = "";
  el.result.innerHTML = "";
  el.title.textContent = "Nowy typ dokumentu";
  el.file.hidden = false;
  el.file.textContent = "Trzy proste kroki. Szczegóły (wagi, reguły) można ustawić później.";
  el.dirty.hidden = true;
  openWizard(el.wizard, {
    jsonPost,
    getHealth: ws.hooks.getHealth,
    onCancel: () => {
      if (wizardDirty() && !confirm("Porzucić tworzenie nowego typu dokumentu?")) return;
      closeWizard();
      el.title.textContent = "Wybierz typ dokumentu z listy";
      el.file.textContent = "";
      el.msg.innerHTML = "";
      el.result.innerHTML = "";
      showMode();
    },
    onFullForm: async (yaml) => {
      closeWizard();
      if (ws.mode !== "form") ws.mode = "form";
      await setEditor(yaml, { id: null, file: null, name: null });
      ws.current.original = ""; // nothing saved yet → keep the "unsaved" state
      updateHeader();
    },
    onTest: async (file, yaml) => {
      el.yaml.value = yaml;
      await test(file, { fromWizard: true });
    },
    onSave: saveFromWizard,
  });
  renderList();
  showMode();
}

async function saveFromWizard(yaml) {
  if (ws.hooks.getHealth()?.readonly) return toast("Zapisywanie zostało wyłączone przez administratora");
  el.yaml.value = yaml;
  const rep = await validate();
  if (!rep.ok) return showReport(rep);
  if (ws.classes.some((c) => c.id === rep.id) && !confirm(`Typ dokumentu o identyfikatorze „${rep.id}” już istnieje. Zastąpić go?`)) return;
  showMessage("info", '<span class="spinner" aria-hidden="true"></span> Zapisuję…');
  const res = await api(`/api/doc-types/${encodeURIComponent(rep.id)}/yaml`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ yaml }),
  });
  closeWizard();
  await refreshClasses();
  await ws.hooks.onSaved();
  await setEditor(yaml, { id: rep.id, file: res.file, name: rep.name });
  showReport(
    { errors: [], warnings: res.warnings },
    `✓ Zapisano nowy typ dokumentu „${esc(rep.name)}”. Kolejne dokumenty będą już rozpoznawane. Szczegóły możesz zmienić poniżej.`,
  );
  toast(`Zapisano nowy typ dokumentu „${rep.name}”`, 4000);
}

/** Template of a new type in plain wording: importance levels Wysoka/Niska instead of odd weights, no example phrases. */
function friendlyTemplate(tpl) {
  const t = JSON.parse(JSON.stringify(tpl));
  t.name = "Nowy typ dokumentu";
  t.id = "nowy_typ_dokumentu";
  delete t.description;
  const c = t.classification || (t.classification = {});
  c.min_score = 4;
  c.signals = [{ pattern: "tytuł dokumentu", weight: 4, scope: "head" }];
  return t;
}

async function setEditor(text, cur) {
  ws.current = { ...cur, original: text };
  ws.form = null;
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
  if (ws.mode === "form") {
    try {
      if (!(await loadForm())) {
        ws.mode = "yaml"; // unreadable YAML → fix it in the text editor
      }
    } catch (e) {
      ws.mode = "yaml";
      showMessage("err", esc(e.message));
    }
  }
  showMode();
  updateHeader();
}

function updateHeader() {
  const cur = ws.current;
  if (!cur) return;
  el.title.textContent = cur.id ? cur.name || cur.id : "Nowy typ dokumentu";
  el.file.textContent = cur.id ? "" : "Jeszcze niezapisany — uzupełnij formularz i kliknij „Zapisz”.";
  el.file.hidden = !!cur.id;
  const dirty = isDirty();
  el.dirty.hidden = !dirty;
  const readonly = !!ws.hooks.getHealth()?.readonly;
  el.check.disabled = el.test.disabled = el.preview.disabled = ws.busy;
  el.save.disabled = ws.busy || readonly;
  el.save.title = readonly ? "Zapisywanie zostało wyłączone przez administratora" : "Zapisz typ dokumentu (Ctrl/⌘+S)";
  el.readonly_note.hidden = !readonly;
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
        `<li>${esc(it.message)}${it.line ? ` <button class="line-link" type="button" data-line="${it.line}" title="Pokaż w trybie zaawansowanym (YAML), linia ${it.line}">pokaż w YAML</button>` : ""}</li>`,
    )
    .join("")}</ul>`;
}

function showReport(rep, okText) {
  ws.errorLines = new Set((rep.errors || []).map((e) => e.line).filter(Boolean));
  updateGutter();
  let html = "";
  if (rep.errors?.length) {
    html += `<div class="alert err" role="alert"><strong>${rep.errors.length === 1 ? "Trzeba poprawić 1 rzecz" : `Trzeba poprawić kilka rzeczy (${rep.errors.length})`}</strong>${issueList(rep.errors)}</div>`;
  } else if (okText) {
    html += `<div class="alert ok">${okText}</div>`;
  }
  if (rep.warnings?.length) html += `<div class="alert warn"><strong>Warto sprawdzić</strong>${issueList(rep.warnings)}</div>`;
  el.msg.innerHTML = html;
}

function summaryText(rep) {
  const s = rep.summary || {};
  const f = s.fields ?? 0;
  const g = s.signals ?? 0;
  return `✓ Wszystko w porządku. Typ „${esc(rep.name)}” odczytuje ${f} ${plural(f, ["pole", "pola", "pól"])} i ma ${g} ${plural(g, ["frazę", "frazy", "fraz"])} rozpoznawania.`;
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
  setBusy(true, "Sprawdzam…");
  try {
    if (!(await syncForm())) return;
    const rep = await validate();
    showReport(rep, summaryText(rep));
  } catch (e) {
    showMessage("err", esc(e.message));
  } finally {
    setBusy(false);
  }
}

async function test(file, { fromWizard = false } = {}) {
  if ((!ws.current && !fromWizard) || ws.busy) return;
  try {
    if (!fromWizard && !(await syncForm())) return;
  } catch (e) {
    return showMessage("err", esc(e.message));
  }
  setBusy(true, `Testuję na pliku ${file.name} — zmiany nie są zapisywane…`);
  el.result.innerHTML = "";
  const body = new FormData();
  body.append("yaml", el.yaml.value);
  body.append("file", file, file.name);
  try {
    const p = await api("/api/doc-types/test", { method: "POST", body });
    ws.lastTest = p;
    ws.errorLines = new Set();
    updateGutter();
    el.msg.innerHTML = p.test.warnings?.length ? `<div class="alert warn"><strong>Warto sprawdzić</strong>${issueList(p.test.warnings)}</div>` : "";
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
  const names = Object.fromEntries(ws.classes.map((c) => [c.id, c.name]));
  names[t.doc_type] = t.name;
  const verdict = t.recognised
    ? `<p class="verdict ok-text">✓ Dokument został rozpoznany jako „${esc(t.name)}”.</p>`
    : `<p class="verdict err-text">✗ Dokument nie został rozpoznany jako „${esc(t.name)}”. ${
        p.doc_type ? `System uznał go za: „${esc(p.doc_type_name || p.doc_type)}”.` : "Nie pasuje do żadnego typu."
      }</p>`;
  const rows = Object.entries(t.min_score)
    .sort((a, b) => (p.scores[b[0]] ?? 0) - (p.scores[a[0]] ?? 0))
    .map(([id, min]) => {
      const score = p.scores[id] ?? 0;
      return `<tr class="${id === t.doc_type ? "tested" : ""}"><td>${esc(names[id] || id)}</td><td>${score}</td><td>${min}</td>
        <td>${score >= min ? '<span class="ok-text">wystarczająco</span>' : '<span class="muted">za mało</span>'}</td></tr>`;
    })
    .join("");
  const fields = (p.fields || [])
    .map((f) => {
      const { group, label } = fieldLabel(f.path);
      return `<tr><td>${group ? `<span class="muted">${esc(group)} › </span>` : ""}${esc(label)}</td>
        <td class="v">${f.value === null ? '<span class="muted">nie znaleziono</span>' : esc(trunc(fmtValue(f.value), 160))}</td>
        <td>${f.value !== null && f.method ? confWord(f.confidence) : ""}</td></tr>`;
    })
    .join("");
  el.result.innerHTML = `<section class="test-card" aria-labelledby="test-title">
      <h3 id="test-title">Wynik testu: ${esc(p.filename)}</h3>
      <p class="muted small">Test nie zapisuje żadnych zmian.</p>
      ${verdict}
      <details class="tech"><summary>Punkty rozpoznawania</summary>
        <table class="score-table"><thead><tr><th scope="col">Typ dokumentu</th><th scope="col">Punkty</th><th scope="col">Wymagane</th><th scope="col"></th></tr></thead><tbody>${rows}</tbody></table>
      </details>
      ${p.doc_type ? `<table class="mini-table"><thead><tr><th scope="col">Pole</th><th scope="col">Odczytana wartość</th><th scope="col">Pewność</th></tr></thead><tbody>${fields}</tbody></table>` : ""}
      <div><button class="btn" type="button" data-open>Pokaż na podglądzie dokumentu</button></div>
    </section>`;
}

async function save() {
  if (!ws.current || ws.busy) return;
  if (ws.hooks.getHealth()?.readonly) return toast("Zapisywanie zostało wyłączone przez administratora");
  if (formChanged() && ws.form.losses.length) {
    const q = `Zapis z formularza usunie z pliku konfiguracji: ${ws.form.losses.join("; ")}.\n\nAby to zachować, anuluj i skorzystaj z trybu zaawansowanego (YAML). Zapisać mimo to?`;
    if (!confirm(q)) return;
  }
  setBusy(true, "Sprawdzam przed zapisem…");
  try {
    if (!(await syncForm())) return;
    const rep = await validate();
    if (!rep.ok) {
      showReport(rep);
      return;
    }
    const id = rep.id;
    const cur = ws.current;
    const question =
      cur.id && id !== cur.id
        ? `Zmieniono identyfikator z „${cur.id}” na „${id}”.\nZapisać jako NOWY typ dokumentu? Typ „${cur.id}” pozostanie bez zmian.`
        : !cur.id && ws.classes.some((c) => c.id === id)
          ? `Typ dokumentu o identyfikatorze „${id}” już istnieje. Zastąpić go?`
          : null;
    if (question && !confirm(question)) {
      el.msg.innerHTML = "";
      return;
    }
    setBusy(true, "Zapisuję…");
    const text = el.yaml.value;
    const res = await api(`/api/doc-types/${encodeURIComponent(id)}/yaml`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ yaml: text }),
    });
    ws.current = { id, file: res.file, name: rep.name, original: text };
    if (ws.form?.dumped) ws.form.losses = []; // the file now is what the form wrote — nothing more to lose
    await refreshClasses();
    await ws.hooks.onSaved();
    showReport(
      { errors: [], warnings: res.warnings },
      `✓ Zapisano typ dokumentu „${esc(rep.name)}”${res.created ? " (nowy)" : ""}.${
        res.active ? " Kolejne dokumenty będą już rozpoznawane z tymi ustawieniami." : " Typ jest nieaktywny — nie będzie używany, dopóki go nie włączysz."
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

async function revert() {
  if (!ws.current || !isDirty()) return;
  if (!confirm("Cofnąć wszystkie niezapisane zmiany?")) return;
  el.yaml.value = ws.current.original;
  ws.errorLines = new Set();
  updateGutter();
  el.msg.innerHTML = "";
  if (ws.mode === "form") await loadForm().catch((e) => showMessage("err", esc(e.message)));
  updateHeader();
}
