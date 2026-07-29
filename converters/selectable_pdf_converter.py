import google.generativeai as genai
import pdfplumber
import pandas as pd
import os
import io
import time
import json
import re
from config import Config
from PIL import Image

# Fix Windows encoding issues
def safe_print(msg):
    try:
        print(msg)
    except UnicodeEncodeError:
        safe_msg = str(msg).encode('ascii', 'ignore').decode('ascii')
        print(safe_msg)

class SelectablePDFConverter:
    """
    GEMINI VISION CONVERTER (Double-Pass Enhanced Extraction)
    - Pass 1: Initial Extraction from 300 DPI Image.
    - Pass 2: Audit & Correct Verification on the same image.
    - Model: Uses gemini-2.5-pro (or Config fallback).
    """
    
    def __init__(self, api_key):
        genai.configure(api_key=api_key)
        
        # dynamic model selection from config
        self.model_name = getattr(Config, 'GEMINI_MODEL_NAME', 'gemini-2.5-pro')
        safe_print(f"[INIT] Selectable Converter initialized with model: {self.model_name}")
        
        self.model = genai.GenerativeModel(
            self.model_name,
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 8192,
                "response_mime_type": "application/json"
            }
        )

    def extract_page_with_gemini(self, page_image, page_num):
        """
        Pass 1: Extracts transactions from a single page image with structural awareness.
        """
        safe_print(f"[AI] Pass 1: Initial Extraction for Page {page_num}...")

        prompt = """
        You are a financial data extraction expert. Your task is to extract EVERY SINGLE transaction from this bank statement page image with 100% precision.

        ### STEP 1: STRUCTURE ANALYSIS
        - Locate the main transaction table area.
        - Identify headers for: 'Date', 'Description', 'Debit' (or 'Withdrawal'), 'Credit' (or 'Deposit'), and 'Balance'.
        - Understand which visual column belongs to which data type.

        ### STEP 2: EXTRACTION RULES
        - **Transactions Only**: Do NOT extract "Beginning Balance", "Ending Balance", or summary rows.
        - **Multi-line merging**: Merge multi-line descriptions into one single clean string.
        - **Date Logic**: Extract the date exactly as visible (e.g., Nov 03). Normalize to MM/DD/YYYY.
        - **Amount Logic**:
            - If separate 'Debit'/ 'Credit' columns exist: Extract values into correct fields.
            - If a single 'Amount' column exists: Use signs (-/+) or context to decide.
            - **CRITICAL**: Never confuse the 'Balance' column for a Debit or Credit amount.
        - **Zero Hallucination**: Only extract what is visually present in the image.

        ### STEP 3: OUTPUT SPECIFICATION
        Return ONLY a raw JSON array of objects. NO MARKDOWN.
        Example format:
        [
          {
            "Date": "MM/DD/YYYY",
            "Description": "Full Description Name",
            "Debit": "Numeric value or empty string",
            "Credit": "Numeric value or empty string"
          }
        ]
        """

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.model.generate_content([prompt, page_image])
                
                if not response or not response.text:
                    safe_print(f"   [WARN] Empty response on attempt {attempt+1}")
                    continue
                    
                raw_text = response.text.replace("```json", "").replace("```", "").strip()
                
                try:
                    data = json.loads(raw_text)
                    
                    if isinstance(data, dict):
                        for key in ["transactions", "data", "rows"]:
                            if key in data and isinstance(data[key], list):
                                data = data[key]
                                break
                    
                    if not isinstance(data, list):
                        data = [data] if isinstance(data, dict) else []
                    
                    # Normalize and validate
                    cleaned = []
                    for item in data:
                        if not isinstance(item, dict): continue
                        
                        # Flexible key mapping
                        res = {
                            "Date": next((str(item[k]).strip() for k in ["Date", "date", "DATE"] if k in item), ""),
                            "Description": next((str(item[k]).strip() for k in ["Description", "description", "DESC"] if k in item), ""),
                            "Debit": next((str(item[k]).strip() for k in ["Debit", "debit", "withdrawal", "Withdrawal"] if k in item), ""),
                            "Credit": next((str(item[k]).strip() for k in ["Credit", "credit", "deposit", "Deposit"] if k in item), "")
                        }
                        
                        # Secondary check for 'Amount' and 'Type'
                        if not res["Debit"] and not res["Credit"]:
                            amt = str(item.get("Amount", item.get("amount", ""))).strip()
                            typ = str(item.get("Type", item.get("type", ""))).upper()
                            if amt:
                                val = amt.replace('$', '').replace(',', '').replace('(', '').replace(')', '').strip()
                                try:
                                    f_val = abs(float(val))
                                    if "DEBIT" in typ or "WITHDRAWAL" in typ or "-" in amt or "(" in amt:
                                        res["Debit"] = str(f_val)
                                    else:
                                        res["Credit"] = str(f_val)
                                except: pass

                        if res["Date"] or res["Description"]:
                            cleaned.append(res)
                    
                    if cleaned:
                        return cleaned
                        
                except json.JSONDecodeError:
                    safe_print(f"   [AI] JSON parse failed on attempt {attempt+1}")
                    
            except Exception as e:
                error_str = str(e)
                safe_print(f"   [AI] Attempt {attempt + 1} failed: {error_str[:100]}")
                if "404" in error_str or "not found" in error_str.lower():
                     return "MODEL_ERROR"
                time.sleep(2)
        
        return []

    def verify_page_with_gemini(self, page_image, extracted_data, page_num):
        """
        Pass 2: "Audit & Correct" - The AI verifies its own output against reality.
        """
        if not extracted_data:
            return []

        safe_print(f"[AI] Pass 2: Verifying Data for Page {page_num}...")
        
        prompt = f"""
        ACT AS A FINANCIAL AUDITOR. Your job is to verify the extracted JSON data against the ORIGINAL IMAGE.
        
        AUDIT TASKS:
        1. **Completeness**: Are there any transactions in the image that are MISSING in the JSON? Add them.
        2. **Classification**: Ensure 'Debit' values are not in the 'Credit' column and vice versa.
        3. **Accuracy**: Correct any typos in descriptions or amounts.
        4. **Balance Check**: Ensure no values from the 'Balance' column were accidentally extracted as transactions.
        
        EXTRACTED DATA (To Verify):
        {json.dumps(extracted_data, indent=2)}
        
        INSTRUCTIONS:
        - Return the COMPLETE, CORRECTED JSON array.
        - Return ONLY the raw JSON list. No markdown.
        """
        
        try:
            response = self.model.generate_content([prompt, page_image])
            if response.text:
                raw_text = response.text.replace("```json", "").replace("```", "").strip()
                try:
                    verified_data = json.loads(raw_text)
                    if isinstance(verified_data, dict):
                        for key in ["transactions", "data", "rows"]:
                            if key in verified_data and isinstance(verified_data[key], list):
                                verified_data = verified_data[key]
                                break
                    if isinstance(verified_data, list):
                        safe_print(f"   [SUCCESS] Page {page_num} Audit Complete.")
                        return verified_data
                except: pass
        except Exception as e:
            safe_print(f"   [ERROR] Page {page_num} Audit failed: {e}")
            
        return extracted_data

    def process_pdf(self, pdf_path, excel_path, max_pages=None, progress_callback=None):
        if not os.path.exists(pdf_path):
            return False, f"File not found: {pdf_path}"

        safe_print(f"\n{'='*70}")
        safe_print(f"ENHANCED AI PROCESSING: {os.path.basename(pdf_path)}")
        safe_print(f"MODEL: {self.model_name}")
        safe_print(f"{'='*70}\n")
        
        start_time = time.time()
        all_transactions = []
        model_error_occurred = False
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                pages = pdf.pages[:max_pages] if max_pages else pdf.pages
                total_pages = len(pages)
                
                for i, page in enumerate(pages, 1):
                    msg = f"Processing Page {i}/{total_pages}..."
                    safe_print(f"[PAGE] {msg}")
                    if progress_callback:
                        progress_callback((i/total_pages)*90, msg)
                    
                    # 1. Convert Page to Image
                    try:
                        page_image = page.to_image(resolution=300).original
                    except Exception as img_err:
                        safe_print(f"[ERROR] failed to convert page {i} to image: {img_err}")
                        continue
                        
                    # 2. Pass 1: Extraction
                    transactions = self.extract_page_with_gemini(page_image, i)
                    
                    if transactions == "MODEL_ERROR":
                        model_error_occurred = True
                        break
                    
                    if transactions:
                        # 3. Pass 2: Enhanced Verification
                        verified_transactions = self.verify_page_with_gemini(page_image, transactions, i)
                        all_transactions.extend(verified_transactions)
                        safe_print(f"[RESULT] Page {i}: {len(verified_transactions)} verified rows collected.")
                    else:
                        safe_print(f"[WARN] Page {i}: No data found.")
                        
                    time.sleep(1)

        except Exception as e:
            return False, f"Processing Error: {str(e)}"

        if model_error_occurred:
            return False, f"Model '{self.model_name}' not available. Please check API Key or Config."

        if not all_transactions:
            return False, "No data extracted. Try 'General PDF' option."

        # Final Post-Process
        df = pd.DataFrame(all_transactions)
        for col in ['Date', 'Description', 'Debit', 'Credit']:
            if col not in df.columns: df[col] = ''
            
        # Clean numeric
        for col in ['Debit', 'Credit']:
            df[col] = pd.to_numeric(df[col].replace('', None), errors='coerce').fillna(0)

        try:
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                df[['Date', 'Description', 'Debit', 'Credit']].to_excel(writer, index=False, sheet_name='Transactions')
                
                summary = pd.DataFrame([
                    ['Total Rows', len(df)],
                    ['Total Debits', df['Debit'].sum()],
                    ['Total Credits', df['Credit'].sum()],
                    ['Net Balance', df['Credit'].sum() - df['Debit'].sum()]
                ], columns=['Metric', 'Value'])
                summary.to_excel(writer, index=False, sheet_name='Summary')
                
            elapsed = time.time() - start_time
            safe_print(f"\n[COMPLETE] Saved {len(df)} transactions in {elapsed:.1f}s")
            return True, f"Success: {len(df)} transactions extracted."
            
        except Exception as e:
            return False, f"Excel Save Error: {e}"

def extract_pdf_to_excel(pdf_path, excel_path, api_key, max_pages=None, progress_callback=None):
    try:
        converter = SelectablePDFConverter(api_key)
        return converter.process_pdf(pdf_path, excel_path, max_pages, progress_callback)
    except Exception as e:
        return False, str(e)