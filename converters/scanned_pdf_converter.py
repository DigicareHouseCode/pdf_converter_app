"""
Scanned (image-based) PDFs.

Vision extraction with the audit pass, at Config.EXTRACTION_DPI. This mode
previously rasterised at 72 DPI "for speed", which left roughly 8 pixels of
height for 8pt statement type — far too little to separate 3 from 8 or 5 from
6 reliably. It also had no verification pass, despite being the mode where
transcription error is likeliest.
"""

from converters.pipeline import convert


def extract_data_with_gemini_full(pdf_path, output_excel_path, api_key,
                                  max_pages=None, progress_callback=None):
    success, message, _summary = convert(
        pdf_path, output_excel_path, api_key,
        mode="scanned",
        max_pages=max_pages,
        progress_callback=progress_callback,
    )
    return success, message
