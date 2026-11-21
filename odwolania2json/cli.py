"""Command line interface.

python -m odwolania2json run                       # ./input → ./output  (also: ./start.sh, start.bat)
python -m odwolania2json classify plik.pdf ...     # debug: classification scores
python -m odwolania2json inspect plik.pdf          # debug: text of each page (after OCR)
python -m odwolania2json doc-types                 # configured document classes + config check
python -m odwolania2json schema przelew            # empty JSON template / --json-schema
python -m odwolania2json web                       # web UI on http://localhost:8000
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Odwołania2JSON — PDF (KIO) → JSON")
console = Console()


def _setup_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s"
    )


@app.command()
def run(
    input: Path = typer.Option(Path("input"), "--input", "-i", help="Folder z PDF (podfoldery = sprawy)"),
    output: Path = typer.Option(Path("output"), "--output", "-o", help="Folder wynikowy (drzewo 1:1)"),
    traces: Path | None = typer.Option(None, help="Folder śladów ekstrakcji (domyślnie z settings.yaml)"),
    mode: str | None = typer.Option(None, help="rules | llm | hybrid (nadpisuje ustawienia)"),
    limit: int | None = typer.Option(None, help="Przetwórz tylko N pierwszych plików"),
):
    """Przetwarza wszystkie PDF z INPUT i zapisuje JSON-y do OUTPUT."""
    _setup_logging()
    from odwolania2json.pipeline import Pipeline, find_pdfs
    from odwolania2json.settings import Settings

    settings = Settings.load()
    if mode:
        settings.extraction.mode = mode  # type: ignore[assignment]
    pipe = Pipeline(settings)
    total = len(find_pdfs(input)[:limit])
    llm_info = settings.llm.model if pipe.llm else "brak (tylko reguły)"
    console.print(
        f"[bold]Odwołania2JSON[/] · tryb: {settings.extraction.mode} · LLM: {llm_info} · plików: {total}"
    )

    def show(res):
        c = res.classification
        status = f"[green]{c.doc_type}[/]" if c.doc_type else "[yellow]pominięty (nierozpoznany)[/]"
        extra = " [dim](OCR)[/]" if res.scanned else ""
        err = f" [red]{'; '.join(res.errors)}[/]" if res.errors else ""
        console.print(
            f"  {res.source.relative_to(input)} → {status} ({c.confidence:.2f}, {c.method}){extra}{err}"
        )

    results = pipe.run(input, output, traces, limit, on_result=show)
    written = sum(1 for r in results if r.data is not None)
    console.print(f"[bold green]Gotowe:[/] {written}/{len(results)} JSON → {output}/")


@app.command()
def classify(pdfs: list[Path] = typer.Argument(..., help="Pliki PDF")):
    """Pokazuje wyniki klasyfikacji (punkty dla każdej klasy)."""
    _setup_logging()
    from odwolania2json.classify import classify as do_classify
    from odwolania2json.ingest import load_document
    from odwolania2json.llm import LLMClient
    from odwolania2json.settings import Settings
    from odwolania2json.spec import load_registry

    settings = Settings.load()
    registry = load_registry(settings.config_dir)
    llm = LLMClient.from_settings(settings.llm)
    table = Table("plik", "klasa", "pewność", "metoda", *registry.doc_types, "uzasadnienie")
    for pdf in pdfs:
        res = do_classify(load_document(pdf, settings.ingest), registry, settings, llm)
        scores = [f"{res.scores.get(k, 0):g}" for k in registry.doc_types]
        table.add_row(pdf.name, str(res.doc_type), f"{res.confidence:.2f}", res.method, *scores, res.reason)
    console.print(table)


@app.command()
def inspect(pdf: Path, max_chars: int = typer.Option(3000, help="Ile znaków na stronę pokazać")):
    """Pokazuje tekst każdej strony (warstwa tekstowa lub OCR)."""
    _setup_logging()
    from odwolania2json.ingest import load_document
    from odwolania2json.settings import Settings

    doc = load_document(pdf, Settings.load().ingest)
    for p in doc.pages:
        console.rule(f"Strona {p.number} · źródło: {p.source}{' / ' + p.ocr_engine if p.ocr_engine else ''}")
        console.print(p.text[:max_chars], markup=False, highlight=False)


@app.command("doc-types")
def doc_types():
    """Lista skonfigurowanych klas dokumentów + walidacja konfiguracji YAML."""
    from odwolania2json.spec import load_registry

    registry = load_registry()
    table = Table("id", "nazwa", "pola", "sygnały", "plik")
    for dt in registry.doc_types.values():
        table.add_row(
            dt.id, dt.name, str(len(dt.leaf_specs())), str(len(dt.classification.signals)), f"{dt.id}.yaml"
        )
    console.print(table)
    problems = registry.check()
    if problems:
        console.print("[red]Problemy w konfiguracji:[/]")
        for p in problems:
            console.print(f"  • {p}")
        raise typer.Exit(1)
    console.print("[green]Konfiguracja poprawna.[/]")


@app.command()
def schema(doc_type: str, json_schema: bool = typer.Option(False, "--json-schema", help="Pokaż JSON Schema")):
    """Pusty szablon JSON danej klasy (albo JSON Schema przekazywany do LLM)."""
    from odwolania2json.spec import load_registry

    registry = load_registry()
    spec = registry.doc_types[doc_type]
    data = spec.json_schema(registry.dictionaries) if json_schema else spec.empty_output()
    console.print_json(json.dumps(data, ensure_ascii=False))


@app.command()
def web(host: str = "127.0.0.1", port: int = 8000):
    """Uruchamia interfejs webowy (wgranie PDF → typ, JSON, źródła wartości)."""
    _setup_logging()
    import uvicorn

    console.print(f"[bold]UI:[/] http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}")
    uvicorn.run("odwolania2json.api.app:app", host=host, port=port)
