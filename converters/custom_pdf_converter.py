"""
Custom mode — the user supplies the extraction instructions.

Because the shape of the output is whatever the user asked for, there is no
fixed schema to normalise against and no running balance to reconcile. Rows
are written in page order exactly as returned.
"""

from converters.pipeline import convert_custom


def extract_custom_pdf_to_excel(pdf_path, excel_path, api_key, custom_prompt,
                                max_pages=None, progress_callback=None):
    success, message, _summary = convert_custom(
        pdf_path, excel_path, api_key, custom_prompt,
        max_pages=max_pages,
        progress_callback=progress_callback,
    )
    return success, message
