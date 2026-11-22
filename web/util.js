// Shared helpers of the panel: DOM, API calls, formatting, small rendering pieces used by both tabs.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

let uidCounter = 0;
export const uid = () => `d${Date.now().toString(36)}${(uidCounter++).toString(36)}`;

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

/** fetch wrapper: JSON or text body; non-2xx → ApiError with the server's Polish message (detail). */
export async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError("Brak połączenia z serwerem (czy działa ./start.sh web?)", 0, null);
  }
  const ctype = res.headers.get("content-type") || "";
  const body = ctype.includes("application/json") ? await res.json().catch(() => null) : await res.text();
  if (!res.ok) {
    let detail = body && typeof body === "object" ? body.detail : body;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
    throw new ApiError(detail ? String(detail) : `Błąd HTTP ${res.status}`, res.status, body);
  }
  return body;
}

let toastTimer = null;
export function toast(message, ms = 2800) {
  const el = $("#toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), ms);
}

export const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`);
export const secs = (v) =>
  v === null || v === undefined ? "" : v < 0.1 ? "<0,1 s" : `${v.toFixed(v < 10 ? 1 : 0).replace(".", ",")} s`;
export const trunc = (s, n) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
export const squash = (s) => String(s ?? "").replace(/\s+/g, " ").trim();

/** Value as it appears in the JSON (strings without quotes). */
export function fmtValue(v) {
  if (v === null || v === undefined) return "null";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// ------------------------------------------------------------------ class colours / badges
const HUES = [217, 162, 28, 275, 340, 95, 195, 48];
let classOrder = [];
export function setClassOrder(ids) {
  classOrder = [...ids];
}
export function classHue(id) {
  const i = classOrder.indexOf(id);
  if (i >= 0) return HUES[i % HUES.length];
  let h = 0;
  for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return HUES[h % HUES.length];
}
export function badgeHtml(docType, name, big = false) {
  const cls = big ? " big" : "";
  if (!docType) return `<span class="badge unknown${cls}" title="Dokument nie pasuje do żadnego typu — wynikiem jest pusty plik JSON">Nie rozpoznano</span>`;
  return `<span class="badge${cls}" style="--h:${classHue(docType)}">${esc(name || docType)}</span>`;
}

// ------------------------------------------------------------------ human wording
/** Polish plural: plural(5, ["typ", "typy", "typów"]). */
export function plural(n, [one, few, many]) {
  if (n === 1) return one;
  const d = n % 10;
  const t = n % 100;
  return d >= 2 && d <= 4 && (t < 12 || t > 14) ? few : many;
}

// key words written without Polish letters in field names → readable label
const WORDS = {
  tytul: "tytuł", odwolanie: "odwołanie", odwolujacy: "odwołujący", zamawiajacy: "zamawiający", zamowienie: "zamówienie",
  czynnosc: "czynność", dzien: "dzień", powod: "powód", imie: "imię", miejscowosc: "miejscowość", pelnomocnicy: "pełnomocnicy",
  pelnomocnik: "pełnomocnik", pelnomocnika: "pełnomocnika", podpisujacy: "podpisujący", postepowania: "postępowania",
  postepowanie: "postępowanie", prowadzacy: "prowadzący", wartosc: "wartość", oplata: "opłata", siedziba: "siedziba",
  mail: "e-mail", email: "e-mail", krs: "KRS", nip: "NIP", pesel: "PESEL", regon: "REGON", bzp: "BZP", ted: "TED", kio: "KIO",
  id: "ID", uzp: "UZP", iban: "IBAN", nr: "nr", wplata: "wpłata", data: "data", numer: "numer", ulica: "ulica",
  kod: "kod", pocztowy: "pocztowy", adres: "adres", tresc: "treść", zarzut: "zarzut", zarzuty: "zarzuty", zadanie: "żądanie",
  zadania: "żądania", uzasadnienie: "uzasadnienie", wykonawca: "wykonawca", wykonawcy: "wykonawcy", sad: "sąd",
};

/** "data_operacji" → "Data operacji", "OdwolanieKIO" → "Odwołanie KIO", "przez-pelnomocnika" → "Przez pełnomocnika". */
export function humanizeKey(key) {
  const words = String(key ?? "")
    .replace(/([a-ząćęłńóśźż])([A-ZĄĆĘŁŃÓŚŹŻ])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .split(/[\s_\-.]+/)
    .filter(Boolean)
    .map((w) => {
      const low = w.toLowerCase();
      return WORDS[low] ?? (w === w.toUpperCase() && w.length > 1 ? w : low);
    });
  if (words.length > 1 && words[words.length - 1] === "ID") words[words.length - 1] = "(id)"; // "powod_id" → "Powód (id)"
  const s = words.join(" ");
  return s ? s[0].toUpperCase() + s.slice(1) : "";
}

/** Path of an output field → {group, label}; "a.b[0].c" → group "A › B — nr 1", label "C". */
export function fieldLabel(path) {
  const parts = String(path ?? "").split(".");
  const leaf = parts.pop() || "";
  const human = (seg) => seg.replace(/^(.*?)\[(\d+)\]$/, (_, k, n) => `${humanizeKey(k)} — nr ${+n + 1}`).replace(/^[^—]*$/, (k) => humanizeKey(k));
  return { group: parts.map(human).join(" › "), label: human(leaf) };
}

/** Confidence 0..1 → plain level. */
export function confLevel(c) {
  if (c === null || c === undefined) return null;
  if (c >= 0.8) return { key: "hi", text: "Wysoka" };
  if (c >= 0.6) return { key: "mid", text: "Średnia" };
  return { key: "lo", text: "Niska" };
}
export function confWord(c) {
  const l = confLevel(c);
  if (!l) return `<span class="muted">—</span>`;
  const icon = l.key === "hi" ? "●" : l.key === "mid" ? "◐" : "○";
  return `<span class="conf-word ${l.key}" title="Pewność odczytu: ${l.text.toLowerCase()}"><span aria-hidden="true">${icon}</span> ${l.text}</span>`;
}

// ------------------------------------------------------------------ extraction method / confidence
const METHOD_BASE = { rules: "reguły", llm: "model językowy", derived: "słownik" };
const METHOD_DETAIL = { pattern: "wzorzec", label: "etykieta", section: "sekcja", heuristic: "heurystyka" };
export function methodLabel(m) {
  if (!m) return "—";
  const [base, ...rest] = String(m).split(":");
  const detail = rest.join(":");
  return (METHOD_BASE[base] || base) + (detail ? ` · ${METHOD_DETAIL[detail] || detail}` : "");
}
export function methodHtml(m) {
  if (!m) return `<span class="muted">—</span>`;
  const base = String(m).split(":")[0];
  const known = base in METHOD_BASE ? base : "other";
  return `<span class="method m-${known}" title="metoda: ${esc(m)}">${esc(methodLabel(m))}</span>`;
}
export function confHtml(c) {
  if (c === null || c === undefined) return `<span class="muted">—</span>`;
  const level = c >= 0.8 ? "hi" : c >= 0.5 ? "mid" : "lo";
  return `<span class="conf ${level}" title="pewność ${pct(c)}"><span class="bar"><i style="--v:${Math.max(0, Math.min(1, c))}"></i></span>${pct(c)}</span>`;
}
export const classificationMethod = (m) => ({ rules: "reguły", llm: "model językowy", none: "—" })[m] || m;

// ------------------------------------------------------------------ JSON syntax highlighting
const JSON_TOKEN = /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g;
export function highlightJson(text) {
  let out = "";
  let last = 0;
  let m;
  JSON_TOKEN.lastIndex = 0;
  while ((m = JSON_TOKEN.exec(text))) {
    out += esc(text.slice(last, m.index));
    if (m[1]) out += m[2] ? `<span class="j-key">${esc(m[1])}</span>${esc(m[2])}` : `<span class="j-str">${esc(m[1])}</span>`;
    else if (m[3]) out += `<span class="j-lit">${m[3]}</span>`;
    else out += `<span class="j-num">${m[4]}</span>`;
    last = JSON_TOKEN.lastIndex;
  }
  return out + esc(text.slice(last));
}

/** Save text as a file (works offline, also for an empty file). */
export function downloadText(text, filename, type = "application/json") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

/** In-app confirmation instead of the browser's "localhost says" box. Resolves true (OK) or false (cancel, Esc, click outside). */
export function confirmDialog(message, { title = "Potwierdź", ok = "Tak", cancel = "Anuluj", danger = false } = {}) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "confirm-dlg";
    dlg.setAttribute("aria-labelledby", "confirm-title");
    dlg.innerHTML = `<form method="dialog">
        <h2 id="confirm-title">${esc(title)}</h2>
        <p>${esc(message).replace(/\n/g, "<br>")}</p>
        <div class="confirm-actions">
          <button class="btn" type="submit" value="cancel">${esc(cancel)}</button>
          <button class="btn ${danger ? "danger" : "primary"}" type="submit" value="ok">${esc(ok)}</button>
        </div>
      </form>`;
    document.body.append(dlg);
    dlg.addEventListener("close", () => {
      resolve(dlg.returnValue === "ok");
      dlg.remove();
    });
    dlg.addEventListener("click", (e) => {
      if (e.target === dlg) dlg.close("cancel"); // click on the backdrop
    });
    dlg.showModal();
    dlg.querySelector(danger ? '[value="cancel"]' : '[value="ok"]').focus(); // risky action → the safe button has focus
  });
}
