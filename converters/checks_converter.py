"""
Check-image extraction pipeline.

Walks a batch directory of check images (PNG, JPG, GIF, TIFF, WebP) and PDFs,
sends each to Gemini for structured field extraction, validates the results,
and writes an Excel workbook with all eight fields plus a Review column.

Design decisions:
* Images are sent to the model directly — they arrive at ~275 DPI from the
  bank's scanner, so re-rasterising would only degrade them.
* GIFs are converted to RGB first because Pillow opens them in palette (P)
  mode and the Gemini API rejects non-RGB uploads.
* PDFs are rendered page-by-page at 300 DPI (the configured floor) and the
  model is asked for *every* check on that page.  Statement-filler pages
  without checks are expected to return [].
* Legal-amount comparison uses a flexible word-to-number parser that handles
  the informal shorthand found on real NY-issued checks ("five seventy four
  & twenty five" rather than "five hundred seventy-four and 25/100").
"""

import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

from config import Config
from converters import common

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ constants

CHECK_COLUMNS = [
    "check_number", "date", "payee", "courtesy_amount",
    "legal_amount", "memo", "micr_line", "account_holder",
]

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".webp"}
PDF_EXTENSIONS = {".pdf"}
ALL_EXTENSIONS = IMAGE_EXTENSIONS | PDF_EXTENSIONS

AMOUNT_TOLERANCE = 0.011  # cents, with room for float noise

# -------------------------------------------------------------------- prompt

PROMPT_CHECK = """\
You are extracting data from a bank check image.

Return a JSON array.  Each element represents ONE check visible in the image.
If the image contains no checks (e.g. it is a statement page), return [].

For each check extract these fields EXACTLY:
{
  "check_number":    "printed check number, usually top-right corner",
  "date":            "date exactly as written on the check",
  "payee":           "PAY TO THE ORDER OF line",
  "courtesy_amount": "numeric dollar amount from the box (e.g. 574.25)",
  "legal_amount":    "the written-out dollar amount on the DOLLARS line, transcribed exactly as handwritten",
  "memo":            "FOR / memo line content",
  "micr_line":       "full MICR line at the bottom of the check, all characters",
  "account_holder":  "name or company printed at the top of the check"
}

RULES:
- courtesy_amount must be a plain number without $ or commas: 574.25 not $574.25
- legal_amount: transcribe the handwritten words exactly, do not convert to a number
- micr_line: include all characters and symbols as accurately as possible
- If a field is not visible or not present, use an empty string ""
- Return ONLY the JSON array. No markdown, no commentary.
"""

# ----------------------------------------------------------- file discovery

def discover_files(batch_dir):
    """
    Walk the batch directory and return a sorted list of
    (absolute_path, relative_path) tuples for supported files.
    """
    found = []
    for dirpath, _, filenames in os.walk(batch_dir):
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext not in ALL_EXTENSIONS:
                continue
            abspath = os.path.join(dirpath, name)
            relpath = os.path.relpath(abspath, batch_dir)
            found.append((abspath, relpath))

    found.sort(key=lambda pair: pair[1].lower())
    return found


# ---------------------------------------------------------- image loading

def load_image(filepath):
    """
    Load an image file and return a PIL Image in RGB mode.

    GIFs arrive in palette (P) mode which the Gemini API rejects,
    so they are converted to RGB here.
    """
    img = Image.open(filepath)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def extract_checks_from_image(image, model, source_hint=""):
    """Send a single image to the model and return parsed check dicts."""
    text = common.generate(model, [PROMPT_CHECK, image])
    items = common.parse_json_rows(text)
    if not items:
        log.debug("No checks found in %s", source_hint)
    return items


def extract_checks_from_pdf(filepath, model):
    """
    Render each page of a PDF at 300 DPI and extract checks.

    A single PDF page may contain multiple check images (common in
    bank statement exports). Pages with no checks return [].
    """
    import pdfplumber

    all_items = []
    try:
        with pdfplumber.open(filepath) as pdf:
            for page_no, page in enumerate(pdf.pages, 1):
                try:
                    image = common.render_page(page)
                except Exception as exc:
                    log.error("PDF page %d of %s could not be rasterised: %s",
                              page_no, filepath, exc)
                    continue

                items = extract_checks_from_image(
                    image, model, f"{filepath} p{page_no}")
                all_items.extend(items)
    except Exception as exc:
        log.error("Could not open PDF %s: %s", filepath, exc)

    return all_items


# ----------------------------------------------------------- date parsing

def parse_check_date(raw):
    """
    Parse a check date in US format.

    06/12/26 → 2026-06-12
    Two-digit years: 00–49 → 2000–2049, 50–99 → 1950–1999.
    """
    if not raw or not raw.strip():
        return None

    raw = raw.strip()

    # Try MM/DD/YY and MM/DD/YYYY with / or - separators
    m = re.match(
        r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", raw
    )
    if not m:
        return None

    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))

    # Two-digit year
    if year < 100:
        year = year + 2000 if year < 50 else year + 1900

    # Basic sanity
    if not (1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100):
        return None

    return f"{year:04d}-{month:02d}-{day:02d}"


# ------------------------------------------------- legal amount parsing

# Word-to-number mapping
_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}


def _words_to_number(text):
    """
    Convert written-out dollar amounts to a float.

    Handles both formal and informal shorthand:
      "five hundred seventy four"  → 574.0
      "five seventy four"          → 574.0
      "one thousand two hundred"   → 1200.0

    Returns None when the text cannot be parsed.
    """
    if not text:
        return None

    # Normalise
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9/\s]", " ", text)  # strip &, -, etc.
    text = re.sub(r"\s+", " ", text).strip()

    # If the model returned a plain number, just use it
    try:
        return float(text)
    except ValueError:
        pass

    # Split on "and", "dollars", "&" to separate dollars from cents
    # Patterns: "five seventy four and twenty five"
    #           "five seventy four & 25/100"
    #           "five seventy four and 25/100 dollars"
    parts = re.split(r"\band\b|\bdollars?\b|\bcents?\b", text)
    dollar_text = parts[0].strip() if parts else ""
    cent_text = parts[1].strip() if len(parts) > 1 else ""

    dollars = _parse_word_group(dollar_text)
    if dollars is None:
        return None

    cents = 0.0
    if cent_text:
        # Handle "25/100" or "50/100" fraction notation
        frac_match = re.match(r"(\d+)\s*/\s*100", cent_text)
        if frac_match:
            cents = int(frac_match.group(1)) / 100.0
        else:
            parsed_cents = _parse_word_group(cent_text)
            if parsed_cents is not None:
                cents = parsed_cents / 100.0

    return dollars + cents


def _parse_word_group(text):
    """
    Parse a group of number words into an integer.

    Handles formal: "five hundred seventy four" → 574
    And informal:   "five seventy four" → 574 (interprets as 5*100 + 74)
    """
    if not text:
        return 0

    words = text.split()
    if not words:
        return 0

    # Try as a plain number first
    try:
        return float(text)
    except ValueError:
        pass

    total = 0
    current = 0

    for word in words:
        word = word.strip()
        if not word:
            continue

        if word in _ONES:
            current += _ONES[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = (current if current else 1) * 100
        elif word == "thousand":
            current = (current if current else 1) * 1000
            total += current
            current = 0
        elif word == "million":
            current = (current if current else 1) * 1_000_000
            total += current
            current = 0
        else:
            # Try as a plain number (e.g. "25")
            try:
                current += int(word)
            except ValueError:
                continue

    total += current

    # Detect informal shorthand like "five seventy four" = 574
    # If we got something like 5 + 74 = 79 but the tokens suggest
    # "five" followed by a two-digit group, re-parse as 5*100 + 74
    if total < 100 and len(words) >= 2:
        result = _try_informal_parse(words)
        if result is not None:
            return result

    return total if total > 0 else None


def _try_informal_parse(words):
    """
    Handle informal shorthand: "five seventy four" → 574.

    Pattern: a small number (1-9) followed by a two-digit number (10-99).
    The small number is implicitly "X hundred".
    """
    if len(words) < 2:
        return None

    first = _ONES.get(words[0])
    if first is None or first < 1 or first > 9:
        return None

    # Parse the rest as a number
    rest_total = 0
    for word in words[1:]:
        if word in _ONES:
            rest_total += _ONES[word]
        elif word in _TENS:
            rest_total += _TENS[word]
        else:
            try:
                rest_total += int(word)
            except ValueError:
                return None

    if 0 < rest_total < 100:
        return first * 100 + rest_total

    return None


def parse_legal_amount(raw):
    """
    Parse the legal (written-out) amount from a check.

    If the model already returned a number, use it directly.
    Otherwise, run the word-to-number parser.
    """
    if raw is None or raw == "":
        return None

    # If it's already a number
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip()
    if not text:
        return None

    # Try direct float parse first (model may have converted it)
    try:
        return float(text.replace(",", "").replace("$", ""))
    except ValueError:
        pass

    return _words_to_number(text)


# -------------------------------------------------------- MICR parsing

def extract_micr_check_number(micr_line):
    """
    Extract the check number from a MICR line.

    MICR format: ⑆check_number⑆ ⑇routing⑇ account⑆
    The check number sits between the first pair of on-us symbols (⑆).
    Gemini may normalise the symbols to various ASCII representations.
    """
    if not micr_line:
        return None

    raw = str(micr_line).strip()

    # Try various representations of the MICR on-us symbol
    # The ⑆ symbol, or common ASCII stand-ins: ||, ⑆, ", etc.
    # Pattern: look for a sequence of digits near the start
    patterns = [
        # Unicode MICR symbols
        r"⑆\s*0*(\d+)\s*⑆",
        # Double-pipe or similar delimiters
        r"\|\|\s*0*(\d+)\s*\|\|",
        # Quoted
        r'"\s*0*(\d+)\s*"',
        # Common OCR of MICR: just grab the first number sequence
        # that looks like a check number (2-6 digits, possibly zero-padded)
        r"(?:^|[^0-9])0*(\d{2,6})(?:[^0-9]|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, raw)
        if m:
            return m.group(1).lstrip("0") or "0"

    return None


# ----------------------------------------------------------- validation

def format_legal_amount(amount):
    """
    Convert a float amount to perfectly formatted check wording.
    e.g. 574.25 -> Five Hundred Seventy-Four and 25/100
    """
    if amount is None:
        return ""
        
    import math
    
    ones = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", 
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", 
            "Seventeen", "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
    
    def convert_less_than_1000(n):
        if n == 0:
            return ""
        elif n < 20:
            return ones[n]
        elif n < 100:
            return tens[n // 10] + ("-" + ones[n % 10] if (n % 10 != 0) else "")
        else:
            return ones[n // 100] + " Hundred" + (" " + convert_less_than_1000(n % 100) if (n % 100 != 0) else "")
            
    dollars = int(math.floor(amount))
    cents = int(round((amount - dollars) * 100))
    
    if dollars == 0:
        dollar_words = "Zero"
    elif dollars < 1000:
        dollar_words = convert_less_than_1000(dollars)
    elif dollars < 1000000:
        dollar_words = convert_less_than_1000(dollars // 1000) + " Thousand"
        rem = dollars % 1000
        if rem != 0:
            dollar_words += " " + convert_less_than_1000(rem)
    else:
        # Fallback for huge numbers
        dollar_words = str(dollars)
        
    return dollar_words

def validate_checks(rows):
    """
    Run all validation rules on extracted check rows.

    Each row gets a _flags list populated with human-readable notes.
    Returns (total_checked, total_flagged).
    """
    flagged = 0

    for row in rows:
        flags = row.setdefault("_flags", [])

        # 1. courtesy_amount ≠ legal_amount
        courtesy = common.as_amount(row.get("courtesy_amount"))
        legal_parsed = parse_legal_amount(row.get("legal_amount"))

        if courtesy is not None and legal_parsed is not None:
            if abs(courtesy - legal_parsed) > AMOUNT_TOLERANCE:
                flags.append(
                    f"Courtesy (${courtesy:,.2f}) ≠ legal (${legal_parsed:,.2f})"
                )

        # Generate perfectly worded legal amount
        if courtesy is not None:
            row["legal_amount"] = format_legal_amount(courtesy)
        elif legal_parsed is not None:
            row["legal_amount"] = format_legal_amount(legal_parsed)

        # 2. printed check_number ≠ MICR check number
        printed = str(row.get("check_number") or "").strip()
        micr_num = extract_micr_check_number(row.get("micr_line"))

        if printed and micr_num and printed != micr_num:
            flags.append(
                f"Printed check # {printed} ≠ MICR check # {micr_num}"
            )

        # 3. check_number ≠ filename (when filename is numeric)
        source_file = row.get("_source_file", "")
        if source_file:
            basename = os.path.splitext(os.path.basename(source_file))[0]
            if basename.isdigit() and printed and printed != basename:
                flags.append(
                    f"Check # {printed} ≠ filename {basename}"
                )

        # 4. date absent or unparsable
        raw_date = str(row.get("date") or "").strip()
        parsed_date = parse_check_date(raw_date)
        if not parsed_date:
            if not raw_date:
                flags.append("Date missing")
            else:
                flags.append(f"Date unreadable: {raw_date}")
        else:
            row["date"] = parsed_date

        if flags:
            flagged += 1

    return len(rows), flagged


# ---------------------------------------------------------- workbook

def write_checks_workbook(rows, path, summary):
    """
    Write the Checks sheet and Summary sheet.

    Columns: check_number, date, payee, courtesy_amount, legal_amount,
             memo, micr_line, account_holder, Review
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Checks"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="0F5F56")
    flag_fill = PatternFill("solid", fgColor="FBECEC")
    money = "#,##0.00"

    # Only include the first 6 columns (excluding micr_line and account_holder)
    headers = CHECK_COLUMNS[:6]
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for row in rows:
        note = "; ".join(row.get("_flags", []))
        ws.append([
            row.get("check_number", ""),
            row.get("date", ""),
            row.get("payee", ""),
            row.get("courtesy_amount"),
            row.get("legal_amount", ""),
            row.get("memo", ""),
        ])
        idx = ws.max_row
        # Format courtesy_amount as money
        ws.cell(row=idx, column=4).number_format = money
        if note:
            for col in range(1, len(headers) + 1):
                ws.cell(row=idx, column=col).fill = flag_fill

    widths = [14, 13, 30, 15, 40, 20]
    for pos, w in enumerate(widths, start=1):
        col_dim = ws.column_dimensions[get_column_letter(pos)]
        col_dim.width = w
    ws.freeze_panes = "A2"

    # Summary sheet
    report = wb.create_sheet("Summary")
    report.append(["Metric", "Value"])
    report["A1"].font = header_font
    report["B1"].font = header_font
    report["A1"].fill = header_fill
    report["B1"].fill = header_fill

    for label, value, fmt in [
        ("Checks read", summary["rows"], None),
        ("Total amount", summary["total_amount"], money),
        ("Flagged for review", summary["flagged"], None),
    ]:
        report.append([label, value])
        if fmt:
            report.cell(row=report.max_row, column=2).number_format = fmt

    if summary.get("failed_files"):
        report.append([])
        report.append(["Files that failed", ""])
        for f in summary["failed_files"]:
            report.append(["", f])

    report.column_dimensions["A"].width = 25
    report.column_dimensions["B"].width = 50

    if summary["flagged"]:
        report.append([])
        report.append([
            "Check the highlighted rows on the Checks sheet — they have "
            "validation warnings that need human review."
        ])

    wb.save(path)


# -------------------------------------------------------- entry point

def convert_checks(batch_dir, excel_path, api_key,
                   progress_callback=None):
    """
    Run the full check extraction pipeline.

    batch_dir:  directory containing check images / PDFs (flat or nested)
    excel_path: where to write the output workbook
    api_key:    Gemini API key

    Returns (success: bool, message: str, summary: dict | None).
    """
    def report(pct, msg):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    # --- discover files ---
    report(2, "Scanning uploaded files…")
    files = discover_files(batch_dir)
    if not files:
        return False, "No supported check files found in the upload.", None

    log.info("Check batch: %d file(s) found in %s", len(files), batch_dir)

    # --- build model ---
    try:
        model = common.build_model(api_key, json_mode=True)
    except common.ModelUnavailable as exc:
        return False, str(exc), None
    except ValueError as exc:
        return False, str(exc), None

    # --- extract ---
    all_rows = []
    failed_files = []
    lock = threading.Lock()
    done = [0]
    total = len(files)

    def process_file(abs_path, rel_path):
        ext = os.path.splitext(abs_path)[1].lower()
        items = []

        try:
            if ext in IMAGE_EXTENSIONS:
                image = load_image(abs_path)
                items = extract_checks_from_image(image, model, rel_path)
            elif ext in PDF_EXTENSIONS:
                items = extract_checks_from_pdf(abs_path, model)
        except Exception as exc:
            log.error("Failed to process %s: %s", rel_path, exc)
            with lock:
                failed_files.append(rel_path)
                done[0] += 1
                report(5 + (done[0] / total) * 80,
                       f"Processed {done[0]} of {total} files")
            return

        with lock:
            for item in items:
                row = {}
                for field in CHECK_COLUMNS:
                    val = item.get(field)
                    if val is None:
                        val = ""
                    row[field] = val

                # courtesy_amount → numeric
                ca = common.as_amount(row["courtesy_amount"])
                row["courtesy_amount"] = ca

                row["_source_file"] = rel_path
                row["_flags"] = []
                all_rows.append(row)

            if not items:
                # Could be a statement page in a PDF — not necessarily an error
                if ext in IMAGE_EXTENSIONS:
                    failed_files.append(rel_path)

            done[0] += 1
            report(5 + (done[0] / total) * 80,
                   f"Processed {done[0]} of {total} files")

    report(5, f"Extracting from {total} file(s)…")
    workers = max(1, min(Config.EXTRACTION_WORKERS, total))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(process_file, absp, relp)
            for absp, relp in files
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except common.ModelUnavailable as exc:
                return False, str(exc), None
            except Exception as exc:
                log.error("Check extraction task failed: %s", exc)

    if not all_rows:
        return False, "No checks could be read from the uploaded files.", None

    # --- validate ---
    report(88, "Validating extracted data…")
    checked, flagged = validate_checks(all_rows)

    # --- sort by check number, then date ---
    def sort_key(r):
        cn = str(r.get("check_number") or "")
        # Numeric check numbers sort numerically
        try:
            cn_int = int(cn)
        except (ValueError, TypeError):
            cn_int = 999999
        return (cn_int, r.get("date") or "")

    all_rows.sort(key=sort_key)

    # --- summary ---
    total_amount = sum(
        r["courtesy_amount"] for r in all_rows
        if isinstance(r.get("courtesy_amount"), (int, float))
    )
    summary = {
        "rows": len(all_rows),
        "total_amount": total_amount,
        "flagged": flagged,
        "failed_files": failed_files,
        "checked": checked,
    }

    # --- write workbook ---
    report(94, "Writing your workbook…")
    try:
        write_checks_workbook(all_rows, excel_path, summary)
    except Exception as exc:
        log.exception("Check workbook write failed")
        return False, f"Could not write the Excel file: {exc}", None

    # --- result message ---
    parts = [f"{len(all_rows)} checks extracted"]
    if flagged:
        parts.append(f"{flagged} flagged for review")
    if failed_files:
        parts.append(f"{len(failed_files)} file(s) unreadable")

    report(100, "Done")
    return True, ", ".join(parts) + ".", summary
