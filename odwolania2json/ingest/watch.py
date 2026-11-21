from __future__ import annotations

import argparse
import hashlib
import re
import time
from collections.abc import Callable
from pathlib import Path

from odwolania2json.ingest.pdf import load_document
from odwolania2json.output.writer import write_result
from odwolania2json.pipeline import Pipeline
from odwolania2json.settings import Settings


def pdf_signature(pdf: Path) -> str:
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    return f"{digest}:{pdf.stat().st_mtime_ns}"


def _polish_date(text: str) -> str | None:
    iso = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if iso:
        year, month, day = map(int, iso.groups())
        try:
            import datetime
            return datetime.date(year, month, day).isoformat()
        except ValueError:
            return None
    match = re.search(
        r"\b(\d{1,2})\s+(stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|"
        r"września|października|listopada|grudnia)\s+(\d{4})",
        text,
        re.I,
    )
    if not match:
        match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})", text)
        if not match:
            return None
        day, month, year = map(int, match.groups())
    else:
        months = {
            "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4,
            "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
            "września": 9, "października": 10, "listopada": 11,
            "grudnia": 12,
        }
        day = int(match.group(1))
        month = months[match.group(2).lower()]
        year = int(match.group(3))
    try:
        import datetime
        return datetime.date(year, month, day).isoformat()
    except ValueError:
        return None


def _section(text: str, start: str, end: str) -> str | None:
    start_match = re.search(start, text, re.I)
    if not start_match:
        return None
    end_match = re.search(end, text[start_match.end():], re.I)
    finish = start_match.end() + end_match.start() if end_match else len(text)
    return text[start_match.end():finish]


def _label_value(text: str, *labels: str) -> str | None:
    lines = text.splitlines()
    for label in labels:
        pattern = re.compile(rf"^\s*{re.escape(label)}\s*[:\-]?\s*(.*?)\s*$", re.I)
        for index, line in enumerate(lines):
            match = pattern.match(line)
            if not match:
                continue
            value = match.group(1).strip()
            if value:
                return value
            for next_line in lines[index + 1:index + 4]:
                if next_line.strip():
                    return next_line.strip()
    return None


def fill_canonical_fields(doc_type: str, data: dict, text: str) -> dict:
    if doc_type == "odwolanie":
        od = data.setdefault("OdwolanieKIO", {})
        applicant = data.setdefault("Odwolujacy", [{}])
        contractor = data.setdefault("Zamawiajacy", [{}])
        od_applicant = applicant[0]
        od_contractor = contractor[0]

        od_section = _section(
            text,
            r"(?m)^Odwołujący\s*:",
            r"(?im)^(Zamawiający\s*:|Wykonawca wybrany|dotyczy\s*:|ODWOŁANIE)",
        )
        contractor_section = _section(
            text,
            r"(?m)^Zamawiający\s*:",
            r"(?im)^(Odwołujący\s*:|Wykonawca wybrany|dotyczy\s*:|ODWOŁANIE)",
        )

        for target, block in ((od_applicant, od_section), (od_contractor, contractor_section)):
            if not block:
                continue
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            name = next(
                (
                    line for line in lines
                    if not re.match(r"^(ul\.|KRS|NIP|REGON|tel\.|e-mail|adres|reprezent)", line, re.I)
                ),
                None,
            )
            if name and target.get("nazwa") is None:
                target["nazwa"] = re.split(r",?\s*(?:ul\.|KRS|NIP|REGON)\b", name, maxsplit=1)[0].strip(" ,")
            for field_name, pattern in (
                ("krs", r"\bKRS\s*:?\s*(\d{10})"),
                ("nip", r"\bNIP\s*:?\s*(\d{10})"),
                ("regon", r"\bREGON\s*:?\s*(\d{9}|\d{14})"),
            ):
                match = re.search(pattern, block, re.I)
                if match and target.get(field_name) is None:
                    target[field_name] = match.group(1)

        if od_applicant:
            attorney = od_applicant.setdefault("pelnomocnik", {})
            representative = re.search(
                r"reprezentowan[a-z]*\s+przez\s*:?\s*(.+)",
                od_section or "",
                re.I,
            )
            if representative and attorney.get("imie_nazwisko") is None:
                match = re.search(
                    r"(?:r\.?\s*pr\.?|adw\.?|mec\.?)\s*"
                    r"([A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+\s+[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+)",
                    representative.group(1),
                    re.I,
                )
                if match:
                    attorney["imie_nazwisko"] = match.group(1)
                    od_applicant["przez-pelnomocnika"] = 1

            office = re.search(r"adres do korespondencji:\s*([^\n]+)", od_section or "", re.I)
            if office and attorney.get("nazwa") is None:
                attorney["nazwa"] = re.split(r",?\s*ul\.", office.group(1), maxsplit=1)[0].strip(" ,")
            mail = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", od_section or "")
            if mail and attorney.get("mail") is None:
                attorney["mail"] = mail.group(0)

        first_zarzut = re.search(r"(?is)Zarzut nr 1(.*?)(?:Zarzut nr 2|Zaskarż|$)", text)
        zarzut = first_zarzut.group(1) if first_zarzut else text
        if od.get("czynnosc_powod_id") is None:
            if re.search(r"(?i)zaniechani\w* odrzucenia", zarzut):
                od["czynnosc_powod_id"] = 7
                od["czynnosc_opis"] = "Zaniechanie odrzucenia oferty"
            elif re.search(r"(?i)zaniechani\w* wykluczenia", zarzut):
                od["czynnosc_powod_id"] = 8
                od["czynnosc_opis"] = "Zaniechanie wykluczenia wykonawcy"
            elif re.search(r"(?i)unieważni\w* postępowani\w*", zarzut):
                od["czynnosc_powod_id"] = 9
                od["czynnosc_opis"] = "Decyzja o unieważnieniu postępowania"
        if od.get("czynnosc_dzien") is None:
            date_match = re.search(
                r"(?is).{0,500}?Zarzut nr 1",
                text,
            )
            context = date_match.group(0) if date_match else text[:2500]
            od["czynnosc_dzien"] = _polish_date(context)

    elif doc_type == "przelew":
        transfer = data.setdefault("przelew_bankowy", {})
        transaction_code = re.search(r"\b(CEN\d{10,})\b", text)
        if transaction_code:
            transfer["numer_transakcji"] = transaction_code.group(1)
        if transfer.get("kwota") is None:
            dots_amount = re.search(
                r"(?i)KWOTA(?: PRZELEWU)?\s*[.:.]*\s*[^\d\n]*"
                r"(\d[\d\s]*[.,]\d{2})\s*(?:PLN|ZŁ)",
                text,
            )
            if dots_amount:
                number = dots_amount.group(1).replace(" ", "").replace(",", ".")
                try:
                    transfer["kwota"] = float(number)
                except ValueError:
                    transfer["kwota"] = None
        if transfer.get("data_operacji") is None:
            iso_date = re.search(r"\b(\d{4}-\d{1,2}-\d{1,2})\b", text)
            if iso_date:
                transfer["data_operacji"] = _polish_date(iso_date.group(1))
            table_date = re.search(
                r"(?is)Data(?: operacji| transakcji| wykonania| realizacji)"
                r"\s*\n?\s*(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})",
                text,
            )
            if table_date:
                transfer["data_operacji"] = _polish_date(table_date.group(1))
        date_match = re.search(
            r"(?im)^\s*(?:Data operacji|Data transakcji|Data wykonania|Data realizacji)"
            r"\s*[:\-]?\s*(.+?)\s*$",
            text,
        )
        if transfer.get("data_operacji") is None and date_match:
            transfer["data_operacji"] = _polish_date(date_match.group(1))

        amount_match = re.search(
            r"(?im)^\s*(?:Kwota operacji|Kwota przelewu|Kwota)"
            r"\s*[:\-]?\s*([+-]?\d[\d\s]*(?:[.,]\d+)*)",
            text,
        )
        if transfer.get("kwota") is None and amount_match:
            number = amount_match.group(1).replace(" ", "")
            number = number.replace(".", "").replace(",", ".") if "," in number else number
            try:
                transfer["kwota"] = float(number)
            except ValueError:
                transfer["kwota"] = None
        if transfer.get("kwota") is None:
            table_amount = re.search(
                r"(?is)Kwota(?: operacji| przelewu)?\s*(?:Data transakcji\s*)?\n?"
                r"\s*([+-]?\d[\d\s]*(?:[.,]\d+)*)\s*(?:PLN|zł)",
                text,
            )
            if table_amount:
                number = table_amount.group(1).replace(" ", "")
                number = number.replace(".", "").replace(",", ".")
                try:
                    transfer["kwota"] = float(number)
                except ValueError:
                    transfer["kwota"] = None

        account_matches = (
            (
                "z_rachunku",
                r"(?im)^\s*(?:Rachunek zleceniodawcy|Rachunek nadawcy|Rachunek obciążany)"
                r"\b\s*[:\-]?\s*(.+?)\s*$",
            ),
            (
                "na_rachunek",
                r"(?im)^\s*(?:Rachunek odbiorcy|Rachunek beneficjenta|Rachunek uznawany|Na rachunek)"
                r"\b\s*[:\-]?\s*(.+?)\s*$",
            ),
        )
        for field_name, pattern in account_matches:
            if transfer.get(field_name) is not None:
                continue
            account_match = re.search(pattern, text)
            if account_match:
                digits = re.sub(r"\D", "", account_match.group(1))
                transfer[field_name] = digits if len(digits) == 26 else None
        if transfer.get("z_rachunku") is None:
            nadawca_account = re.search(
                r"(?is)RACHUNEK NADAWCY.{0,100}?(?:PL)?O?(\d{26})",
                text,
            )
            if nadawca_account:
                transfer["z_rachunku"] = nadawca_account.group(1)
            first_account = re.search(
                r"Rachunek obciążany.{0,100}?PL(\d{26})",
                text,
                re.I | re.S,
            )
            if first_account:
                transfer["z_rachunku"] = first_account.group(1)
        if transfer.get("na_rachunek") is None:
            odbiorca_account = re.search(
                r"(?is)RACHUNEK ODBIORCY.{0,100}?(?:PL)?O?(\d{26})",
                text,
            )
            if odbiorca_account:
                transfer["na_rachunek"] = odbiorca_account.group(1)
            second_account = re.search(
                r"Rachunek odbiorcy.{0,100}?PL(\d{26})",
                text,
                re.I | re.S,
            )
            if second_account:
                transfer["na_rachunek"] = second_account.group(1)

        if transfer.get("numer_transakcji") is None:
            transaction_match = re.search(
                r"(?im)^\s*(?:Numer referencyjny|Nr ref\.?|Identyfikator operacji|"
                r"Numer transakcji operacji|Numer transakcji|ID transakcji)"
                r"\s*[:\-]?\s*(.+?)\s*$",
                text,
            )
            if transaction_match:
                transfer["numer_transakcji"] = transaction_match.group(1).strip()
        transaction_value = str(transfer.get("numer_transakcji") or "")
        if transfer.get("numer_transakcji") is None or not re.search(
            r"\bCEN\d{10,}\b",
            transaction_value,
        ):
            transaction_code = re.search(r"\b(CEN\d{10,})\b", text)
            if transaction_code:
                transfer["numer_transakcji"] = transaction_code.group(1)

        if transfer.get("tytul") is None:
            title_match = re.search(
                r"(?im)^\s*(?:Tytuł przelewu|Tytułem|Tytuł|Opis)\s*[:\-]?\s*(.*)$",
                text,
            )
            if title_match:
                title_parts = [title_match.group(1).strip()]
                start = title_match.end()
                for line in text[start:].splitlines():
                    if re.match(r"(?i)^\s*(?:Numer|Nr|Status|Opłata|Potwierdzenie)\b", line):
                        break
                    if line.strip():
                        title_parts.append(line.strip())
                title = " ".join(part for part in title_parts if part)
                title = re.sub(r"\s+", " ", title).strip()
                transfer["tytul"] = title or None

    elif doc_type == "pelnomocnictwo":
        principals = data.setdefault("mocodawca", [{}])
        representatives = data.setdefault("pelnomocnicy", [])
        principal = principals[0]
        principal_data = principal.setdefault("data", {})
        principal_data.pop("regon", None)
        principal_data.setdefault("imie_nazwisko", None)
        acting_name = re.search(
            r"(?is)Działając w imieniu\s+([^(\n]+)",
            text,
        )
        if acting_name and principal_data.get("nazwa") is None:
            principal_data["nazwa"] = acting_name.group(1).strip(" ,")
        match = re.search(
            r"(?im)Mocodawc[ayą]\s+(?:jest\s+)?([^,\n]+)",
            text,
        )
        if match and match.group(1).lower().startswith("jest"):
            match = None
        stated_name = re.search(
            r"(?im)(?:Mocodawc[ayą]\s+jest)\s+([^,\n]+)",
            text,
        )
        if stated_name:
            match = stated_name
        if not stated_name:
            match = re.search(
                r"(?im)^([A-ZŁŚŻŹĆŃÓ][^\n]{2,}?)\s*,\s*ul\.\s+"
                r"[^\n]*?(?:KRS\s*:?\s*(\d{10}))?",
                text,
            )
        if match and principal_data.get("nazwa") is None:
            name = match.group(1).strip(" ,")
            name = re.sub(r"(?i)^jest\s+", "", name).strip()
            principal_data["nazwa"] = name
        for field_name, pattern in (
            ("krs", r"\bKRS\s*:?\s*(\d{10})"),
            ("nip", r"\bNIP\s*:?\s*(\d{10})"),
            ("regon", r"\bREGON\s*:?\s*(\d{9}|\d{14})"),
        ):
            found = re.search(pattern, text, re.I)
            if found and principal_data.get(field_name) is None:
                principal_data[field_name] = found.group(1)
        if principal_data.get("typ") is None:
            principal_data["typ"] = "osoba_prawna"

        accept_match = re.search(
            r"(?is)Pełnomocnictwo przyjmuję:(.*)$",
            text,
        )
        accepted = accept_match.group(1) if accept_match else text
        attorney_names = re.findall(
            r"(?:r\.?\s*pr\.?|adw\.?|mec\.?)\s+"
            r"([A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+(?:-[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+)?"
            r"\s+[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+)",
            accepted,
            re.I,
        )
        unique_names = list(dict.fromkeys(attorney_names))
        representatives.clear()
        representatives.extend({"imie_nazwisko": name, "pesel": None} for name in unique_names)

        signature_block = _section(
            text,
            r"(?im)^Mocodawca\s*:",
            r"(?im)^Pełnomocnictwo przyjmuję\s*:",
        )
        if signature_block:
            signers = re.findall(
                r"(?m)^([A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+(?:-[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+)?"
                r"\s+[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+)\s*$",
                signature_block,
            )
            signers = [
                signer for signer in signers
                if not re.search(r"(?i)\b(prokurent|zarząd|zarządu|prezes|dyrektor|członek)\b", signer)
            ]
            principal["podpisujacy"] = [
                {"imie_nazwisko": signer} for signer in signers
            ]
        if principal.get("podpisujacy") and all(
            not signer.get("imie_nazwisko")
            for signer in principal["podpisujacy"]
        ):
            principal["podpisujacy"] = []
        if not principal.get("podpisujacy"):
            inline_signer = re.search(
                r"(?im)^([A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+\s+"
                r"[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+)\s+(?:r\.?\s*pr\.?|Prezes Zarządu)",
                text,
            )
            if inline_signer:
                principal["podpisujacy"] = [
                    {"imie_nazwisko": inline_signer.group(1)}
                ]
        if not principal.get("podpisujacy"):
            role_signer = re.search(
                r"(?im)^([A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+\s+[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+)"
                r"\s*\n\s*(?:Prezes Zarządu|Dyrektor|Prokurent|Członek Zarządu)",
                text,
            )
            if role_signer:
                principal["podpisujacy"] = [
                    {"imie_nazwisko": role_signer.group(1)}
                ]
        if not principal.get("podpisujacy"):
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            roles = ("Prokurent", "Dyrektor", "Członek Zarządu", "Prezes Zarządu")
            for index, line in enumerate(lines):
                if not any(role.lower() in line.lower() for role in roles):
                    continue
                for previous in reversed(lines[max(0, index - 4):index]):
                    if re.fullmatch(
                        r"[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+(?:-[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+)? "
                        r"[A-ZŁŚŻŹĆŃÓ][a-ząćęłńóśźż]+",
                        previous,
                    ):
                        principal["podpisujacy"] = [
                            {"imie_nazwisko": previous}
                        ]
                        break
                if principal.get("podpisujacy"):
                    break
        principal_data.pop("regon", None)

    return data


def process_input_folder(
    input_dir: str | Path = "input",
    output_dir: str | Path = "output",
    traces_dir: str | Path | None = None,
    on_result: Callable[[str], None] | None = None,
) -> dict[Path, str]:
    input_root = Path(input_dir)
    output_root = Path(output_dir)
    processed: dict[Path, str] = {}
    settings = Settings.load()
    pipeline = Pipeline(settings)
    if traces_dir is None:
        traces_dir = settings.output.traces_dir

    for pdf in sorted(input_root.rglob("*")):
        if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
            continue
        try:
            result = pipeline.process_file(pdf)
            document = load_document(pdf, settings.ingest)
            if result.data is not None:
                result.data = fill_canonical_fields(
                    result.classification.doc_type or "",
                    result.data,
                    document.text,
                )
            relative = pdf.resolve().relative_to(input_root.resolve())
            json_path = output_root / relative.with_suffix(".json")
            write_result(
                json_path,
                result.data,
                settings.output.unknown_output,
                settings.output.indent,
            )
            if traces_dir:
                trace_path = Path(traces_dir) / relative.with_suffix(".trace.json")
                trace_path.parent.mkdir(parents=True, exist_ok=True)
                trace_path.write_text(
                    __import__("json").dumps(result.to_trace(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            processed[pdf.resolve()] = pdf_signature(pdf)
            if on_result:
                on_result(str(json_path))
        except Exception as exc:
            if on_result:
                on_result(f"ERROR {pdf}: {exc}")
    return processed


def watch_input_folder(
    input_dir: str | Path = "input",
    output_dir: str | Path = "output",
    traces_dir: str | Path | None = None,
    poll_seconds: float = 1.0,
) -> None:
    input_root = Path(input_dir)
    input_root.mkdir(parents=True, exist_ok=True)
    seen = process_input_folder(input_dir, output_dir, traces_dir, print)
    print(f"Watching {input_root.resolve()} for PDF changes...")

    while True:
        current: dict[Path, str] = {}
        for pdf in sorted(input_root.rglob("*")):
            if pdf.is_file() and pdf.suffix.lower() == ".pdf":
                resolved = pdf.resolve()
                try:
                    current[resolved] = pdf_signature(pdf)
                except (FileNotFoundError, PermissionError):
                    continue

        changed = [
            pdf
            for pdf, signature in current.items()
            if seen.get(pdf) != signature
        ]
        removed = set(seen) - set(current)
        for pdf in removed:
            relative = pdf.relative_to(input_root.resolve())
            (Path(output_dir) / relative.with_suffix(".json")).unlink(missing_ok=True)
            seen.pop(pdf, None)

        if changed:
            process_input_folder(input_dir, output_dir, traces_dir, print)
            seen = current

        time.sleep(max(0.1, poll_seconds))


def main() -> None:
    parser = argparse.ArgumentParser(description="Automatically process PDFs added to input/")
    parser.add_argument("-i", "--input", default="input")
    parser.add_argument("-o", "--output", default="output")
    parser.add_argument("-t", "--traces", default=None)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    if args.once:
        process_input_folder(args.input, args.output, args.traces, print)
    else:
        watch_input_folder(args.input, args.output, args.traces, args.poll_seconds)


if __name__ == "__main__":
    main()
