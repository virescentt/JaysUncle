// Kreator nowego typu dokumentu (3 kroki) — zakładka "Typy dokumentów".
// 1. Nazwa  2. Jak rozpoznać (frazy, opcjonalnie z przykładowego PDF)  3. Jakie dane odczytać (pola, tryb llm).
// Wynik: dane klasy → /api/doc-types/dump → YAML, który przechodzi zwykłe validate / test / zapis.
// Pełny formularz (wagi, wzorce, reguły) jest dostępny potem jako "Edytuj szczegóły".

import { esc, api } from "./util.js";
import { reEscape, slugify } from "./classform.js";

const KINDS = [
  ["string", "Tekst"],
  ["date", "Data"],
  ["amount", "Kwota"],
  ["number", "Liczba"],
  ["boolean", "Tak / nie"],
  ["array", "Lista"],
];
const STEPS = ["Nazwa", "Jak rozpoznać", "Jakie dane odczytać"];
const W_HIGH = 4;
const W_MID = 2;
const MIN_SCORE = 4;

const blankField = () => ({ label: "", description: "", kind: "string" });

let wz = null; // state of the open wizard
let root = null;
let hooks = null; // {jsonPost, getHealth, onTest(file, yaml), onSave(yaml, data), onFullForm(yaml), onCancel()}

export const wizardOpen = () => !!wz;
export const wizardDirty = () =>
  !!wz && !!(wz.name.trim() || wz.description.trim() || wz.phrases.some((p) => p.trim()) || wz.fields.some((f) => f.label.trim()));

export function openWizard(rootEl, h) {
  root = rootEl;
  hooks = h;
  wz = { step: 0, maxStep: 0, name: "", description: "", phrases: [], lines: [], sample: null, loading: false, error: "", fields: [blankField()] };
  render();
  root.querySelector("#wz-name")?.focus();
}

export function closeWizard() {
  wz = null;
  if (root) root.replaceChildren();
}

// ------------------------------------------------------------------ data → class
function fieldKeys() {
  const used = new Set();
  return wz.fields.map((f) => {
    let key = slugify(f.label) || "";
    if (/^\d/.test(key)) key = `pole_${key}`;
    if (!key) return "";
    let k = key;
    for (let n = 2; used.has(k); n++) k = `${key}_${n}`;
    used.add(k);
    return k;
  });
}

function classData() {
  const id = slugify(wz.name) || "nowy_typ";
  const phrases = wz.phrases.map((p) => p.trim()).filter(Boolean);
  const signals = phrases.map((p, i) =>
    i === 0 ? { pattern: reEscape(p), weight: W_HIGH, scope: "head" } : { pattern: reEscape(p), weight: W_MID },
  );
  const keys = fieldKeys();
  const output = {};
  wz.fields.forEach((f, i) => {
    if (!keys[i]) return;
    const desc = f.description.trim() || f.label.trim();
    if (f.kind === "array") output[keys[i]] = { type: "array", description: desc, items: { type: "string" } };
    else if (f.kind === "amount") output[keys[i]] = { type: "number", description: `${desc} (kwota jako liczba, np. 1500.00)` };
    else output[keys[i]] = { type: f.kind, description: desc };
  });
  const data = { id: /^[a-z]/.test(id) ? id : `typ_${id}`.slice(0, 64), name: wz.name.trim(), version: 1, enabled: true };
  if (wz.description.trim()) data.description = wz.description.trim();
  data.classification = { min_score: MIN_SCORE, signals, negative_signals: [] };
  data.extraction = {
    mode: "llm",
    instructions: "Przepisuj wartości dokładnie tak, jak w dokumencie; nie zgaduj — brak danych → null.\n",
  };
  data.output = output;
  return data;
}

async function toYaml() {
  return (await hooks.jsonPost("/api/doc-types/dump", { data: classData() })).yaml;
}

// ------------------------------------------------------------------ step checks
function problems(step) {
  const out = [];
  if (step === 0) {
    if (!wz.name.trim()) out.push("Wpisz nazwę typu dokumentu.");
    else if (!slugify(wz.name)) out.push("Nazwa musi zawierać litery lub cyfry.");
  }
  if (step === 1 && !wz.phrases.some((p) => p.trim())) out.push("Dodaj co najmniej jedną frazę — najlepiej tytuł dokumentu.");
  if (step === 2) {
    const named = wz.fields.filter((f) => f.label.trim());
    if (!named.length) out.push("Dodaj co najmniej jedno pole do odczytania.");
    wz.fields.forEach((f, i) => {
      if (!f.label.trim() && (f.description.trim() || wz.fields.length > 1)) out.push(`Pole nr ${i + 1}: wpisz nazwę albo usuń wiersz.`);
    });
  }
  return out;
}

function go(step) {
  for (let s = wz.step; s < step; s++) {
    const p = problems(s);
    if (p.length) {
      wz.step = s;
      wz.error = p.join(" ");
      render();
      return;
    }
  }
  wz.error = "";
  wz.step = step;
  wz.maxStep = Math.max(wz.maxStep, step);
  render();
  root.querySelector(".wz-body h3")?.focus();
}

// ------------------------------------------------------------------ sample PDF → first lines
async function loadSample(file) {
  wz.sample = file;
  wz.loading = true;
  wz.lines = [];
  wz.error = "";
  render();
  const body = new FormData();
  body.append("files", file, file.name);
  try {
    const [p] = await api("/api/process", { method: "POST", body });
    wz.lines = (p.head_lines || []).slice(0, 15);
    if (!wz.lines.length) wz.error = "Nie udało się odczytać tekstu z tego pliku — wpisz frazy ręcznie.";
    if (p.id) api(`/api/doc/${p.id}`, { method: "DELETE" }).catch(() => {});
  } catch (e) {
    wz.error = e.message;
  } finally {
    wz.loading = false;
    if (wz) render();
  }
}

function addPhrase(text, focus = false) {
  const t = text.trim();
  if (wz.phrases.some((p) => p.trim().toLowerCase() === t.toLowerCase() && t)) return;
  const empty = wz.phrases.findIndex((p) => !p.trim());
  if (t && empty >= 0) wz.phrases[empty] = t;
  else wz.phrases.push(t);
  wz.error = "";
  render();
  if (focus) root.querySelector(".wz-phrases li:last-child input")?.focus();
}

// ------------------------------------------------------------------ rendering
function input(id, value, attrs = "") {
  return `<input id="${id}" value="${esc(value)}" ${attrs}>`;
}

function stepName() {
  const id = slugify(wz.name);
  return `
    <h3 tabindex="-1">Jak nazywa się ten dokument?</h3>
    <p class="wz-hint">Nazwa będzie widoczna na liście dokumentów, np. „Umowa najmu” albo „Zaświadczenie o niekaralności”.</p>
    <div class="cf-lbl"><label for="wz-name">Nazwa typu dokumentu</label>${input("wz-name", wz.name, 'class="cf-big" autocomplete="off" placeholder="np. Umowa najmu" data-k="name"')}
      <small class="cf-help" id="wz-id">${id ? `Identyfikator techniczny (tworzony automatycznie): <code>${esc(id)}</code>` : "&nbsp;"}</small></div>
    <div class="cf-lbl"><label for="wz-desc">Krótki opis <span class="muted">(opcjonalnie)</span></label>
      <textarea id="wz-desc" rows="2" data-k="description" placeholder="Czym jest dokument i kto go wystawia.">${esc(wz.description)}</textarea></div>`;
}

function stepRecognise() {
  const added = new Set(wz.phrases.map((p) => p.trim().toLowerCase()));
  const lines = wz.loading
    ? `<p class="muted"><span class="spinner" aria-hidden="true"></span> Odczytuję plik ${esc(wz.sample?.name || "")}…</p>`
    : wz.lines.length
      ? `<p class="wz-hint">Kliknij wiersz, aby dodać go jako frazę. Potem możesz go skrócić.</p>
         <ol class="wz-lines">${wz.lines
           .map((l) => {
             const on = added.has(l.toLowerCase());
             return `<li><button type="button" class="wz-line${on ? " on" : ""}" data-line="${esc(l)}" aria-pressed="${on}">${esc(l)}</button></li>`;
           })
           .join("")}</ol>`
      : "";
  const rows = wz.phrases
    .map(
      (p, i) => `<li>
        <label class="sr-only" for="wz-ph-${i}">Fraza nr ${i + 1}</label>
        <input id="wz-ph-${i}" value="${esc(p)}" data-phrase="${i}" placeholder="${i === 0 ? "np. UMOWA NAJMU" : "np. wynajmujący"}" autocomplete="off">
        ${i === 0 ? '<span class="wz-tag">najważniejsza — zwykle tytuł</span>' : ""}
        <button type="button" class="btn small ghost" data-del-phrase="${i}" aria-label="Usuń frazę nr ${i + 1}">Usuń</button>
      </li>`,
    )
    .join("");
  return `
    <h3 tabindex="-1">Po czym rozpoznać ten dokument?</h3>
    <p class="wz-hint">Podaj zwroty, które zawsze pojawiają się w tym dokumencie — najlepiej jego tytuł. Najłatwiej: wgraj przykładowy plik i kliknij właściwe wiersze.</p>
    <div class="wz-sample">
      <button type="button" class="btn" data-act="sample">${wz.sample ? "Wybierz inny plik PDF…" : "Wgraj przykładowy PDF…"}</button>
      <span class="muted small">${wz.sample ? `Plik: ${esc(wz.sample.name)}` : "Opcjonalnie — plik nie jest nigdzie zapisywany."}</span>
      <input type="file" id="wz-file" accept=".pdf,application/pdf" hidden>
    </div>
    ${lines}
    <h4>Frazy rozpoznawania</h4>
    ${rows ? `<ul class="wz-phrases">${rows}</ul>` : '<p class="muted small">Nie dodano jeszcze żadnej frazy.</p>'}
    <div><button type="button" class="btn small" data-act="add-phrase">+ Wpisz frazę ręcznie</button></div>`;
}

function stepFields() {
  const keys = fieldKeys();
  const h = hooks.getHealth();
  const llm = h?.llm_model && h.llm_status !== "unavailable";
  const rows = wz.fields
    .map(
      (f, i) => `<li class="wz-field">
        <div class="cf-lbl"><label for="wz-fl-${i}">Nazwa pola (po ludzku)</label>
          <input id="wz-fl-${i}" value="${esc(f.label)}" data-field="${i}" data-k="label" placeholder="np. Data zawarcia umowy" autocomplete="off">
          <small class="cf-help">${keys[i] ? `W wyniku: <code>${esc(keys[i])}</code>` : "&nbsp;"}</small></div>
        <div class="cf-lbl"><label for="wz-fk-${i}">Rodzaj</label>
          <select id="wz-fk-${i}" data-field="${i}" data-k="kind">${KINDS.map(([v, l]) => `<option value="${v}"${v === f.kind ? " selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="cf-lbl wz-field-desc"><label for="wz-fd-${i}">Opis — jak koledze z pracy</label>
          <input id="wz-fd-${i}" value="${esc(f.description)}" data-field="${i}" data-k="description" placeholder="np. Dzień, w którym strony podpisały umowę." autocomplete="off"></div>
        <button type="button" class="btn small ghost wz-del" data-del-field="${i}" aria-label="Usuń pole nr ${i + 1}" ${wz.fields.length === 1 ? "disabled" : ""}>Usuń</button>
      </li>`,
    )
    .join("");
  return `
    <h3 tabindex="-1">Jakie dane odczytać z dokumentu?</h3>
    <p class="wz-hint">Dane z nowego typu odczytuje <strong>model językowy</strong> na podstawie nazw i opisów pól — dlatego opisz każde pole zwykłymi słowami. Model konfiguruje informatyk (plik <code>.env</code>).</p>
    ${llm ? "" : '<div class="alert warn">Model językowy nie jest teraz skonfigurowany — dokument zostanie rozpoznany, ale pola pozostaną puste, dopóki informatyk go nie włączy.</div>'}
    <ul class="wz-fields">${rows}</ul>
    <div><button type="button" class="btn small" data-act="add-field">+ Dodaj pole</button></div>`;
}

function render() {
  if (!wz || !root) return;
  const steps = STEPS.map((name, i) => {
    const cur = i === wz.step;
    const reachable = i <= wz.maxStep;
    return `<li><button type="button" class="wz-step${cur ? " current" : ""}${i < wz.step ? " done" : ""}" data-step="${i}"
      ${cur ? 'aria-current="step"' : ""} ${reachable ? "" : "disabled"}><span class="wz-n">${i + 1}</span>${name}</button></li>`;
  }).join("");
  const body = [stepName, stepRecognise, stepFields][wz.step]();
  const last = wz.step === STEPS.length - 1;
  root.innerHTML = `
    <nav class="wz-steps" aria-label="Kroki kreatora"><ol>${steps}</ol></nav>
    <section class="wz-body cf-sec" aria-label="Krok ${wz.step + 1} z ${STEPS.length}: ${STEPS[wz.step]}">${body}
      ${wz.error ? `<div class="alert err" role="alert">${esc(wz.error)}</div>` : ""}
    </section>
    <div class="wz-nav">
      <button type="button" class="btn" data-act="back" ${wz.step === 0 ? "hidden" : ""}>← Wstecz</button>
      <button type="button" class="link-btn subtle" data-act="cancel">Anuluj</button>
      <span class="spacer"></span>
      <button type="button" class="link-btn subtle" data-act="full">Edytuj szczegóły (pełny formularz)</button>
      ${last && wz.sample ? '<button type="button" class="btn" data-act="test">Sprawdź na pliku</button>' : ""}
      ${last ? `<button type="button" class="btn primary" data-act="save" ${hooks.getHealth()?.readonly ? 'disabled title="Zapisywanie zostało wyłączone przez administratora"' : ""}>Zapisz</button>` : '<button type="button" class="btn primary" data-act="next">Dalej →</button>'}
    </div>`;
  bind();
}

function bind() {
  root.oninput = (e) => {
    const t = e.target;
    if (t.dataset.phrase !== undefined) wz.phrases[+t.dataset.phrase] = t.value;
    else if (t.dataset.field !== undefined) {
      wz.fields[+t.dataset.field][t.dataset.k] = t.value;
      if (t.dataset.k === "label") {
        const keys = fieldKeys();
        root.querySelectorAll(".wz-field").forEach((li, i) => {
          li.querySelector(".cf-help").innerHTML = keys[i] ? `W wyniku: <code>${esc(keys[i])}</code>` : "&nbsp;";
        });
      }
    } else if (t.dataset.k) {
      wz[t.dataset.k] = t.value;
      if (t.dataset.k === "name") {
        const id = slugify(t.value);
        root.querySelector("#wz-id").innerHTML = id ? `Identyfikator techniczny (tworzony automatycznie): <code>${esc(id)}</code>` : "&nbsp;";
      }
    }
  };
  root.onchange = (e) => {
    const t = e.target;
    if (t.id === "wz-file" && t.files[0]) loadSample(t.files[0]);
    else if (t.tagName === "SELECT" && t.dataset.field !== undefined) wz.fields[+t.dataset.field].kind = t.value;
  };
  root.onkeydown = (e) => {
    if (e.key === "Enter" && e.target.tagName === "INPUT") {
      e.preventDefault();
      if (e.target.dataset.phrase !== undefined) addPhrase("", true);
      else if (e.target.dataset.field !== undefined) root.querySelector('[data-act="add-field"]').click();
      else root.querySelector('[data-act="next"]')?.click();
    }
  };
  root.onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    if (b.dataset.step !== undefined) return go(+b.dataset.step);
    if (b.dataset.line !== undefined) {
      const l = b.dataset.line;
      const i = wz.phrases.findIndex((p) => p.trim().toLowerCase() === l.toLowerCase());
      if (i >= 0) wz.phrases.splice(i, 1);
      else addPhrase(l);
      return render();
    }
    if (b.dataset.delPhrase !== undefined) {
      wz.phrases.splice(+b.dataset.delPhrase, 1);
      return render();
    }
    if (b.dataset.delField !== undefined) {
      wz.fields.splice(+b.dataset.delField, 1);
      return render();
    }
    switch (b.dataset.act) {
      case "next":
        return go(wz.step + 1);
      case "back":
        wz.error = "";
        wz.step -= 1;
        return render();
      case "sample":
        return root.querySelector("#wz-file").click();
      case "add-phrase":
        return addPhrase("", true);
      case "add-field":
        wz.fields.push(blankField());
        render();
        return root.querySelector(".wz-field:last-child input")?.focus();
      case "cancel":
        return hooks.onCancel();
      case "full":
      case "test":
      case "save": {
        const upTo = b.dataset.act === "full" ? 0 : STEPS.length;
        for (let s = 0; s < upTo; s++) {
          const p = problems(s);
          if (p.length) {
            wz.step = s;
            wz.error = p.join(" ");
            return render();
          }
        }
        if (b.dataset.act === "full" && !wz.name.trim()) wz.name = "Nowy typ dokumentu";
        try {
          b.disabled = true;
          const yaml = await toYaml();
          if (b.dataset.act === "full") return hooks.onFullForm(yaml);
          if (b.dataset.act === "test") return await hooks.onTest(wz.sample, yaml);
          return await hooks.onSave(yaml, classData());
        } catch (err) {
          wz.error = err.message;
          render();
        } finally {
          if (b.isConnected) b.disabled = false;
        }
      }
    }
  };
}
