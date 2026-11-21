// Formularz zakładki "Typy dokumentów": przyjazna edycja typu (klasy) dokumentu bez znajomości YAML.
// Formularz pracuje na danych klasy (to, co daje yaml.safe_load) — model → JSON → /api/doc-types/dump → YAML.
// Klucze, których formularz nie obsługuje (strategy, params, section, enum, …), są przechowywane i oddawane bez zmian,
// kolejność kluczy jest zachowana.

import { humanizeKey } from "./util.js";

const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

/** JSON with sorted keys — comparison that ignores key order. */
export function canon(v) {
  if (Array.isArray(v)) return `[${v.map(canon).join(",")}]`;
  if (isObj(v))
    return `{${Object.keys(v)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canon(v[k])}`)
      .join(",")}}`;
  return JSON.stringify(v);
}

// ------------------------------------------------------------------ literal text ↔ regex
// Znaki specjalne wyrażeń regularnych (bez trybu verbose). Python re.escape dodatkowo poprzedza "\" spację, "#", "&", "~"
// — taki zapis też rozpoznajemy jako zwykły tekst.
const SPECIAL = /[.^$*+?{}[\]\\|()]/g;
const LITERAL_RE = /^(?:[^.^$*+?{}[\]\\|()]|\\[^A-Za-z0-9])*$/;
export const reEscape = (s) => s.replace(SPECIAL, "\\$&");
const isLiteral = (p) => LITERAL_RE.test(p);
const unescape = (p) => p.replace(/\\([^A-Za-z0-9])/g, "$1");

/** Put `v` under key `k` unless it equals the default — then keep the original value if it was the same, else drop. */
function put(out, orig, k, v, dflt) {
  if (!same(v, dflt)) out[k] = v;
  else if (k in orig && same(orig[k], v)) out[k] = orig[k];
  else delete out[k];
}

// ------------------------------------------------------------------ model
function sigModel(s) {
  const orig = isObj(s) ? s : { pattern: String(s ?? "") };
  const p = String(orig.pattern ?? "");
  const literal = isLiteral(p);
  return {
    orig,
    text: literal ? unescape(p) : p,
    isRegex: !literal,
    adv: !literal, // loaded as a programmer regex → shown read-only in the "advanced" group
    weight: orig.weight ?? 1,
    head: orig.scope === "head",
    cs: !!orig.case_sensitive,
  };
}

function fieldModel(key, f, open) {
  const orig = isObj(f) ? f : {};
  const valKey = "validate" in orig || !("validators" in orig) ? "validate" : "validators";
  const list = (v) => (Array.isArray(v) ? v.map(String) : []);
  const n = {
    orig,
    key,
    open,
    type: orig.type ?? "string",
    description: orig.description ?? "",
    labels: list(orig.labels),
    patterns: list(orig.patterns),
    normalize: list(orig.normalize),
    validators: list(orig[valKey]),
    valKey,
    ground: orig.ground ?? "auto",
    dictionary: orig.dictionary ?? null,
    children: Object.entries(isObj(orig.properties) ? orig.properties : {}).map(([k, c]) => fieldModel(k, c)),
    items: orig.type === "array" ? fieldModel(null, orig.items ?? {}) : null,
  };
  return n;
}

export function buildModel(data) {
  const orig = isObj(data) ? data : {};
  const cls = isObj(orig.classification) ? orig.classification : {};
  const ext = isObj(orig.extraction) ? orig.extraction : {};
  return {
    orig,
    id: String(orig.id ?? ""),
    name: String(orig.name ?? ""),
    description: orig.description ?? "",
    enabled: orig.enabled ?? true,
    min_score: cls.min_score ?? 3,
    signals: (Array.isArray(cls.signals) ? cls.signals : []).map(sigModel),
    negative: (Array.isArray(cls.negative_signals) ? cls.negative_signals : []).map(sigModel),
    mode: ext.mode ?? "",
    instructions: ext.instructions ?? "",
    fields: Object.entries(isObj(orig.output) ? orig.output : {}).map(([k, f]) => fieldModel(k, f, true)),
  };
}

export const newField = (key = "") => ({ ...fieldModel(key, { type: "string" }, true), isNew: true });
export const newSignal = () => ({ orig: {}, text: "", isRegex: false, adv: false, weight: 2, head: false, cs: false });

/** "Umowa najmu lokalu" → "umowa_najmu_lokalu" (technical id of a new document type). */
export function slugify(name) {
  const map = { ą: "a", ć: "c", ę: "e", ł: "l", ń: "n", ó: "o", ś: "s", ź: "z", ż: "z" };
  return String(name || "")
    .toLowerCase()
    .replace(/[ąćęłńóśźż]/g, (c) => map[c])
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
}

// ------------------------------------------------------------------ model → JSON
function sigJson(s, where, problems) {
  const out = clone(s.orig);
  if (!s.text.trim()) problems.push(`${where}: pusta fraza — wpisz tekst albo usuń wiersz`);
  const o = String(s.orig.pattern ?? "");
  out.pattern = s.isRegex ? s.text : o && isLiteral(o) && unescape(o) === s.text ? o : reEscape(s.text);
  put(out, s.orig, "weight", Number.isFinite(s.weight) ? s.weight : 1, 1);
  put(out, s.orig, "scope", s.head ? "head" : "all", "all");
  put(out, s.orig, "case_sensitive", s.cs, false);
  return out;
}

function fieldsJson(nodes, where, problems) {
  const out = {};
  for (const n of nodes) {
    const key = n.key.trim();
    if (!key) problems.push(`${where ? `Grupa „${where}”` : "Dane do odczytania"}: pole bez nazwy — uzupełnij „Nazwę pola w wyniku” albo usuń pole`);
    else if (key in out) problems.push(`Pole „${key}” występuje dwa razy${where ? ` w grupie „${where}”` : ""} — nazwy pól muszą się różnić`);
    else out[key] = fieldJson(n, where ? `${where}.${key}` : key, problems);
  }
  return out;
}

function fieldJson(n, path, problems) {
  const o = n.orig;
  const out = clone(o);
  const origType = o.type ?? "string";
  put(out, o, "type", n.type, "string");
  put(out, o, "description", n.description, "");
  put(out, o, "labels", n.labels, []);
  put(out, o, "patterns", n.patterns, []);
  put(out, o, "normalize", n.normalize, []);
  put(out, o, n.valKey, n.validators, []);
  put(out, o, "ground", n.ground, "auto");
  put(out, o, "dictionary", n.dictionary || null, null);
  if (n.type === "object") out.properties = fieldsJson(n.children, path, problems);
  else if (origType === "object") delete out.properties;
  if (n.type === "array") out.items = fieldJson(n.items, `${path}[]`, problems);
  else if (origType === "array") delete out.items;
  return out;
}

/** {json, problems}: problems = things that cannot be written (empty / duplicate keys, empty signals). */
export function modelToJson(m) {
  const problems = [];
  const o = m.orig;
  const out = clone(o);
  out.id = m.id.trim();
  out.name = m.name;
  put(out, o, "description", m.description, "");
  put(out, o, "enabled", m.enabled, true);

  const oc = isObj(o.classification) ? o.classification : {};
  const c = clone(oc);
  put(c, oc, "min_score", Number.isFinite(m.min_score) ? m.min_score : 3, 3);
  put(c, oc, "signals", m.signals.map((s, i) => sigJson(s, `Fraza rozpoznawania nr ${i + 1}`, problems)), []);
  put(c, oc, "negative_signals", m.negative.map((s, i) => sigJson(s, `Fraza wykluczająca nr ${i + 1}`, problems)), []);
  out.classification = c;

  const oe = isObj(o.extraction) ? o.extraction : {};
  const e = clone(oe);
  put(e, oe, "mode", m.mode || null, null);
  put(e, oe, "instructions", m.instructions, "");
  if (Object.keys(e).length || "extraction" in o) out.extraction = e;

  out.output = fieldsJson(m.fields, "", problems);
  return { json: out, problems };
}

// ------------------------------------------------------------------ rendering
function h(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "value") e.value = v;
    else if (k === "checked") e.checked = !!v;
    else if (k === "text") e.textContent = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) e.append(kid);
  return e;
}

const TYPE_LABELS = {
  string: "tekst",
  number: "liczba",
  integer: "liczba całkowita",
  boolean: "tak / nie",
  date: "data",
  object: "grupa pól",
  array: "lista",
};
const GROUND_LABELS = {
  auto: "automatycznie (zalecane)",
  exact: "dokładnie taki sam tekst",
  digits: "te same cyfry",
  fuzzy: "przybliżone dopasowanie",
  none: "nie sprawdzaj",
};
const MODE_LABELS = {
  llm: "Model językowy",
  rules: "Reguły",
  hybrid: "Reguły + model językowy",
};
const MODE_HELP = {
  "": "Używany jest sposób ustawiony dla całego programu.",
  llm: "Model językowy — wystarczą dobre opisy pól. Najprostsze w przygotowaniu.",
  rules: "Reguły — dokładne i szybkie, ale wymagają wzorców przygotowanych przez informatyka.",
  hybrid: "Reguły + model językowy — najpierw reguły, a model uzupełnia to, czego reguły nie znalazły.",
};
const WEIGHTS = [
  [1, "Niska"],
  [2, "Średnia"],
  [4, "Wysoka"],
];

let uidN = 0;
const nextId = (p) => `${p}-${++uidN}`;
const hint = (text, id) => h("p", { class: "cf-hint", text, id });

/** <label for> + control (+ optional hint linked through aria-describedby). */
function field(label, control, help, cls = "") {
  const id = control.id || (control.id = nextId("cf"));
  let helpEl = null;
  if (help) {
    helpEl = h("small", { class: "cf-help", id: `${id}-help`, text: help });
    control.setAttribute("aria-describedby", helpEl.id);
  }
  return h("div", { class: `cf-lbl ${cls}`.trim() }, h("label", { for: id, text: label }), control, helpEl);
}

/**
 * Render the form for model `m` into `root`.
 * ctx: {meta, losses: [str], isNew: bool, onChange()} — onChange after every edit (dirty flag).
 */
export function renderForm(root, m, ctx) {
  const { meta } = ctx;
  const changed = () => ctx.onChange();
  const rerender = () => {
    const scroller = root.closest(".workshop") || document.scrollingElement;
    const top = scroller.scrollTop;
    renderForm(root, m, ctx);
    scroller.scrollTop = top;
    const target = root.querySelector("[data-autofocus]");
    if (target) {
      target.removeAttribute("data-autofocus");
      target.focus();
    }
    changed();
  };
  const text = (obj, key, { after, ...attrs } = {}) =>
    h(attrs.rows ? "textarea" : "input", {
      ...attrs,
      value: obj[key],
      oninput: (e) => {
        obj[key] = e.target.value;
        after?.(e.target);
        changed();
      },
    });
  const num = (obj, key, attrs = {}) =>
    h("input", {
      type: "number",
      step: "any",
      ...attrs,
      value: String(obj[key]),
      oninput: (e) => {
        obj[key] = e.target.value === "" ? NaN : Number(e.target.value);
        changed();
      },
    });
  const check = (obj, key, label, attrs = {}) =>
    h(
      "label",
      { class: "check cf-check", ...attrs },
      h("input", {
        type: "checkbox",
        checked: obj[key],
        onchange: (e) => {
          obj[key] = e.target.checked;
          changed();
        },
      }),
      h("span", { text: label }),
    );
  const select = (obj, key, options, onchange, attrs = {}) =>
    h(
      "select",
      {
        ...attrs,
        onchange: (e) => {
          obj[key] = e.target.value;
          if (onchange) onchange(e.target.value);
          else changed();
        },
      },
      options.map(([v, l]) => h("option", { value: v, selected: v === obj[key] ? "selected" : null, text: l })),
    );
  const lines = (obj, key, placeholder) =>
    h("textarea", {
      rows: Math.max(2, Math.min(6, obj[key].length + 1)),
      placeholder,
      spellcheck: "false",
      class: "mono",
      value: obj[key].join("\n"),
      oninput: (e) => {
        obj[key] = e.target.value.split("\n").filter((l) => l.trim() !== "");
        changed();
      },
    });
  const checks = (obj, key, available) => {
    const names = [...available.map((a) => a.name), ...obj[key].filter((n) => !available.some((a) => a.name === n))];
    return h(
      "div",
      { class: "cf-checks" },
      names.map((name) => {
        const info = available.find((a) => a.name === name);
        return h(
          "label",
          { class: `check${info ? "" : " err-text"}`, title: info ? info.doc : "nieznana nazwa — popraw w trybie zaawansowanym lub odznacz" },
          h("input", {
            type: "checkbox",
            checked: obj[key].includes(name),
            onchange: (e) => {
              obj[key] = e.target.checked ? [...obj[key], name] : obj[key].filter((x) => x !== name);
              changed();
            },
          }),
          name,
        );
      }),
    );
  };
  const btn = (label, onclick, attrs = {}) => h("button", { type: "button", class: "btn small", onclick, ...attrs }, label);
  const section = (title, intro, ...kids) => {
    const id = nextId("sec");
    return h("section", { class: "cf-sec", "aria-labelledby": id }, h("h3", { id, text: title }), intro ? hint(intro) : null, ...kids);
  };

  // ---------------------------------------------------------------- 1. podstawowe
  const idMsg = h("small", { class: "cf-help" });
  const idRe = new RegExp(`^(?:${meta.id_pattern})$`);
  const checkId = (input) => {
    const ok = idRe.test(input.value.trim());
    input.classList.toggle("invalid", !ok);
    input.setAttribute("aria-invalid", String(!ok));
    idMsg.className = ok ? "cf-help" : "cf-help err-text";
    idMsg.textContent = ok
      ? m.autoId
        ? "Tworzony automatycznie z nazwy. Używany wewnętrznie przez program."
        : "Używany wewnętrznie przez program."
      : "Dozwolone są tylko małe litery a–z, cyfry i znak _ (bez spacji i polskich liter).";
  };
  if (m.autoId === undefined) m.autoId = !!ctx.isNew;
  const idInput = text(m, "id", {
    spellcheck: "false",
    autocomplete: "off",
    class: "mono",
    placeholder: "np. umowa_najmu",
    after: (t) => {
      m.autoId = false;
      checkId(t);
    },
  });
  idInput.id = nextId("cf");
  idMsg.id = `${idInput.id}-help`;
  idInput.setAttribute("aria-describedby", idMsg.id);
  checkId(idInput);
  const nameInput = text(m, "name", {
    placeholder: "np. Umowa najmu",
    class: "cf-big",
    after: (t) => {
      if (!m.autoId) return;
      m.id = slugify(t.value);
      idInput.value = m.id;
      checkId(idInput);
    },
  });

  const sec1 = section(
    "Podstawowe informacje",
    null,
    field("Nazwa typu dokumentu", nameInput, "Tak ten typ będzie widoczny na liście dokumentów."),
    field("Krótki opis", text(m, "description", { rows: 2, placeholder: "Czym jest dokument i kto go wystawia." })),
    h(
      "div",
      { class: "cf-grid2" },
      h("div", { class: "cf-lbl" }, h("label", { for: idInput.id, text: "Identyfikator" }), idInput, idMsg),
      h(
        "div",
        { class: "cf-lbl cf-lbl-check" },
        check(m, "enabled", "Rozpoznawany (włączony)"),
        h("small", { class: "cf-help", text: "Wyłączony typ nie jest rozpoznawany — takie dokumenty dostają pusty plik JSON." }),
      ),
    ),
  );

  // ---------------------------------------------------------------- 2. rozpoznawanie
  const weightSelect = (s, label) => {
    const known = WEIGHTS.some(([w]) => w === s.weight);
    const opts = WEIGHTS.map(([w, l]) => [String(w), l]);
    if (!known) opts.push(["custom", `Inna (${Number.isFinite(s.weight) ? s.weight : "?"})`]);
    const sel = h(
      "select",
      {
        class: "cf-weight",
        "aria-label": label,
        onchange: (e) => {
          if (e.target.value !== "custom") s.weight = Number(e.target.value);
          changed();
        },
      },
      opts.map(([v, l]) => h("option", { value: v, selected: (known ? String(s.weight) : "custom") === v ? "selected" : null, text: l })),
    );
    return sel;
  };

  const phraseRow = (s, list, n, kind) => {
    const label = `${kind === "neg" ? "Fraza wykluczająca" : "Fraza"} nr ${n}`;
    const bodyId = nextId("more");
    const moreBody = h(
      "div",
      { class: "cf-more-body", id: bodyId, hidden: !s.moreOpen },
      check(s, "cs", "Rozróżniaj wielkość liter"),
      check(s, "isRegex", "To jest wyrażenie regularne (dla informatyków)", {
        title: "Bez zaznaczenia tekst jest szukany dosłownie — kropki, nawiasy itp. nie mają specjalnego znaczenia.",
      }),
    );
    const moreBtn = h(
      "button",
      {
        type: "button",
        class: "cf-more-btn",
        "aria-expanded": String(!!s.moreOpen),
        "aria-controls": bodyId,
        onclick: (e) => {
          s.moreOpen = !s.moreOpen;
          moreBody.hidden = !s.moreOpen;
          e.currentTarget.setAttribute("aria-expanded", String(s.moreOpen));
        },
      },
      "Więcej",
    );
    return h(
      "li",
      { class: "cf-sig" },
      h("input", {
        "data-autofocus": s.focusMe ? (delete s.focusMe, "") : null,
        class: `cf-sig-text${s.isRegex ? " mono" : ""}`,
        spellcheck: "false",
        "aria-label": label,
        placeholder: kind === "neg" ? "np. wyrok" : "np. pełnomocnictwo",
        value: s.text,
        oninput: (e) => {
          s.text = e.target.value;
          changed();
        },
      }),
      h("label", { class: "cf-inline" }, h("span", { class: "cf-inline-lbl", text: "Ważność" }), weightSelect(s, `${label} — ważność`)),
      check(s, "head", "Tylko w nagłówku dokumentu", { title: "Szukaj tylko na początku dokumentu (np. w tytule)" }),
      moreBtn,
      btn(
        "Usuń",
        () => {
          list.splice(list.indexOf(s), 1);
          rerender();
        },
        { class: "btn small ghost", "aria-label": `Usuń: ${label}` },
      ),
      moreBody,
    );
  };

  const advRow = (s) => {
    const parts = [WEIGHTS.find(([w]) => w === s.weight)?.[1] || `waga ${s.weight}`];
    if (s.head) parts.push("tylko w nagłówku");
    if (s.cs) parts.push("wielkość liter ma znaczenie");
    return h("li", { class: "cf-adv-row" }, h("code", { text: s.text }), h("span", { class: "muted small", text: parts.join(" · ") }));
  };

  const phraseList = (list, kind, title, help, addLabel) => {
    const simple = list.filter((s) => !s.adv);
    const adv = list.filter((s) => s.adv);
    const kids = [
      h("h4", { text: title }),
      hint(help),
      simple.length
        ? h("ul", { class: "cf-sigs" }, simple.map((s, i) => phraseRow(s, list, i + 1, kind)))
        : h("p", { class: "muted small", text: adv.length ? "Brak zwykłych fraz — dodaj własną." : "Brak fraz." }),
      btn(addLabel, () => {
        list.push({ ...newSignal(), focusMe: true });
        rerender();
      }),
    ];
    if (adv.length)
      kids.push(
        h(
          "details",
          { class: "cf-advgroup" },
          h("summary", { text: `Wyrażenia zaawansowane (dla informatyków): ${adv.length}` }),
          hint("Te wzorce przygotował informatyk w specjalnym zapisie. Działają razem z frazami powyżej. Można je zmienić tylko w trybie zaawansowanym (YAML)."),
          h("ul", { class: "cf-adv-list" }, adv.map(advRow)),
        ),
      );
    return h("div", { class: "cf-siglist", "data-kind": kind }, ...kids);
  };

  const sec2 = section(
    "Jak rozpoznać dokument",
    "Wpisz zwroty, które zwykle występują w tym dokumencie (np. jego tytuł). Każda znaleziona fraza daje punkty — Niska 1, Średnia 2, Wysoka 4. Wielkość liter i polskie znaki zwykle nie mają znaczenia.",
    phraseList(m.signals, "pos", "Dokument zawiera…", "Typowe zwroty i słowa z tego dokumentu.", "+ Dodaj frazę"),
    phraseList(m.negative, "neg", "Wyklucz, jeśli dokument zawiera…", "Zwroty, które świadczą, że to INNY dokument — odejmują punkty.", "+ Dodaj frazę wykluczającą"),
    field(
      "Wymagana liczba punktów",
      num(m, "min_score", { min: "0", class: "cf-short" }),
      "Dokument zostaje rozpoznany, gdy suma punktów osiągnie tę wartość. Np. 4 = wystarczy jedna fraza o wysokiej ważności albo dwie o średniej.",
    ),
  );

  // ---------------------------------------------------------------- 3. ekstrakcja
  const modes = meta.extraction_modes || [];
  const modeOpts = [["", "Domyślny (ustawienie programu)"], ...modes.map((v) => [v, MODE_LABELS[v] || v])];
  if (m.mode && !modes.includes(m.mode)) modeOpts.push([m.mode, m.mode]);
  const modeHelp = h("small", { class: "cf-help", text: MODE_HELP[m.mode] ?? "" });
  const modeSel = select(m, "mode", modeOpts, (v) => {
    modeHelp.textContent = MODE_HELP[v] ?? "";
    changed();
  });
  modeSel.id = nextId("cf");
  modeHelp.id = `${modeSel.id}-help`;
  modeSel.setAttribute("aria-describedby", modeHelp.id);
  const sec3 = section(
    "Jak odczytywać dane",
    null,
    modes.length
      ? h("div", { class: "cf-lbl" }, h("label", { for: modeSel.id, text: "Sposób odczytu" }), modeSel, modeHelp)
      : h("p", { class: "muted small", text: "Ta wersja programu używa sposobu odczytu ustawionego dla całego programu." }),
    field(
      "Dodatkowe wskazówki dla modelu językowego",
      text(m, "instructions", { rows: 3, placeholder: "np. Kwoty podawaj w złotych; daty w formacie RRRR-MM-DD; gdy brak danych — zostaw puste." }),
      "Opcjonalnie. Pisz zwykłym językiem.",
    ),
  );

  // ---------------------------------------------------------------- 4. pola
  const typeOpts = meta.field_types.map((t) => [t, TYPE_LABELS[t] || t]);
  const dictOpts = (cur) => {
    const opts = [["", "— brak —"], ...meta.dictionaries.map((d) => [d.id, d.description ? `${d.id} — ${d.description}` : d.id])];
    if (cur && !meta.dictionaries.some((d) => d.id === cur)) opts.push([cur, `${cur} (nieznany)`]);
    return opts;
  };
  const HANDLED = new Set(["type", "description", "labels", "patterns", "normalize", "validate", "validators", "ground", "dictionary", "properties", "items"]);

  const fieldList = (nodes, depth, addLabel) =>
    h(
      "div",
      { class: "cf-fields" },
      nodes.map((n, i) => fieldCard(n, nodes, i, depth)),
      btn(addLabel, () => {
        nodes.push({ ...newField(), focusMe: true });
        rerender();
      }),
    );

  const onType = (n) => () => {
    if (n.type === "object" && !n.children) n.children = [];
    if (n.type === "array" && !n.items) n.items = fieldModel(null, { type: "string" });
    rerender();
  };

  const descInput = (n, group = false) =>
    text(n, "description", {
      "data-autofocus": n.focusMe ? (delete n.focusMe, "") : null,
      rows: 2,
      class: "cf-desc",
      placeholder: group ? "np. Dane przelewu z potwierdzenia bankowego." : "np. Data wystawienia dokumentu, zapisana jako RRRR-MM-DD.",
    });

  const advanced = (n) => {
    const extra = Object.keys(n.orig).filter((k) => !HANDLED.has(k));
    return h(
      "details",
      { class: "cf-adv" },
      h("summary", { text: "Ustawienia dla informatyków" }),
      hint("Potrzebne tylko przy odczycie regułami. Przy modelu językowym wystarczy dobry opis pola."),
      h(
        "div",
        { class: "cf-grid2" },
        field("Etykiety (jedna w wierszu)", lines(n, "labels", "np. Data wystawienia"), "Tekst przed wartością w dokumencie, np. „NIP:”"),
        field("Wzorce — wyrażenia regularne (jeden w wierszu)", lines(n, "patterns", "np. NIP[:\\s]*(?P<value>\\d{10})"), "Wartość = grupa (?P<value>…) albo pierwsza grupa"),
      ),
      h("div", { class: "cf-lbl" }, h("span", { class: "cf-lbl-text", text: "Porządkowanie wartości (normalizatory)" }), checks(n, "normalize", meta.normalizers)),
      h("div", { class: "cf-lbl" }, h("span", { class: "cf-lbl-text", text: "Kontrola poprawności (walidatory)" }), checks(n, "validators", meta.validators)),
      h(
        "div",
        { class: "cf-grid2" },
        field("Słownik wartości", select(n, "dictionary", dictOpts(n.dictionary))),
        field("Sprawdzanie, czy wartość występuje w tekście", select(n, "ground", meta.grounds.map((g) => [g, GROUND_LABELS[g] || g]))),
      ),
      extra.length ? h("p", { class: "muted small", text: `Inne ustawienia tego pola (zachowane bez zmian, edycja w trybie zaawansowanym): ${extra.join(", ")}` }) : null,
    );
  };

  const fieldBody = (n, depth) => [
    n.type === "object" ? h("div", { class: "cf-sub" }, h("h4", { text: "Pola w tej grupie" }), fieldList(n.children, depth + 1, "+ Dodaj pole w grupie")) : null,
    n.type === "array"
      ? h(
          "div",
          { class: "cf-sub" },
          h("h4", { text: "Każdy element listy" }),
          field("Rodzaj elementu", select(n.items, "type", typeOpts.filter(([t]) => t !== "array"), onType(n.items))),
          n.items.type === "object" ? null : field("Opis elementu", descInput(n.items)),
          fieldBody(n.items, depth + 1),
        )
      : null,
    n.type === "object" || n.type === "array" ? null : advanced(n),
  ];

  const fieldCard = (n, siblings, i, depth) => {
    const human = (k) => (k ? humanizeKey(k) : "(nowe pole)");
    const sumKey = h("strong", { text: human(n.key) });
    const sumDesc = h("span", { class: "cf-sum-desc", text: n.description ? n.description.split("\n")[0] : "" });
    const move = (d) => () => {
      const j = i + d;
      if (j < 0 || j >= siblings.length) return;
      [siblings[i], siblings[j]] = [siblings[j], siblings[i]];
      rerender();
    };
    const keyInput = text(n, "key", {
      spellcheck: "false",
      class: "mono",
      placeholder: "np. data_wystawienia",
      after: (t) => (sumKey.textContent = human(t.value)),
    });
    return h(
      "details",
      {
        class: "cf-field",
        open: (n.open ?? (!!n.isNew || (depth === 0 && n.type !== "object" && n.type !== "array"))) || null,
        ontoggle: (e) => (n.open = e.target.open),
      },
      h("summary", {}, sumKey, h("span", { class: "cf-type", text: TYPE_LABELS[n.type] || n.type }), sumDesc),
      n.type === "object" ? null : field("Opis pola", descInput(n), "Opisz pole tak, jak koledze z pracy — co to za wartość i jak ją rozpoznać.", "cf-desc-wrap"),
      h(
        "div",
        { class: "cf-row" },
        field("Nazwa pola w wyniku", keyInput, "Klucz w pliku JSON: bez spacji i polskich liter, np. data_wystawienia."),
        field("Rodzaj", select(n, "type", typeOpts, onType(n))),
        h(
          "div",
          { class: "cf-tools", role: "group", "aria-label": "Kolejność i usuwanie" },
          btn("↑", move(-1), { title: "Przesuń wyżej", "aria-label": "Przesuń pole wyżej", disabled: i === 0 ? true : null }),
          btn("↓", move(1), { title: "Przesuń niżej", "aria-label": "Przesuń pole niżej", disabled: i === siblings.length - 1 ? true : null }),
          btn(
            "Usuń pole",
            () => {
              if (n.children?.length && !confirm(`Usunąć pole „${n.key}” razem z polami w tej grupie?`)) return;
              siblings.splice(i, 1);
              rerender();
            },
            { class: "btn small ghost" },
          ),
        ),
      ),
      n.type === "object" ? field("Opis grupy (opcjonalnie)", descInput(n, true)) : null,
      fieldBody(n, depth),
    );
  };

  // Built-in types are read by rules and their output is a fixed JSON format: show WHAT is read, compactly.
  // Editing that structure is a job for IT (behind a button); types from the wizard (mode llm) stay editable.
  const firstSentence = (d) => String(d).replace(/\s+/g, " ").trim().split(/(?<=[.!?])\s/)[0];
  const typeLabel = (n) =>
    n.type === "array" && n.items && n.items.type !== "object" ? `lista (${TYPE_LABELS[n.items.type] || n.items.type})` : TYPE_LABELS[n.type] || n.type;
  const fieldTree = (nodes) =>
    h(
      "ul",
      { class: "cf-tree" },
      nodes.map((n) => {
        const kids = n.type === "object" ? n.children : n.type === "array" && n.items?.type === "object" ? n.items.children : null;
        return h(
          "li",
          {},
          h(
            "div",
            { class: "cf-tree-row" },
            h("span", { class: "cf-tree-name", text: humanizeKey(n.key) }),
            h("span", { class: "cf-type", text: typeLabel(n) }),
            n.orig?.derived_from ? h("span", { class: "cf-type cf-type-dict", text: "ze słownika" }) : null,
            h("code", { class: "cf-tree-key", text: n.key }),
          ),
          n.description && !kids?.length ? h("p", { class: "cf-tree-desc", text: firstSentence(n.description) }) : null,
          kids?.length ? fieldTree(kids) : null,
        );
      }),
    );
  const fixedStructure = !ctx.isNew && (!m.mode || m.mode === "rules") && !m.editStructure;
  const sec4 = fixedStructure
    ? section(
        "Dane do odczytania",
        "Te dane system odczytuje z dokumentu. Układ wyniku jest stały (wymagany format pliku JSON), a odczyt działa na regułach — tu niczego nie trzeba ustawiać.",
        fieldTree(m.fields),
        btn("Zmień strukturę wyniku (dla informatyków)", () => ((m.editStructure = true), rerender()), { class: "btn small ghost" }),
      )
    : section(
        "Dane do odczytania",
        "Każde pole to jedna wartość w wyniku. „Grupa pól” łączy kilka pól (np. dane zamawiającego), „lista” — powtarzające się elementy (np. kilku pełnomocników).",
        fieldList(m.fields, 0, "+ Dodaj pole"),
      );

  const onlyComments = ctx.losses?.length && ctx.losses.every((l) => /komentarz/i.test(l));
  const banner = !ctx.losses?.length
    ? null
    : onlyComments
      ? h("p", { class: "cf-note", text: "Zapis z formularza pominie notatki techniczne zapisane w pliku konfiguracji. Wszystkie ustawienia zostaną zachowane." })
      : h(
          "div",
          { class: "alert warn" },
          h("strong", { text: "Uwaga: " }),
          "ten typ ma ustawienia, których formularz nie zachowa przy zapisie (" + ctx.losses.join("; ") + "). ",
          "Jeśli chcesz coś zmienić, poproś informatyka o edycję w trybie zaawansowanym. Samo przeglądanie niczego nie zmienia.",
        );

  root.replaceChildren(...[banner, sec1, sec2, sec3, sec4].filter(Boolean));
}
