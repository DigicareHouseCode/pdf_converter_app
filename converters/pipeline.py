"""
Page orchestration shared by every conversion mode.

Two extraction strategies live here:

* text layer  — for PDFs that already contain their characters. pdfplumber
                returns them exactly, so there is nothing for a vision model
                to add and no OCR error to introduce.
* vision      — for scans, and for digital pages whose table structure defeats
                table detection.

The `selectable` mode used to rasterise pages and OCR them even though the
text was right there in the file. It now tries the text layer first and only
falls back to vision per page.
"""

import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pdfplumber

from config import Config
from converters import common

log = logging.getLogger(__name__)


# ------------------------------------------------------------- text layer ---

_HEADER_PATTERNS = {
    "Date": re.compile(r"^\s*(date|posted|posting|trans(action)?\s*date|value\s*date)\s*$", re.I),
    "Description": re.compile(r"^\s*(description|details?|narration|particulars|memo|transaction|reference)\s*$", re.I),
    "Debit": re.compile(r"^\s*(debits?|withdrawals?|payments?|money\s*out|paid\s*out|charges?)\s*$", re.I),
    "Credit": re.compile(r"^\s*(credits?|deposits?|additions?|money\s*in|paid\s*in)\s*$", re.I),
    "Balance": re.compile(r"^\s*(balance|running\s*balance|closing\s*balance)\s*$", re.I),
    "Amount": re.compile(r"^\s*(amount|value)\s*$", re.I),
}

_SUMMARY_ROW = re.compile(
    r"\b(beginning|opening|ending|closing|total|subtotal|balance\s+forward|carried\s+forward)\b",
    re.I,
)

_DATE_LIKE = re.compile(r"^\s*\d{1,4}[/\-. ]\d{1,2}([/\-. ]\d{2,4})?\s*$|^\s*\d{1,2}\s+[A-Za-z]{3,9}\s*\d{0,4}\s*$")


def _cell(value):
    """Flatten a table cell, merging wrapped lines into one string."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _map_header(cells):
    """
    Map column index -> canonical field name, if this row looks like a header.

    Returns None when the row is not a header.
    """
    mapping = {}
    for index, raw in enumerate(cells):
        text = _cell(raw)
        if not text:
            continue
        for field, pattern in _HEADER_PATTERNS.items():
            if pattern.match(text):
                mapping.setdefault(index, field)
                break

    fields = set(mapping.values())
    # A usable header needs a date and at least one money column.
    if "Date" in fields and fields & {"Debit", "Credit", "Amount", "Balance"}:
        return mapping
    return None


def rows_from_table(table, page_no):
    """Convert one pdfplumber table into canonical rows."""
    mapping = None
    rows = []

    for cells in table:
        if not cells:
            continue

        if mapping is None:
            mapping = _map_header(cells)
            continue  # header row itself is never data

        values = {}
        for index, raw in enumerate(cells):
            field = mapping.get(index)
            if field:
                values.setdefault(field, _cell(raw))

        date = values.get("Date", "")
        description = values.get("Description", "")

        if not date and not description:
            continue

        # Summary lines must be recognised before the continuation check below,
        # otherwise a dateless "Total Debits" is absorbed into the row above it.
        if _SUMMARY_ROW.search(description) and not values.get("Debit") and not values.get("Credit"):
            continue

        # A row with no date is a wrapped description belonging to the row above.
        if not date and rows:
            if description:
                rows[-1]["Description"] = f"{rows[-1]['Description']} {description}".strip()
            continue

        debit = common.as_amount(values.get("Debit"))
        credit = common.as_amount(values.get("Credit"))
        balance = common.as_amount(values.get("Balance"))

        if debit is None and credit is None:
            amount = common.as_amount(values.get("Amount"))
            if amount is not None:
                if amount < 0:
                    debit = abs(amount)
                else:
                    credit = abs(amount)

        if debit is None and credit is None and balance is None:
            continue

        rows.append({
            "Date": date,
            "Description": description,
            "Debit": abs(debit) if debit is not None else None,
            "Credit": abs(credit) if credit is not None else None,
            "Balance": balance,
            "_page": page_no,
            "_row": len(rows),
            "_flags": [],
        })

    return rows


def extract_page_text_layer(page, page_no):
    """
    Pull transactions straight out of the page's text layer.

    Returns [] when the page has no usable table, which is the caller's signal
    to fall back to vision for that page only.
    """
    try:
        text = page.extract_text() or ""
    except Exception:
        return []

    # A scanned page has a text layer of roughly nothing.
    if len(text.strip()) < 40:
        return []

    strategies = (
        {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
        {"vertical_strategy": "text", "horizontal_strategy": "text"},
    )

    for settings in strategies:
        try:
            tables = page.extract_tables(settings) or []
        except Exception:
            continue

        for table in tables:
            rows = rows_from_table(table, page_no)
            if len(rows) >= 2:  # one row is more likely a false positive
                log.info("Page %s: %d rows from text layer", page_no, len(rows))
                return rows

    return []


# ----------------------------------------------------------------- vision ---

def extract_page_vision(page, page_no, model, audit=True, prompt=None):
    """Rasterise one page and read it with the model, then audit the result."""
    try:
        image = common.render_page(page)
    except Exception as exc:
        log.error("Page %s could not be rasterised: %s", page_no, exc)
        return [], []

    rejected = []
    text = common.generate(model, [prompt or common.PROMPT_TRANSACTIONS, image])
    items = common.parse_json_rows(text)
    rows = common.normalise_rows(items, page_no, rejected)

    # Second pass: the model re-reads the page and corrects its own output.
    # This used to run only on the mode that needed it least.
    if audit and rows:
        import json
        payload = json.dumps(
            [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows],
            indent=1, default=str,
        )
        audited_text = common.generate(model, [common.PROMPT_AUDIT.format(payload=payload), image])
        audited_items = common.parse_json_rows(audited_text)
        if audited_items:
            audited_rows = common.normalise_rows(audited_items, page_no, rejected)
            if audited_rows:
                log.info("Page %s: audit %d -> %d rows", page_no, len(rows), len(audited_rows))
                rows = audited_rows

    return rows, rejected


# ------------------------------------------------------------------- run ----

def _select_pages(pdf, max_pages):
    """Apply the plan page limit and the global cost ceiling."""
    limit = Config.MAX_PAGES_PER_CONVERSION
    if max_pages:
        limit = min(limit, max_pages)
    pages = pdf.pages[:limit]
    return pages, len(pdf.pages)


def convert(pdf_path, excel_path, api_key, mode="general",
            max_pages=None, progress_callback=None, prompt=None):
    """
    Run a conversion end to end.

    mode:
      selectable — text layer first, vision per page only where it fails
      scanned    — vision only, with the audit pass
      general    — text layer first, vision fallback (same as selectable)
      custom     — vision with a user-supplied prompt, no audit

    Returns (success: bool, message: str, summary: dict | None).
    """
    if not os.path.exists(pdf_path):
        return False, f"File not found: {os.path.basename(pdf_path)}", None

    use_text_layer = mode in ("selectable", "general")
    audit = mode in ("scanned", "general", "selectable")
    json_mode = True

    def report(percent, message):
        if progress_callback:
            try:
                progress_callback(percent, message)
            except Exception:
                pass

    all_rows = []
    rejected = []
    model = None
    lock = threading.Lock()

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages, total_in_file = _select_pages(pdf, max_pages)
            total = len(pages)
            if total == 0:
                return False, "The PDF has no readable pages.", None

            year_hint = common.statement_year(pdf)

            # Pass 1: the text layer is free and exact, so always try it first.
            vision_needed = list(range(total))
            if use_text_layer:
                report(5, "Reading the document's text layer…")
                vision_needed = []
                for index, page in enumerate(pages):
                    rows = extract_page_text_layer(page, index + 1)
                    if rows:
                        all_rows.extend(rows)
                    else:
                        vision_needed.append(index)

                if not vision_needed:
                    log.info("All %d pages read from the text layer; no model calls made.", total)
                    report(90, "Building your workbook…")

            # Pass 2: vision for whatever is left.
            if vision_needed:
                if not api_key:
                    if all_rows:
                        log.warning("No API key; %d pages skipped.", len(vision_needed))
                    else:
                        return False, "GEMINI_API_KEY is not configured.", None
                else:
                    try:
                        model = common.build_model(api_key, json_mode=json_mode)
                    except common.ModelUnavailable as exc:
                        return False, str(exc), None
                    except ValueError as exc:
                        return False, str(exc), None

                    done = [0]

                    def work(index):
                        rows, page_rejected = extract_page_vision(
                            pages[index], index + 1, model, audit=audit, prompt=prompt
                        )
                        with lock:
                            all_rows.extend(rows)
                            rejected.extend(page_rejected)
                            done[0] += 1
                            report(
                                5 + (done[0] / len(vision_needed)) * 85,
                                f"Read page {done[0]} of {len(vision_needed)}",
                            )

                    workers = max(1, min(Config.EXTRACTION_WORKERS, len(vision_needed)))
                    with ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = [pool.submit(work, i) for i in vision_needed]
                        for future in as_completed(futures):
                            try:
                                future.result()
                            except common.ModelUnavailable as exc:
                                return False, str(exc), None
                            except Exception as exc:
                                log.error("Page task failed: %s", exc)

    except Exception as exc:
        log.exception("PDF processing failed")
        return False, f"Could not read the PDF: {exc}", None

    if not all_rows:
        return False, "No transactions could be read from this document.", None

    report(92, "Checking the running balance…")

    # Order first — the reconciliation below only means anything in statement order.
    rows = common.order_rows(all_rows)
    rows = common.resolve_dates(rows, year_hint)
    checked, flagged = common.reconcile(rows)
    summary = common.summarise(rows, checked, flagged, len(rejected))

    report(96, "Writing your workbook…")
    try:
        common.write_workbook(rows, excel_path, summary)
    except Exception as exc:
        log.exception("Workbook write failed")
        return False, f"Could not write the Excel file: {exc}", None

    if total_in_file > len(rows) and total_in_file > Config.MAX_PAGES_PER_CONVERSION:
        log.warning("Document had %d pages; processed %d.", total_in_file, Config.MAX_PAGES_PER_CONVERSION)

    report(100, "Done")
    return True, common.result_message(summary), summary


def convert_custom(pdf_path, excel_path, api_key, user_prompt,
                   max_pages=None, progress_callback=None):
    """
    Custom mode: the user defines the columns, so there is no fixed schema to
    normalise against and no balance to reconcile. Rows are written as-is.
    """
    if not os.path.exists(pdf_path):
        return False, f"File not found: {os.path.basename(pdf_path)}", None
    if not (user_prompt or "").strip():
        return False, "Describe what you want extracted.", None

    def report(percent, message):
        if progress_callback:
            try:
                progress_callback(percent, message)
            except Exception:
                pass

    try:
        model = common.build_model(api_key, json_mode=True)
    except (common.ModelUnavailable, ValueError) as exc:
        return False, str(exc), None

    prompt = common.custom_prompt(user_prompt)
    collected = []
    lock = threading.Lock()

    try:
        with pdfplumber.open(pdf_path) as pdf:
            pages, _ = _select_pages(pdf, max_pages)
            total = len(pages)
            if total == 0:
                return False, "The PDF has no readable pages.", None

            done = [0]

            def work(index):
                try:
                    image = common.render_page(pages[index])
                except Exception:
                    return
                text = common.generate(model, [prompt, image])
                items = common.parse_json_rows(text)
                with lock:
                    for position, item in enumerate(items):
                        collected.append((index, position, item))
                    done[0] += 1
                    report(5 + (done[0] / total) * 88, f"Read page {done[0]} of {total}")

            workers = max(1, min(Config.EXTRACTION_WORKERS, total))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for future in as_completed([pool.submit(work, i) for i in range(total)]):
                    try:
                        future.result()
                    except Exception as exc:
                        log.error("Custom page task failed: %s", exc)
    except Exception as exc:
        log.exception("Custom conversion failed")
        return False, f"Could not read the PDF: {exc}", None

    if not collected:
        return False, "Nothing matched those instructions in this document.", None

    collected.sort(key=lambda entry: (entry[0], entry[1]))
    records = [entry[2] for entry in collected]

    report(95, "Writing your workbook…")
    try:
        import pandas as pd
        frame = pd.DataFrame(records)
        with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Extracted", index=False)
    except Exception as exc:
        log.exception("Workbook write failed")
        return False, f"Could not write the Excel file: {exc}", None

    report(100, "Done")
    summary = {"rows": len(records), "debits": 0, "credits": 0, "net": 0,
               "checked": 0, "flagged": 0, "rejected": 0}
    return True, f"{len(records)} rows extracted.", summary
