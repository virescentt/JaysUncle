// Tryb "Formularz" zakładki "Klasy dokumentów": przyjazna edycja klasy bez znajomości YAML.
// Formularz pracuje na danych klasy (to, co daje yaml.safe_load) — model → JSON → /api/doc-types/dump → YAML.
// Klucze, których formularz nie obsługuje (strategy, params, section, enum, …), są przechowywane i oddawane bez zmian,
// kolejność kluczy jest zachowana.

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
export const newSignal = () => ({ orig: {}, text: "", isRegex: false, weight: 1, head: false, cs: false });

// ------------------------------------------------------------------ model → JSON
function sigJson(s, where, problems) {
  const out = clone(s.orig);
  if (!s.text.trim()) problems.push(`${where}: puste wyrażenie — wpisz tekst albo usuń wiersz`);
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
    if (!key) problems.push(`${where || "Pola wyniku"}: pole bez nazwy — uzupełnij nazwę albo usuń pole`);
    else if (key in out) problems.push(`${where ? `${where}.` : ""}${key}: nazwa pola powtarza się na tym samym poziomie`);
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
  put(c, oc, "signals", m.signals.map((s, i) => sigJson(s, `Wyrażenie charakterystyczne ${i + 1}`, problems)), []);
  put(c, oc, "negative_signals", m.negative.map((s, i) => sigJson(s, `Wyrażenie wykluczające ${i + 1}`, problems)), []);
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
  object: "grupa pól (obiekt)",
  array: "lista (wiele elementów)",
};
const GROUND_LABELS = {
  auto: "automatycznie (zalecane)",
  exact: "dokładnie taki sam tekst",
  digits: "te same cyfry",
  fuzzy: "przybliżone dopasowanie",
  none: "nie sprawdzaj",
};
const MODE_LABELS = { llm: "LLM (wystarczą opisy pól)", rules: "Reguły", hybrid: "Reguły + LLM" };

const hint = (text) => h("p", { class: "cf-hint", text });
const labelled = (label, control, help) => h("label", { class: "cf-lbl" }, h("span", { text: label }), control, help ? h("small", { class: "muted", text: help }) : null);

/**
 * Render the form for model `m` into `root`.
 * ctx: {meta, losses: [str], onChange(), onStructure()} — onChange after every edit (dirty flag),
 * onStructure: re-render request (add / remove / type change) — handled here by calling render again.
 */
export function renderForm(root, m, ctx) {
  const { meta } = ctx;
  const changed = () => ctx.onChange();
  const rerender = () => {
    const scroller = root.closest(".workshop") || document.scrollingElement;
    const top = scroller.scrollTop;
    renderForm(root, m, ctx);
    scroller.scrollTop = top;
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
  const check = (obj, key, label, title) =>
    h(
      "label",
      { class: "check cf-check", title },
      h("input", {
        type: "checkbox",
        checked: obj[key],
        onchange: (e) => {
          obj[key] = e.target.checked;
          changed();
        },
      }),
      label,
    );
  const select = (obj, key, options, onchange) =>
    h(
      "select",
      {
        onchange: (e) => {
          obj[key] = e.target.value;
          if (onchange) onchange();
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
          { class: `check${info ? "" : " err-text"}`, title: info ? info.doc : "nieznana nazwa — popraw w trybie YAML lub odznacz" },
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

  // ---------------------------------------------------------------- 1. podstawowe
  const idMsg = h("small", { class: "muted" });
  const idRe = new RegExp(`^(?:${meta.id_pattern})$`);
  const checkId = (input) => {
    const ok = idRe.test(input.value.trim());
    input.classList.toggle("invalid", !ok);
    idMsg.className = ok ? "muted" : "err-text";
    idMsg.textContent = ok
      ? "Nazwa pliku: config/doc_types/" + input.value.trim() + ".yaml"
      : "Dozwolone tylko małe litery a-z, cyfry i podkreślenie _ (bez spacji i polskich znaków), maks. 64 znaki.";
  };
  const idInput = text(m, "id", { spellcheck: "false", autocomplete: "off", after: checkId, placeholder: "np. umowa_najmu" });
  checkId(idInput);

  const sec1 = h(
    "fieldset",
    { class: "cf-sec" },
    h("legend", { text: "1. Podstawowe" }),
    hint("Identyfikator to techniczna nazwa klasy (i pliku). Nazwa i opis są widoczne w panelu."),
    h("div", { class: "cf-grid2" }, h("label", { class: "cf-lbl" }, h("span", { text: "Identyfikator (id)" }), idInput, idMsg), labelled("Nazwa", text(m, "name", { placeholder: "np. Umowa najmu" }))),
    labelled("Opis", text(m, "description", { rows: 2, placeholder: "Czym jest dokument i kto go wystawia." })),
    check(m, "enabled", "klasa aktywna (używana przy przetwarzaniu dokumentów)"),
  );

  // ---------------------------------------------------------------- 2. rozpoznawanie
  const sigList = (list, title, help, addLabel) => {
    const rows = list.map((s, i) =>
      h(
        "div",
        { class: "cf-sig" },
        text(s, "text", { class: "cf-sig-text", spellcheck: "false", placeholder: s.isRegex ? "wyrażenie regularne, np. sygn\\.?\\s*akt" : "słowo lub zwrot, np. pełnomocnictwo", "aria-label": `${title} ${i + 1}` }),
        h("label", { class: "cf-lbl cf-weight", title: "Ile punktów daje trafienie" }, h("span", { text: "waga" }), num(s, "weight", { min: "0" })),
        check(s, "head", "tylko początek dokumentu", "Szukaj tylko na początku dokumentu (np. tytuł)"),
        check(s, "cs", "rozróżniaj wielkość liter"),
        check(s, "isRegex", "to jest wyrażenie regularne", "Bez zaznaczenia tekst jest szukany dosłownie (kropki, nawiasy itp. nie mają specjalnego znaczenia)"),
        btn("Usuń", () => {
          list.splice(i, 1);
          rerender();
        }, { class: "btn small ghost", title: "Usuń wiersz" }),
      ),
    );
    return h(
      "div",
      { class: "cf-siglist" },
      h("h4", { text: title }),
      hint(help),
      rows.length ? rows : h("p", { class: "muted small", text: "Brak." }),
      btn(addLabel, () => {
        list.push(newSignal());
        rerender();
      }),
    );
  };
  const sec2 = h(
    "fieldset",
    { class: "cf-sec" },
    h("legend", { text: "2. Rozpoznawanie dokumentu" }),
    hint("System sumuje wagi wyrażeń znalezionych w treści dokumentu. Gdy suma osiągnie próg, dokument zostaje uznany za tę klasę. Wielkość liter i polskie znaki zwykle nie mają znaczenia."),
    labelled("Próg rozpoznania (min. suma punktów)", num(m, "min_score", { min: "0", class: "cf-short" })),
    sigList(m.signals, "Słowa i wyrażenia charakterystyczne", "Teksty typowe dla tego dokumentu, np. jego tytuł. Każde trafienie dodaje swoją wagę.", "+ dodaj wyrażenie"),
    sigList(m.negative, "Wyrażenia wykluczające", "Teksty, które świadczą, że to INNY dokument — każde trafienie odejmuje swoją wagę.", "+ dodaj wyrażenie wykluczające"),
  );

  // ---------------------------------------------------------------- 3. ekstrakcja
  const modes = meta.extraction_modes || [];
  const modeOpts = [["", "domyślny z ustawień"], ...modes.map((v) => [v, MODE_LABELS[v] || v])];
  if (m.mode && !modes.includes(m.mode)) modeOpts.push([m.mode, m.mode]);
  const sec3 = h(
    "fieldset",
    { class: "cf-sec" },
    h("legend", { text: "3. Sposób ekstrakcji" }),
    hint("„LLM” — model językowy wyszukuje wartości na podstawie opisów pól (najprostsze, nie wymaga reguł). „Reguły” — etykiety i wzorce z sekcji „Zaawansowane” przy polach. „Reguły + LLM” — reguły, a model uzupełnia brakujące pola."),
    modes.length ? labelled("Tryb", select(m, "mode", modeOpts)) : h("p", { class: "muted small", text: "Ta wersja programu nie obsługuje wyboru trybu dla klasy — używany jest tryb z ustawień." }),
    labelled("Dodatkowe wskazówki dla modelu", text(m, "instructions", { rows: 4, placeholder: "np. Kwoty podawaj w złotych; daty w formacie RRRR-MM-DD; brak danych → null." })),
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
        nodes.push(newField());
        rerender();
      }),
    );

  const onType = (n) => () => {
    if (n.type === "object" && !n.children) n.children = [];
    if (n.type === "array" && !n.items) n.items = fieldModel(null, { type: "string" });
    rerender();
  };

  const fieldBody = (n, depth) => {
    const extra = Object.keys(n.orig).filter((k) => !HANDLED.has(k));
    const adv = h(
      "details",
      { class: "cf-adv" },
      h("summary", { text: "Zaawansowane — dla reguł" }),
      hint("Potrzebne tylko w trybie „Reguły” / „Reguły + LLM”. W trybie LLM wystarczy dobry opis pola."),
      h(
        "div",
        { class: "cf-grid2" },
        labelled("Etykiety (jedna w wierszu)", lines(n, "labels", "np. Data wystawienia"), "Tekst przed wartością w dokumencie, np. „NIP:”"),
        labelled("Wzorce — wyrażenia regularne (jeden w wierszu)", lines(n, "patterns", "np. NIP[:\\s]*(?P<value>\\d{10})"), "Wartość = grupa (?P<value>…) albo pierwsza grupa"),
      ),
      h("div", { class: "cf-lbl" }, h("span", { text: "Normalizatory (porządkowanie wartości)" }), checks(n, "normalize", meta.normalizers)),
      h("div", { class: "cf-lbl" }, h("span", { text: "Walidatory (kontrola poprawności)" }), checks(n, "validators", meta.validators)),
      h(
        "div",
        { class: "cf-grid2" },
        labelled("Słownik wartości", select(n, "dictionary", dictOpts(n.dictionary))),
        labelled("Sprawdzanie, czy wartość występuje w tekście", select(n, "ground", meta.grounds.map((g) => [g, GROUND_LABELS[g] || g]))),
      ),
      extra.length ? h("p", { class: "muted small", text: `Inne ustawienia tego pola (zachowane bez zmian, edycja w trybie YAML): ${extra.join(", ")}` }) : null,
    );
    return [
      labelled(
        "Opis — najważniejsze: na jego podstawie model językowy szuka wartości",
        text(n, "description", { rows: 2, placeholder: "np. Data wystawienia dokumentu, w formacie RRRR-MM-DD." }),
      ),
      n.type === "object" ? h("div", { class: "cf-sub" }, h("h4", { text: "Pola w tej grupie" }), fieldList(n.children, depth + 1, "+ dodaj pole w grupie")) : null,
      n.type === "array"
        ? h(
            "div",
            { class: "cf-sub" },
            h("h4", { text: "Element listy" }),
            labelled("Typ elementu", select(n.items, "type", typeOpts.filter(([t]) => t !== "array"), onType(n.items))),
            fieldBody(n.items, depth + 1),
          )
        : null,
      n.type === "object" || n.type === "array" ? null : adv,
    ];
  };

  const fieldCard = (n, siblings, i, depth) => {
    const sumKey = h("strong", { text: n.key || "(bez nazwy)" });
    const sumDesc = h("span", { class: "muted", text: n.description ? ` — ${n.description.split("\n")[0].slice(0, 90)}` : "" });
    const move = (d) => () => {
      const j = i + d;
      if (j < 0 || j >= siblings.length) return;
      [siblings[i], siblings[j]] = [siblings[j], siblings[i]];
      rerender();
    };
    const card = h(
      "details",
      { class: "cf-field", open: (n.open ?? (depth === 0 || !!n.isNew)) || null, ontoggle: (e) => (n.open = e.target.open) },
      h("summary", {}, sumKey, h("span", { class: "cf-type", text: ` ${TYPE_LABELS[n.type] || n.type}` }), sumDesc),
      h(
        "div",
        { class: "cf-row" },
        labelled("Nazwa pola w JSON", text(n, "key", { spellcheck: "false", placeholder: "np. data_wystawienia", after: (t) => (sumKey.textContent = t.value || "(bez nazwy)") })),
        labelled("Typ", select(n, "type", typeOpts, onType(n))),
        h(
          "div",
          { class: "cf-tools" },
          btn("↑", move(-1), { title: "Przesuń wyżej", disabled: i === 0 ? true : null }),
          btn("↓", move(1), { title: "Przesuń niżej", disabled: i === siblings.length - 1 ? true : null }),
          btn("Usuń pole", () => {
            if (n.children?.length && !confirm(`Usunąć pole „${n.key}” razem z polami podrzędnymi?`)) return;
            siblings.splice(i, 1);
            rerender();
          }, { class: "btn small ghost" }),
        ),
      ),
      fieldBody(n, depth),
    );
    return card;
  };

  const sec4 = h(
    "fieldset",
    { class: "cf-sec" },
    h("legend", { text: "4. Pola wyniku (struktura JSON)" }),
    hint("Każde pole to jedna wartość w wynikowym pliku JSON. Grupa pól tworzy zagnieżdżony obiekt, lista — powtarzające się elementy (np. kilku wykonawców)."),
    fieldList(m.fields, 0, "+ dodaj pole"),
  );

  const banner = ctx.losses?.length
    ? h(
        "div",
        { class: "alert warn" },
        h("strong", { text: "Ta klasa ma elementy, których formularz nie zachowa przy zapisie: " }),
        ctx.losses.join("; ") + ". ",
        "Zalecamy edycję tej klasy w trybie YAML. Samo przełączenie bez zmian niczego nie usuwa.",
      )
    : null;

  root.replaceChildren(...[banner, sec1, sec2, sec3, sec4].filter(Boolean));
}
