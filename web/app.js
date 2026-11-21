// Lokalny frontend do API procesu PDF. Wszystkie biblioteki są serwowane z /static.
import * as pdfjsLib from "./vendor/pdfjs/pdf.mjs";

// Worker PDF.js musi być osobnym plikiem - przetwarzanie stron dzieje się poza wątkim UI.
pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/vendor/pdfjs/pdf.worker.mjs";

// Krótkie pomocniki DOM i bezpiecznego HTML.
const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
})[c]);

// Jedno wspólne miejsce stanu: wybrane pliki, wyniki API i aktywny PDF.
const state = {
  selectedFiles: [],
  results: [],
  activeResult: null,
  activeFile: null,
  pdfDocument: null,
  currentPage: 1,
  currentViewport: null,
  fitScale: 1,
  zoom: 1,
  isProcessing: false,
};
let activePdfUrl = null;

// Wybiera tylko PDF-y i odświeża listę oczekujących dokumentów.
function updateSelectedFiles(fileList) {
  state.selectedFiles = Array.from(fileList).filter((file) => {
    return file.type === "application/pdf"
      || file.name.toLowerCase().endsWith(".pdf");
  });

  const processButton = $("#process");
  processButton.disabled = state.selectedFiles.length === 0 || state.isProcessing;
  $("#status").textContent = state.selectedFiles.length
    ? `${state.selectedFiles.length} PDF-y dodane. Rozpoczynam przetwarzanie…`
    : "Nie wybrano prawidłowych plików PDF.";

  renderSelectedFiles();
  if (state.selectedFiles.length && !state.isProcessing) {
    processFiles();
  }
}

// Zdjęcie plików z pola wyboru uruchamia tę samą walidację co drag & drop.
$("#files").addEventListener("change", (event) => {
  updateSelectedFiles(event.target.files);
});

// Obsługa przeciągnięcia plików na obszar uploadu.
const dropzone = $("#dropzone");
dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("dragging");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragging");
});

dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragging");
  updateSelectedFiles(event.dataTransfer.files);
});

dropzone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    $("#files").click();
  }
});

// Lista przed przetworzeniem informuje użytkownika, co zostanie wysłane.
function renderSelectedFiles() {
  if (!state.selectedFiles.length) {
    $("#documents").innerHTML = `
      <div class="panel-heading">
        <h2>Dokumenty</h2>
      </div>
      <p class="muted">Nie wybrano plików.</p>
    `;
    return;
  }

  $("#documents").innerHTML = `
    <div class="panel-heading">
      <h2>Dokumenty</h2>
      <span class="muted">${state.selectedFiles.length}</span>
    </div>
    ${state.selectedFiles.map((file) => `
      <div class="document-item ${state.isProcessing ? "processing" : "pending"}">
        <strong>${esc(file.name)}</strong>
        <span class="muted">${state.isProcessing ? "przetwarzanie…" : "gotowy do przetworzenia"}</span>
      </div>
    `).join("")}
  `;
}

// Wysyła wszystkie zaznaczone PDF-y jako pola multipart o nazwie "files".
async function processFiles() {
  if (!state.selectedFiles.length || state.isProcessing) {
    if (!state.selectedFiles.length) {
      $("#status").textContent = "Najpierw wybierz co najmniej jeden plik PDF.";
    }
    return;
  }

  state.isProcessing = true;
  const body = new FormData();

  for (const file of state.selectedFiles) {
    body.append("files", file);
  }

  $("#status").textContent =
    `Przetwarzanie ${state.selectedFiles.length} plik(ów)…`;
  const processButton = $("#process");
  processButton.disabled = true;
  processButton.textContent = "Przetwarzanie…";
  renderSelectedFiles();

  try {
    const response = await fetch("/api/process", {
      method: "POST",
      body,
    });

    if (!response.ok) {
      throw new Error(await response.text());
    }

    state.results = await response.json();
    await renderResults();
    $("#status").textContent = "Gotowe.";
  } catch (error) {
    $("#status").textContent =
      `Błąd przetwarzania: ${error.message}`;
  } finally {
    state.isProcessing = false;
    processButton.disabled = state.selectedFiles.length === 0;
    processButton.textContent = state.results.length ? "Przetwórz ponownie" : "Przetwórz PDF-y";
  }
}
// Rozpoczęcie przetwarzania przypisane do przycisku.
$("#process").addEventListener("click", processFiles);

// Wyniki API tworzą przyciski dokumentów z typem, pewnością i informacją o OCR.
async function renderResults() {
  $("#documents").innerHTML = `
    <div class="panel-heading">
      <h2>Dokumenty</h2>
      <span class="muted">${state.results.length}</span>
    </div>
    ${state.results.map((result, index) => `
      <button
        type="button"
        class="document-item ${result.doc_type ? "recognized" : "unknown"}"
        data-index="${index}"
      >
        <strong>${esc(result.filename)}</strong>
        <span>${esc(result.doc_type ?? "nierozpoznany")}</span>
        <span class="muted">
          pewność ${Number(result.confidence).toFixed(2)}
          ${result.scanned ? " · OCR" : ""}
        </span>
      </button>
    `).join("")}
  `;

  document.querySelectorAll(".document-item[data-index]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        selectResult(Number(button.dataset.index)).catch((error) => {
          $("#status").textContent = `Błąd otwierania dokumentu: ${error.message}`;
        });
      });
    });

  if (state.results.length) {
    await selectResult(0);
  }
}

// Wybór dokumentu synchronizuje listę, JSON, ślady pól i podgląd PDF.
async function selectResult(index) {
  if (!state.results[index]) return;
  state.activeResult = state.results[index];

  // API zachowuje kolejność plików; indeks działa także dla powtarzających się nazw.
  state.activeFile = state.selectedFiles[index]
    ?? state.selectedFiles.find((file) => file.name === state.activeResult.filename)
    ?? null;

  document.querySelectorAll(".document-item")
    .forEach((item) => item.classList.remove("active"));

  const selected = document.querySelector(
    `.document-item[data-index="${index}"]`
  );

  if (selected) {
    selected.classList.add("active");
  }

  renderJson(state.activeResult);
  renderFields(state.activeResult);

  if (state.activeFile) {
    await openPdf(state.activeFile);
  } else {
    clearPdfPreview();
  }
}

// Wyświetla wynikową strukturę JSON i przycisk pobrania.
function renderJson(result) {
  if (!result.data) {
    $("#json-panel").innerHTML =
      '<h2>Wynik JSON</h2><p class="muted">Nie wygenerowano JSON - dokument nie został rozpoznany lub wystąpił błąd.</p>';
    return;
  }

  const jsonText = JSON.stringify(result.data, null, 2);

  $("#json-panel").innerHTML = `
    <div class="panel-heading">
      <h2>Wynik JSON</h2>
      <button id="download-json" type="button">Pobierz JSON</button>
    </div>
    <pre id="json-output"><code>${esc(jsonText)}</code></pre>
  `;

  $("#download-json").addEventListener("click", () => {
    downloadJson(result);
  });
}

// Pobiera wynik bezpośrednio w przeglądarce jako plik .json.
function downloadJson(result) {
  const jsonText = JSON.stringify(result.data, null, 2);
  const blob = new Blob([jsonText], {
    type: "application/json",
  });

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");

  link.href = url;
  link.download =
    `${result.filename.replace(/\.pdf$/i, "")}.json`;

  document.body.appendChild(link);
  link.click();
  link.remove();

  URL.revokeObjectURL(url);
}

// Buduje tabelę śladów pól. Cały tekst pochodzący z API trafia przez esc().
function renderFields(result) {
  if (!result.fields || !result.fields.length) {
    $("#fields-panel").innerHTML =
      '<h2>Źródła pól</h2><p class="muted">Brak pól do pokazania.</p>';
    return;
  }

  $("#fields-panel").innerHTML = `
    <h2>Źródła pól</h2>
    <div class="fields-table-wrapper">
      <table>
        <thead>
          <tr>
            <th>Pole</th>
            <th>Wartość</th>
            <th>Metoda</th>
            <th>Pewność</th>
            <th>Źródło</th>
          </tr>
        </thead>
        <tbody>
          ${result.fields.map((field, index) => `
            <tr
              class="field-row"
              data-field-index="${index}"
              tabindex="0"
            >
              <td><code>${esc(field.path)}</code></td>
              <td>${field.value === null ? "null" : esc(field.value)}</td>
              <td>${esc(field.method ?? "brak")}</td>
              <td>${Number(field.confidence).toFixed(2)}</td>
              <td>
                ${field.evidence
                  ? `str. ${esc(field.evidence.page)}: ${esc(field.evidence.snippet)}`
                  : "Brak źródła"}
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;

  document.querySelectorAll(".field-row").forEach((row) => {
    row.addEventListener("click", () => {
      const fieldIndex = Number(row.dataset.fieldIndex);
      showEvidence(result.fields[fieldIndex]);
    });

    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        const fieldIndex = Number(row.dataset.fieldIndex);
        showEvidence(result.fields[fieldIndex]);
      }
    });
  });
}

// Kliknięcie pola przechodzi do strony, a jeśli dostępne są bbox, zaznacza fragment.
async function showEvidence(field) {
  if (!field.evidence || !field.evidence.page) {
    $("#status").textContent = "Brak danych o źródle tego pola.";
    return;
  }

  await goToPdfPage(field.evidence.page);

  if (!field.evidence.bbox) {
    $("#status").textContent =
      "Pole wskazuje stronę i fragment, ale nie ma współrzędnych zaznaczenia.";
    clearHighlights();
    return;
  }

  highlightEvidence(field.evidence.bbox);
  $("#status").textContent =
    `Źródło pola: strona ${field.evidence.page}.`;
}

// Otwiera PDF w PDF.js i zwalnia poprzedni, tymczasowy URL.
async function openPdf(file) {
  if (activePdfUrl) {
    URL.revokeObjectURL(activePdfUrl);
  }

  activePdfUrl = URL.createObjectURL(file);

  try {
    const pdf = await pdfjsLib.getDocument({
      url: activePdfUrl,
      cMapUrl: null,
    }).promise;
    state.pdfDocument = pdf;
    state.currentPage = 1;
    state.zoom = 1;

    await renderPdfPage(1);
  } catch (error) {
    clearPdfPreview();
    throw error;
  }
}

// Renderuje jedną stronę na canvasie i zapisuje viewport do późniejszego przeliczenia bbox.
async function renderPdfPage(pageNumber) {
  if (!state.pdfDocument) return;

  const page = await state.pdfDocument.getPage(pageNumber);
  const baseViewport = page.getViewport({ scale: 1 });
  const container = $("#pdf-pages");
  const horizontalPadding = 32;
  const verticalPadding = 32;
  const availableWidth = Math.max(200, container.clientWidth - horizontalPadding);
  const availableHeight = Math.max(300, container.clientHeight - verticalPadding);

  // Przy zoom 100% strona mieści się jednocześnie po szerokości i wysokości.
  state.fitScale = Math.min(
    availableWidth / baseViewport.width,
    availableHeight / baseViewport.height,
  );

  const viewport = page.getViewport({
    scale: state.fitScale * state.zoom,
  });
  state.currentViewport = viewport;

  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");

  canvas.width = viewport.width;
  canvas.height = viewport.height;

  // Warstwa pozycji musi otaczać canvas: highlight jest dzieckiem tego samego kontenera.
  const wrapper = document.createElement("div");
  wrapper.className = "pdf-page-wrap";
  wrapper.style.width = `${viewport.width}px`;
  wrapper.style.height = `${viewport.height}px`;
  wrapper.appendChild(canvas);

  await page.render({
    canvasContext: context,
    viewport,
  }).promise;

  $("#pdf-pages").innerHTML = "";
  $("#pdf-pages").appendChild(wrapper);

  state.currentPage = pageNumber;
  $("#page-label").textContent =
    `Strona ${pageNumber} z ${state.pdfDocument.numPages}`;

  $("#prev-page").disabled = pageNumber <= 1;
  $("#next-page").disabled =
    pageNumber >= state.pdfDocument.numPages;

  const zoomPercent = Math.round(state.zoom * 100);
  $("#zoom-reset").textContent = `${zoomPercent}%`;
  $("#zoom-out").disabled = state.zoom <= 0.5;
  $("#zoom-reset").disabled = false;
  $("#zoom-in").disabled = state.zoom >= 3;
}

// Przyciski nawigacji strony.
$("#prev-page").addEventListener("click", async () => {
  if (state.currentPage > 1) {
    await renderPdfPage(state.currentPage - 1);
  }
});

$("#next-page").addEventListener("click", async () => {
  if (
    state.pdfDocument
    && state.currentPage < state.pdfDocument.numPages
  ) {
    await renderPdfPage(state.currentPage + 1);
  }
});

// Przejście do strony wskazanej przez evidence; zakres strony jest sprawdzany.
async function goToPdfPage(pageNumber) {
  if (
    state.pdfDocument
    && pageNumber >= 1
    && pageNumber <= state.pdfDocument.numPages
  ) {
    await renderPdfPage(pageNumber);
  }
}

// Zmiana zoomu jest oddzielna od zoomu przeglądarki i działa przy każdym rozmiarze ekranu.
async function setPdfZoom(nextZoom) {
  if (!state.pdfDocument) return;
  state.zoom = Math.min(3, Math.max(0.5, nextZoom));
  await renderPdfPage(state.currentPage);
  $("#pdf-pages").scrollTo({ top: 0, left: 0, behavior: "smooth" });
}

$("#zoom-in").addEventListener("click", () => {
  setPdfZoom(state.zoom + 0.25).catch(() => {});
});

$("#zoom-out").addEventListener("click", () => {
  setPdfZoom(state.zoom - 0.25).catch(() => {});
});

$("#zoom-reset").addEventListener("click", () => {
  setPdfZoom(1).catch(() => {});
});

// Ctrl/Command + kółko myszki przybliża podgląd zamiast zmieniać całą stronę.
$("#pdf-pages").addEventListener("wheel", (event) => {
  if (!event.ctrlKey && !event.metaKey) return;
  event.preventDefault();
  const nextZoom = state.zoom + (event.deltaY < 0 ? 0.1 : -0.1);
  setPdfZoom(nextZoom).catch(() => {});
}, { passive: false });

// Zmiana szerokości okna i zoom przeglądarki zmieniają rozmiar kontenera.
// ResizeObserver ponownie dopasowuje stronę przy każdym poziomie własnego zoomu.
let resizeTimer = null;
const pdfResizeObserver = new ResizeObserver(() => {
  if (!state.pdfDocument) return;
  window.clearTimeout(resizeTimer);
  resizeTimer = window.setTimeout(() => {
    renderPdfPage(state.currentPage).catch(() => {});
  }, 100);
});
pdfResizeObserver.observe($("#pdf-pages"));

// Przelicza współrzędne PDF points na piksele aktualnego viewportu i rysuje zaznaczenie.
function highlightEvidence(bbox) {
  clearHighlights();
  if (!Array.isArray(bbox) || bbox.length !== 4 || !state.currentViewport) return;

  const [x0, top, x1, bottom] = bbox.map(Number);
  if (![x0, top, x1, bottom].every(Number.isFinite)) return;

  const start = state.currentViewport.convertToViewportPoint(x0, top);
  const end = state.currentViewport.convertToViewportPoint(x1, bottom);

  const highlight = document.createElement("div");
  highlight.className = "pdf-highlight";
  highlight.style.left = `${Math.min(start[0], end[0])}px`;
  highlight.style.top = `${Math.min(start[1], end[1])}px`;
  highlight.style.width = `${Math.abs(end[0] - start[0])}px`;
  highlight.style.height = `${Math.abs(end[1] - start[1])}px`;

  const wrapper = $(".pdf-page-wrap");
  if (wrapper) wrapper.appendChild(highlight);
}

// Usuwa zaznaczenia przy zmianie pola lub strony.
function clearHighlights() {
  document.querySelectorAll(".pdf-highlight").forEach((item) => item.remove());
}

// Resetuje podgląd po błędzie PDF albo przy zmianie dokumentu bez pliku.
function clearPdfPreview() {
  state.pdfDocument = null;
  state.currentViewport = null;
  state.currentPage = 1;
  $("#pdf-pages").innerHTML = "";
  $("#page-label").textContent = "Brak dokumentu";
  $("#prev-page").disabled = true;
  $("#next-page").disabled = true;
  $("#zoom-out").disabled = true;
  $("#zoom-reset").textContent = "100%";
  $("#zoom-reset").disabled = true;
  $("#zoom-in").disabled = true;
  clearHighlights();
}

// Tryb jasny/ciemny jest zapisywany lokalnie w przeglądarce.
const savedTheme = localStorage.getItem("theme") || "light";
document.documentElement.dataset.theme = savedTheme;

function updateThemeButton() {
  const theme = document.documentElement.dataset.theme;

  $("#theme-toggle").textContent =
    theme === "dark" ? "Jasny motyw" : "Ciemny motyw";
}

$("#theme-toggle").addEventListener("click", () => {
  const currentTheme = document.documentElement.dataset.theme;
  const nextTheme = currentTheme === "dark" ? "light" : "dark";

  document.documentElement.dataset.theme = nextTheme;
  localStorage.setItem("theme", nextTheme);
  updateThemeButton();
});

updateThemeButton();

// Sprawdza stan API przy wejściu na stronę i pokazuje tryb oraz liczbę klas.
async function loadHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const health = await response.json();
    $("#health").textContent =
      `tryb: ${health.mode} · LLM: ${health.llm_model ?? "brak"} · klasy: ${health.doc_types.join(", ")}`;
  } catch {
    $("#health").textContent = "API niedostępne";
  }
}

loadHealth();
