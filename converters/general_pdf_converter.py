import google.generativeai as genai
import pdfplumber
import pandas as pd
from PIL import Image
import io
import os
import json
import re
import time

try:
    from PIL import PngImagePlugin
except ImportError:
    pass


def extract_page_with_gemini(page_image, model):
    """
    Sends a page image to Gemini and asks it to extract transaction data
    """
    # Detailed and Robust Prompt
    prompt = """
    ACT AS A FINANCIAL DATA EXPERT. Your goal is to extract every single financial transaction from this bank statement page image with 100% accuracy.

    detailed analysis instructions:
    1. SCAN THE PAGE: Identify the main transaction table. Ignore headers, footers, and summary blocks (e.g., "Total Debits", "Ending Balance") unless they are part of the transaction row itself.
    2. IDENTIFY HEADERS: Locate columns for 'Date', 'Description', and 'Amount'.
       - Check if there are separate 'Debit'/'Withdrawal' and 'Credit'/'Deposit' columns.
       - If only one 'Amount' column exists, look for signs (-/+) or rely on the context of the description (e.g. "Payment", "Deposit").
    3. EXTRACT ROWS: Capture every transaction row.
       - Merge multi-line descriptions into one full string.
       - Normalize the Date to MM/DD/YYYY.
    4. DETERMINE TYPE:
       - DEBIT: Money out (Withdrawal, Payment, Check, Fee). Amount must be NEGATIVE.
       - CREDIT: Money in (Deposit, Transfer, Interest). Amount must be POSITIVE.

    JSON OUTPUT FORMAT (Strict Array of Objects):
    [
      {
        "Date": "MM/DD/YYYY",
        "Description": "The complete description text",
        "Amount": "Numeric value (NEGATIVE for Debits, POSITIVE for Credits)",
        "Type": "Explicitly 'DEBIT' or 'CREDIT'",
        "Balance": "Running balance (if visible)"
      }
    ]

    STRICT RULES:
    - NO HALLUCINATIONS: Only extract what is visible.
    - ACCURACY IS CRITICAL: Do not miss any transaction.
    - FORMATTING: Remove currency symbols ($) and commas from numbers.
    - DATES: Use MM/DD/YYYY format.
    - NO MARKDOWN: Return ONLY the raw JSON array.
    """

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"   [AI] Attempt {attempt + 1}/{max_retries}...")
            
            # Using JSON mode for better reliability
            response = model.generate_content(
                [prompt, page_image],
                generation_config={
                    "temperature": 0.0,
                    "max_output_tokens": 8000,
                    "response_mime_type": "application/json"
                }
            )
            
            if not response or not response.text:
                print(f"   [WARNING] Empty response")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return []
                
            raw_text = response.text.strip()
            print(f"   [DEBUG] Got {len(raw_text)} chars response")
            
            try:
                data = json.loads(raw_text)
                
                # Handle different root levels (list or dict with 'transactions' key)
                if isinstance(data, dict) and "transactions" in data:
                    data = data["transactions"]
                
                if not isinstance(data, list):
                    data = [data] if isinstance(data, dict) else []
                
                # Clean and validate
                cleaned = []
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    
                    # Extract amount logic
                    amount_val = item.get("Amount", item.get("amount", 0))
                    item_type = str(item.get("Type", item.get("type", ""))).upper()
                    
                    try:
                        amount_num = float(str(amount_val).replace('$', '').replace(',', '').strip())
                    except:
                        amount_num = 0.0

                    debit = ""
                    credit = ""
                    
                    if item_type == "DEBIT" or amount_num < 0:
                        debit = str(abs(amount_num))
                    elif item_type == "CREDIT" or amount_num > 0:
                        credit = str(abs(amount_num))
                    
                    cleaned_item = {
                        "Date": str(item.get("Date", item.get("date", ""))).strip(),
                        "Description": str(item.get("Description", item.get("description", ""))).strip(),
                        "Debit": debit,
                        "Credit": credit
                    }
                    
                    if cleaned_item["Date"] or cleaned_item["Description"]:
                        cleaned.append(cleaned_item)
                
                if cleaned:
                    print(f"   [SUCCESS] Extracted {len(cleaned)} transactions")
                    return cleaned
                else:
                    print(f"   [WARNING] No valid transactions after cleaning")
                    
            except json.JSONDecodeError as e:
                print(f"   [ERROR] JSON parse failed: {e}")
                # Try regex fallback if JSON mode fails
                json_match = re.search(r'\[.*?\]', raw_text, re.DOTALL)
                if json_match:
                    try:
                        data = json.loads(json_match.group(0))
                        # ... process data same as above ...
                        # (Keeping it simple for now as response_mime_type usually works)
                    except: pass
                
        except Exception as e:
            print(f"   [ERROR] Attempt {attempt + 1} failed: {str(e)[:100]}")
            
        if attempt < max_retries - 1:
            time.sleep(3 * (attempt + 1))
    
    print(f"   [FAILED] All attempts failed")
    return []


def extract_text_fallback(page):
    """Fallback: extract text and parse for transactions"""
    print(f"   [FALLBACK] Trying text extraction...")
    transactions = []
    
    try:
        text = page.extract_text()
        if not text:
            print(f"   [FALLBACK] No text found")
            return []
        
        lines = text.split('\n')
        print(f"   [FALLBACK] Analyzing {len(lines)} lines...")
        
        # Look for lines with dates and amounts
        for line in lines:
            line = line.strip()
            if not line or len(line) < 10:
                continue
            
            # Try to find date at start: MM/DD/YYYY or MM-DD-YYYY
            date_match = re.match(r'^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})', line)
            if not date_match:
                continue
            
            date = date_match.group(1)
            rest = line[len(date):].strip()
            
            # Look for dollar amounts
            amounts = re.findall(r'(\$?[\d,]+\.\d{2})', rest)
            if not amounts:
                continue
            
            # Get last amount (usually the transaction amount)
            amount_str = amounts[-1].replace('$', '').replace(',', '')
            
            try:
                amount = float(amount_str)
                
                # Description is everything before the last amount
                desc_end = rest.rfind(amounts[-1])
                description = rest[:desc_end].strip() if desc_end > 0 else rest
                
                # Determine debit/credit (heuristic)
                # If negative or has minus, it's a debit
                is_debit = amount < 0 or '-' in line
                
                transactions.append({
                    "Date": date,
                    "Description": description[:200],  # Limit length
                    "Debit": str(abs(amount)) if is_debit else "",
                    "Credit": str(abs(amount)) if not is_debit else ""
                })
            except:
                continue
        
        if transactions:
            print(f"   [FALLBACK] Found {len(transactions)} transactions")
        else:
            print(f"   [FALLBACK] No transactions found")
            
        return transactions
        
    except Exception as e:
        print(f"   [FALLBACK] Error: {e}")
        return []


def convert_pdf_to_excel(pdf_path, excel_path, api_key, max_pages=None, progress_callback=None):
    """Main conversion function"""
    if not os.path.exists(pdf_path):
        print(f"[ERROR] File not found: {pdf_path}")
        return False, f"File not found: {pdf_path}"

    print(f"\n{'='*60}")
    print(f"[START] Processing: {os.path.basename(pdf_path)}")
    print(f"{'='*60}\n")

    # Configure Gemini
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-2.5-pro")
        print(f"[OK] Gemini API configured")
    except Exception as e:
        print(f"[ERROR] Failed to configure Gemini: {e}")
        return False, f"API configuration failed: {str(e)}"

    all_transactions = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            
            pages_to_process = pdf.pages
            if max_pages and total_pages > max_pages:
                print(f"[WARNING] Page limit applied: only processing first {max_pages} pages.")
                pages_to_process = pdf.pages[:max_pages]
                total_pages = max_pages
            
            print(f"[INFO] Total pages to process: {total_pages}\n")
            
            for i, page in enumerate(pages_to_process, start=1):
                if progress_callback:
                    progress_callback((i / total_pages) * 95, f"Processing page {i}/{total_pages}...")
                
                print(f"[PAGE {i}/{total_pages}] Processing...")
                
                # Try AI extraction first
                page_transactions = []
                try:
                    pil_img = page.to_image(resolution=150).original
                    page_transactions = extract_page_with_gemini(pil_img, model)
                except Exception as e:
                    print(f"   [ERROR] AI extraction failed: {str(e)[:100]}")
                
                # Fallback to text extraction if AI fails
                if not page_transactions:
                    page_transactions = extract_text_fallback(page)
                
                if page_transactions:
                    all_transactions.extend(page_transactions)
                    print(f"   [RESULT] Extracted {len(page_transactions)} transactions\n")
                else:
                    print(f"   [RESULT] No transactions found\n")
                
                # Small delay to avoid rate limits
                if i < total_pages:
                    time.sleep(0.5)

    except Exception as e:
        error_msg = str(e).encode('ascii', 'ignore').decode('ascii')
        print(f"[ERROR] PDF processing failed: {error_msg}")
        return False, f"PDF processing error: {error_msg}"

    if progress_callback:
        progress_callback(98, "Saving to Excel...")

    if not all_transactions:
        print(f"\n[FAILED] No transactions extracted from any page")
        return False, "No transaction data extracted from the PDF."

    print(f"\n[SUCCESS] Total transactions extracted: {len(all_transactions)}")

    # Create DataFrame
    try:
        df = pd.DataFrame(all_transactions)
        df = df.dropna(how='all')
        
        # Clean numeric columns
        for col in ['Debit', 'Credit']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].replace('', None), errors='coerce')
                df[col] = df[col].fillna('').astype(str)
                df[col] = df[col].apply(lambda x: x.replace('.0', '') if x.endswith('.0') else x)

        # Save to Excel
        with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name="Transactions", index=False)
            
            # Auto-adjust columns
            worksheet = writer.sheets["Transactions"]
            for column in worksheet.columns:
                max_length = 0
                column_letter = column[0].column_letter
                for cell in column:
                    try:
                        if len(str(cell.value)) > max_length:
                            max_length = len(str(cell.value))
                    except:
                        pass
                adjusted_width = min(max_length + 2, 50)
                worksheet.column_dimensions[column_letter].width = adjusted_width

        print(f"[SAVED] Excel file: {excel_path}")
        print(f"[STATS] {len(df)} transactions, {len(df.columns)} columns")
        print(f"{'='*60}\n")
        
        return True, f"Successfully converted {len(df)} transactions"

    except Exception as e:
        error_msg = str(e).encode('ascii', 'ignore').decode('ascii')
        print(f"[ERROR] Excel save failed: {error_msg}")
        return False, f"Error saving Excel: {error_msg}"


if __name__ == "__main__":
    # Test
    pdf_file = "test.pdf"
    excel_file = "output.xlsx"
    api_key = "YOUR_API_KEY"
    
    success, msg = convert_pdf_to_excel(pdf_file, excel_file, api_key)
    print(f"\nResult: {msg}")