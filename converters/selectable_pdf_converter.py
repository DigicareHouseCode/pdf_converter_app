"""
Selectable (digital) PDFs.

Reads the PDF's own text layer, which is exact, and only falls back to vision
on pages where table detection finds nothing. The previous implementation
rasterised every page at 300 DPI and OCR'd it, discarding perfect data and
reintroducing transcription error that did not need to exist.
"""

from converters.pipeline import convert


def extract_pdf_to_excel(pdf_path, excel_path, api_key, max_pages=None, progress_callback=None):
    success, message, _summary = convert(
        pdf_path, excel_path, api_key,
        mode="selectable",
        max_pages=max_pages,
        progress_callback=progress_callback,
    )
    return success, message
