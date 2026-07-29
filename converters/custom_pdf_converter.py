import google.generativeai as genai
import pdfplumber
import pandas as pd
from PIL import Image
import io
import os
import json
import re
import time


def extract_custom_page_with_gemini(page_image, model, custom_prompt):
    """
    Sends a page image to Gemini with custom prompt for structured extraction.
    """
    prompt = f"""
    ACT AS A CUSTOM DATA EXTRACTION SPECIALIST. Your task is to extract structured data from this PDF page image based STRICTLY on the user's requirements.

    USER REQUIREMENTS:
    {custom_prompt}

    DEEP ANALYSIS INSTRUCTIONS:
    1. ANALYZE the image to locate the specific data points requested.
    2. IGNORE irrelevant text, headers, or footers unless requested.
    3. EXTRACT with 100% precision. Do not infer or hallucinate data.
    4. FORMAT the output as a clean JSON array of objects.

    JSON OUTPUT FORMAT (Strict Array of Objects):
    [
      {{
        "Column1": "Value",
        "Column2": "Value",
        ... (matches user requirements)
      }}
    ]

    STRICT RULES:
    - ACCURACY IS CRITICAL.
    - NUMBERS: Return as clean numbers or strings without currency symbols unless requested otherwise.
    - DATES: Standardize to YYYY-MM-DD or MM/DD/YYYY if possible.
    - MULTI-LINE TEXT: Merge into single strings.
    - NO MARKDOWN: Return ONLY the raw JSON array.
    """

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                [prompt, page_image],
                generation_config={
                    "temperature": 0.0,
                    "max_output_tokens": 8000,
                    "response_mime_type": "application/json"
                }
            )
            
            if not response or not response.text:
                continue

            raw_text = response.text.strip()
            return json.loads(raw_text)

        except Exception as e:
            print(f"[WARNING] Custom extraction error on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))

    return []


def convert_custom_pdf_to_excel(pdf_path, excel_path, api_key, custom_prompt, max_pages=None, progress_callback=None):
    """
    Custom PDF converter that uses user-provided prompt for conversion.
    """
    if not os.path.exists(pdf_path):
        print(f"[ERROR] ERROR: File '{pdf_path}' not found.")
        return False, f"File not found: {pdf_path}"

    print(f"[PAGE] Processing {pdf_path} with custom prompt...")

    # Configure Gemini
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-pro")

    all_rows = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            
            # Application of page limit
            pages_to_process = pdf.pages
            if max_pages and total_pages > max_pages:
                print(f"[WARNING] Page limit applied: only processing first {max_pages} pages.")
                pages_to_process = pdf.pages[:max_pages]
                total_pages = max_pages

            for i, page in enumerate(pages_to_process, start=1):
                if progress_callback:
                    progress_callback((i / total_pages) * 95, f"Processing page {i}/{total_pages} with custom logic...")
                
                print(f"[SEARCH] Processing page {i}/{total_pages}...")

                # Convert page to image for Gemini Vision - Using higher resolution for better OCR
                try:
                    pil_img = page.to_image(resolution=500).original
                except:
                    pil_img = page.to_image(resolution=300).original

                # Extract with custom prompt
                page_rows = extract_custom_page_with_gemini(pil_img, model, custom_prompt)

                if page_rows:
                    if isinstance(page_rows, dict) and "transactions" in page_rows:
                        page_rows = page_rows["transactions"]
                    elif isinstance(page_rows, dict) and "data" in page_rows:
                        page_rows = page_rows["data"]
                    
                    if isinstance(page_rows, list):
                        all_rows.extend(page_rows)
                        print(f"[SUCCESS] Page {i}: extracted {len(page_rows)} rows")
                
                # Small delay
                if i < total_pages:
                    time.sleep(1)

        if progress_callback:
            progress_callback(98, "Saving results...")


    except Exception as e:
        print(f"[ERROR] PDF processing error: {e}")
        return False, f"PDF processing error: {str(e)}"

    if not all_rows:
        print("[ERROR] No data extracted from the PDF.")
        return False, "No data extracted from the PDF with the given prompt."

    # Convert to DataFrame
    try:
        df = pd.DataFrame(all_rows)

        # Clean column names
        df.columns = [str(col).strip() for col in df.columns]

        # Convert date columns if present
        for col in df.columns:
            if any(keyword in col.lower() for keyword in ['date', 'time']):
                try:
                    df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d')
                except:
                    pass

        # Convert numeric columns
        for col in df.columns:
            if any(keyword in col.lower() for keyword in ['amount', 'price', 'cost', 'debit', 'credit', 'balance']):
                try:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                except:
                    pass

        # Save to Excel
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name="Custom_Data", index=False)

            # Add a summary sheet
            summary_data = {
                'Summary': [
                    f"Total rows extracted: {len(df)}",
                    f"Total columns: {len(df.columns)}",
                    f"Custom prompt used: {custom_prompt[:100]}...",
                    f"Extraction completed: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                ]
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)

        print(f"\n[INFO] SUCCESS: Saved custom extracted content to {excel_path}")
        print(f"[STATS] Extracted {len(df)} rows with {len(df.columns)} columns")

        # Show column names
        print("[LIST] Columns extracted:", list(df.columns))

        return True, f"Successfully extracted {len(df)} rows using custom prompt"

    except Exception as e:
        print(f"[ERROR] DataFrame/Excel error: {e}")
        return False, f"DataFrame/Excel error: {str(e)}"



def extract_custom_pdf_to_excel(pdf_path, excel_path, api_key, custom_prompt, max_pages=None, progress_callback=None):
    """
    Wrapper function for Flask compatibility.
    """
    return convert_custom_pdf_to_excel(pdf_path, excel_path, api_key, custom_prompt, max_pages=max_pages, progress_callback=progress_callback)