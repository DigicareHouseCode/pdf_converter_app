"""
General mode — works out which strategy each page needs.

Tries the text layer per page and falls back to vision only where that finds
nothing, so a document that mixes digital and scanned pages is handled
correctly without the user having to know which is which.
"""

from converters.pipeline import convert


def convert_pdf_to_excel(pdf_path, excel_path, api_key, max_pages=None, progress_callback=None):
    success, message, _summary = convert(
        pdf_path, excel_path, api_key,
        mode="general",
        max_pages=max_pages,
        progress_callback=progress_callback,
    )
    return success, message
