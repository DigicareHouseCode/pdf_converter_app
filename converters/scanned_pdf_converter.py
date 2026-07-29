import google.generativeai as genai
import pdfplumber
import pandas as pd
import json
import os
import time
import re


def extract_data_with_gemini_correct(pdf_path, output_excel_path, api_key, max_pages=None, progress_callback=None):
    """CORRECT solution using Gemini API with proper prompt for Broadview statements."""
    print(f"Starting CORRECT Gemini extraction for: {pdf_path}")


    if not os.path.exists(pdf_path):
        print(f"[ERROR] File not found: {pdf_path}")
        return False, f"File not found: {pdf_path}"

    try:
        # Configure Gemini
        genai.configure(api_key=api_key)

        # Use gemini-1.5-flash for speed
        model = genai.GenerativeModel('gemini-2.5-pro')

        all_transactions = []

        # Open PDF and process each page
        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)
            
            # Application of page limit
            pages_to_process = pdf.pages
            if max_pages and total_pages > max_pages:
                print(f"[WARNING] Page limit applied: only processing first {max_pages} pages.")
                pages_to_process = pdf.pages[:max_pages]
                total_pages = max_pages

            # Prepare for parallel processing
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import threading

            completed_pages = 0
            # Lock for thread-safe list append and progress updates if needed
            lock = threading.Lock()
            
            def process_page(page_num, page):
                """Process a single page and return transactions"""
                print(f"[PAGE] Processing page {page_num}/{total_pages}...")

                # Convert page to image (low resolution for speed)
                # Reduced resolution slightly more for speed
                try:
                    img = page.to_image(resolution=72).original
                except Exception as e:
                    print(f"[ERROR] Page {page_num} image conversion failed: {e}")
                    return []

                # Create the CORRECT prompt (same as before)
                # Create the CORRECT prompt (Detailed and Robust)
                prompt_text = """
                ACT AS A FINANCIAL ANALYST. Your task is to extract Every Single Financial Transaction from this bank statement page image with 100% precision.

                DEEP ANALYSIS INSTRUCTIONS:
                1. VISUALLY SCAN the entire image to understand the layout. Identify the main transaction table.
                2. IGNORE summary tables at the top or bottom. Focus ONLY on the chronological list of transactions.
                3. IDENTIFY COLUMNS: Look for 'Date', 'Description' (or Details/Narration), 'Debit' (Withdrawals/Payments), 'Credit' (Deposits/Additions), and 'Balance'.
                4. READ EACH ROW carefully.
                   - If a description spans multiple lines, MERGE it into a single clean string.
                   - Ensure the 'Date' corresponds correctly to the 'Date' column.
                   - CRITICAL: Determine if the amount is a DEBIT (money leaving) or CREDIT (money entering).
                     * A value in the 'Debit', 'Withdrawal', or 'Payment' column is a DEBIT.
                     * A value in the 'Credit', 'Deposit', or 'Addition' column is a CREDIT.
                     * If there is a single 'Amount' column, look for a minus sign (-) or brackets () indicating a negative value (Debit).
                   - EXTRACT the Running Balance if present.

                JSON OUTPUT FORMAT (Strict Array of Objects):
                [
                  {
                    "Date": "MM/DD/YYYY",
                    "Description": "The full, complete description text from the statement",
                    "Amount": "The numeric amount. MUST be POSITIVE for Credits (Deposits) and NEGATIVE for Debits (Withdrawals).",
                    "Type": "Explicitly state 'DEBIT' or 'CREDIT'",
                    "Balance": "The running balance amount (if available)"
                  }
                ]

                STRICT RULES:
                - PRECISION: Do not miss any transaction. Do not hallucinate.
                - FORMATTING: 'Amount' must be a JSON Number or a String that can be easily parsed (e.g., "-1250.00", "50.00"). Remove '$' and commas.
                - DATES: Normalize dates to MM/DD/YYYY where possible. If year is missing, assume current year context or 2025.
                - NEGATIVE LOGIC: If you see "Withdrawal" header, all values in that column are NEGATIVE amounts. If "Deposit", all are POSITIVE.
                - NO MARKDOWN: Return ONLY the raw JSON array.
                """
                
                content = [prompt_text, img]

                # Try with retry logic
                max_retries = 3 # Reduced retries for parallel execution speed
                response = None

                for attempt in range(max_retries):
                    try:
                        # Use request_options to set a longer timeout
                        response = model.generate_content(
                            content,
                            generation_config={
                                "temperature": 0.1,
                                "max_output_tokens": 8000,
                            },
                            request_options={"timeout": 300}
                        )

                        if response and response.text:
                            break
                    except Exception as e:
                        error_msg = str(e)
                        if "429" in error_msg or "exhausted" in error_msg.lower():
                             # If quota exhausted, back off significantly
                             time.sleep(10 * (attempt + 1))
                        elif attempt < max_retries - 1:
                            time.sleep(2 * (attempt + 1))
                
                page_transactions = []
                if response and response.text:
                    response_text = response.text.strip()
                    json_data = extract_json_from_text(response_text)
                    
                    if json_data:
                        for item in json_data:
                            if isinstance(item, dict):
                                transaction = process_gemini_transaction(item)
                                if transaction:
                                    page_transactions.append(transaction)
                    else:
                        # Fallback
                        fallback = extract_fallback_transactions(response_text)
                        page_transactions.extend(fallback)
                
                return page_transactions

            # Use ThreadPoolExecutor for parallel processing
            # Max workers = 3 to avoid hitting rate limits too hard (Gemini free tier has limits)
            # Adjust based on your API tier
            max_workers = 3 
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_page = {executor.submit(process_page, i, page): i for i, page in enumerate(pages_to_process, 1)}
                
                for future in as_completed(future_to_page):
                    page_num = future_to_page[future]
                    try:
                        page_txns = future.result()
                        with lock:
                            all_transactions.extend(page_txns)
                            completed_pages += 1
                            progress = (completed_pages / total_pages) * 100
                            if progress_callback:
                                msg = f"Processed page {completed_pages}/{total_pages}"
                                progress_callback(progress, msg)
                            print(f"[SUCCESS] Page {page_num} finished. Found {len(page_txns)} transactions.")
                    except Exception as exc:
                        print(f"[ERROR] Page {page_num} generated an exception: {exc}")

        print(f"\n[STATS] Total transactions extracted: {len(all_transactions)}")

        if not all_transactions:
            print("[ERROR] No transactions extracted")
            return False, "No transactions could be extracted from the PDF"

        # Create DataFrame
        df = create_dataframe_from_transactions(all_transactions)

        # Save to Excel
        return save_to_excel(df, output_excel_path)

    except Exception as e:
        print(f"[ERROR] Error in Gemini extraction: {e}")
        return False, f"Unexpected error during extraction: {str(e)}"


def extract_json_from_text(text):
    """Extract JSON array from text response."""
    try:
        # Look for JSON array
        start = text.find('[')
        end = text.rfind(']') + 1

        if start != -1 and end != 0 and end > start:
            json_str = text[start:end]
            return json.loads(json_str)

        # Try to find JSON object
        start = text.find('{')
        end = text.rfind('}') + 1

        if start != -1 and end != 0 and end > start:
            json_str = text[start:end]
            data = json.loads(json_str)
            return [data] if isinstance(data, dict) else data

    except json.JSONDecodeError as e:
        print(f"JSON decode error: {e}")
        # Try to fix common JSON issues
        try:
            # Remove trailing commas
            text = re.sub(r',\s*}', '}', text)
            text = re.sub(r',\s*]', ']', text)
            return json.loads(text)
        except:
            return None
    except Exception as e:
        print(f"Error parsing JSON: {e}")

    return None


def process_gemini_transaction(item):
    """Process a transaction from Gemini response with improved accuracy."""
    try:
        date = item.get("Date", item.get("date", "")).strip()
        description = item.get("Description", item.get("description", "")).strip()
        amount_val = item.get("Amount", item.get("amount", ""))
        balance = item.get("Balance", item.get("balance", "")).strip()
        trans_type = item.get("Type", item.get("type", "")).upper()

        if not date or not description:
            return None

        # Clean and parse amount
        amount_num = 0.0
        try:
            if isinstance(amount_val, (int, float)):
                amount_num = float(amount_val)
            else:
                clean_amount = str(amount_val).replace('$', '').replace(',', '').strip()
                amount_num = float(clean_amount)
        except:
            return None

        # Determine Debit and Credit based on Type and Amount
        # DEBIT = Money out, CREDIT = Money in
        debit = ""
        credit = ""
        
        # Determine Debit and Credit based on Type and Amount
        # STANDARDIZED: DEBIT = Money out (Withdrawals), CREDIT = Money in (Deposits)
        debit = ""
        credit = ""
        
        if trans_type == "DEBIT" or amount_num < 0:
            debit = str(abs(amount_num)) 
            credit = ""
        elif trans_type == "CREDIT" or amount_num > 0:
            credit = str(abs(amount_num))
            debit = ""
        else:
            return None

        # Extract check number
        cheque_num = ""
        check_match = re.search(r'[Cc]heck\s*#?\s*(\d+)', description, re.IGNORECASE)
        if check_match:
            cheque_num = check_match.group(1)
        else:
            check_match2 = re.search(r'\b[Cc]heck\s+(\d{2,})\b', description)
            if check_match2:
                cheque_num = check_match2.group(1)

        # Clean description
        clean_desc = re.sub(r'\s+', ' ', description).strip()

        return {
            "Date": date,
            "Cheque #": cheque_num,
            "Description": clean_desc,
            "Debit": debit,
            "Credit": credit,
            "Payee": "",
            "Account": "Account",
            "OriginalAmount": str(amount_num),
            "ExtractedBalance": str(balance)
        }

    except Exception as e:
        print(f"Error processing transaction: {e}")
        return None


def extract_fallback_transactions(text):
    """Fallback extraction using regex patterns."""
    transactions = []

    # Pattern for transaction lines
    lines = text.split('\n')

    for line in lines:
        line = line.strip()

        # Look for patterns like: "06/02", "External Deposit...", "2282.95", "3223.62"
        pattern1 = r'"([^"]+)",\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)"'
        matches1 = re.findall(pattern1, line)

        for match in matches1:
            if len(match) == 4:
                date, desc, amount, balance = match
                trans = create_transaction_from_parts(date, desc, amount, balance)
                if trans:
                    transactions.append(trans)

        # Look for JSON-like objects
        pattern2 = r'\{"Date":\s*"([^"]+)",\s*"Description":\s*"([^"]+)",\s*"TransactionAmount":\s*"([^"]+)",\s*"Balance":\s*"([^"]+)"\}'
        matches2 = re.findall(pattern2, line)

        for match in matches2:
            if len(match) == 4:
                date, desc, amount, balance = match
                trans = create_transaction_from_parts(date, desc, amount, balance)
                if trans:
                    transactions.append(trans)

    return transactions


def create_transaction_from_parts(date, desc, amount, balance):
    """Create transaction from extracted parts."""
    try:
        # Clean amount
        clean_amount = amount.replace('$', '').replace(',', '').strip()

        # Determine Debit/Credit (Standard: Debit=Out, Credit=In)
        try:
            amount_num = float(clean_amount)
            if amount_num < 0:
                debit = str(abs(amount_num))
                credit = ""
            else:
                debit = ""
                credit = str(abs(amount_num))
        except:
            debit = ""
            credit = ""

        # Extract check number
        cheque_num = ""
        check_match = re.search(r'[Cc]heck\s*#?\s*(\d+)', desc, re.IGNORECASE)
        if check_match:
            cheque_num = check_match.group(1)

        return {
            "Date": date.strip(),
            "Cheque #": cheque_num,
            "Description": desc.strip(),
            "Debit": debit,
            "Credit": credit,
            "Payee": "",
            "Account": "Account",
            "OriginalAmount": clean_amount,
            "ExtractedBalance": balance.strip()
        }
    except:
        return None


def create_dataframe_from_transactions(transactions):
    """Create DataFrame from transactions list."""
    df = pd.DataFrame(transactions)

    # Ensure all required columns exist
    required_columns = ["Date", "Cheque #", "Description", "Debit", "Credit", "Payee", "Account"]
    for col in required_columns:
        if col not in df.columns:
            df[col] = ""

    # Convert numeric columns
    df["Debit"] = pd.to_numeric(df["Debit"], errors="coerce").fillna(0)
    df["Credit"] = pd.to_numeric(df["Credit"], errors="coerce").fillna(0)

    # Remove helper columns
    for col in ["OriginalAmount", "ExtractedBalance"]:
        if col in df.columns:
            df = df.drop(columns=[col])

    # Sort by date
    try:
        df['SortDate'] = pd.to_datetime(df['Date'] + '/2025', format='%m/%d/%Y', errors='coerce')
        df = df.sort_values('SortDate')
        df = df.drop(columns=['SortDate'])
    except:
        pass

    # Remove duplicates
    df = df.drop_duplicates(subset=['Date', 'Description', 'Debit', 'Credit'])

    # Reorder columns
    df = df[required_columns]

    return df


def save_to_excel(df, output_path):
    """Save DataFrame to Excel with formatting."""
    try:
        with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
            df.to_excel(writer, sheet_name='Transactions', index=False)

            workbook = writer.book
            worksheet = writer.sheets['Transactions']

            # Format headers
            header_format = workbook.add_format({
                'bold': True,
                'bg_color': '#4F81BD',
                'font_color': 'white',
                'border': 1,
                'align': 'center'
            })

            # Apply header format
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(0, col_num, value, header_format)

            # Format Debit and Credit columns
            money_format = workbook.add_format({'num_format': '#,##0.00'})

            debit_col = df.columns.get_loc("Debit")
            credit_col = df.columns.get_loc("Credit")

            for row in range(1, len(df) + 1):
                debit_val = df.iloc[row - 1]["Debit"]
                credit_val = df.iloc[row - 1]["Credit"]

                if debit_val != 0:
                    worksheet.write(row, debit_col, debit_val, money_format)
                if credit_val != 0:
                    worksheet.write(row, credit_col, credit_val, money_format)

            # Auto-adjust column widths
            for i, col in enumerate(df.columns):
                max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
                worksheet.set_column(i, i, min(max_len, 50))

            # Add summary
            summary_row = len(df) + 2
            summary_format = workbook.add_format({
                'bold': True,
                'bg_color': '#F2F2F2',
                'border': 1
            })

            worksheet.write(summary_row, 0, "SUMMARY", summary_format)
            worksheet.write(summary_row + 1, 0, "Total Debits (Withdrawals):")
            worksheet.write(summary_row + 1, 1, df['Debit'].sum(), money_format)
            worksheet.write(summary_row + 2, 0, "Total Credits (Deposits):")
            worksheet.write(summary_row + 2, 1, df['Credit'].sum(), money_format)
            worksheet.write(summary_row + 3, 0, "Transaction Count:")
            worksheet.write(summary_row + 3, 1, len(df))
            worksheet.write(summary_row + 4, 0, "Net Saving:")
            net_change = df['Credit'].sum() - df['Debit'].sum()
            worksheet.write(summary_row + 4, 1, net_change, money_format)


        return True, f"Successfully extracted {len(df)} transactions"

    except Exception as e:
        print(f"[ERROR] Error saving Excel: {e}")
        return False, f"Error saving Excel: {str(e)}"


# ALTERNATIVE: SIMPLE TEXT EXTRACTION (No AI)
def extract_with_simple_text(pdf_path, output_excel_path):
    """Simple text extraction without AI - might work better for some PDFs."""
    print("Using simple text extraction...")

    transactions = []

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue

                lines = text.split('\n')

                for line in lines:
                    line = line.strip()

                    # Look for transaction pattern with 4 columns
                    # Using more specific pattern for Broadview statements
                    pattern = r'^(\d{1,2}/\d{1,2})\s+(.+?)\s+(-?\$?\d{1,3}(?:,\d{3})*\.\d{2})\s+(-?\$?\d{1,3}(?:,\d{3})*\.\d{2})$'
                    match = re.search(pattern, line)

                    if match:
                        date, desc, amount, balance = match.groups()

                        # Process the transaction
                        trans = create_transaction_from_parts(date, desc, amount, balance)
                        if trans:
                            transactions.append(trans)
                            print(f"Found: {date} - {desc[:30]}... - {amount}")

    except Exception as e:
        print(f"Error in simple extraction: {e}")
        return False, f"Error in simple extraction: {str(e)}"

    if transactions:
        df = pd.DataFrame(transactions)
        df = df[["Date", "Cheque #", "Description", "Debit", "Credit", "Payee", "Account"]]
        df["Debit"] = pd.to_numeric(df["Debit"], errors="coerce").fillna(0)
        df["Credit"] = pd.to_numeric(df["Credit"], errors="coerce").fillna(0)
        try:
            df.to_excel(output_excel_path, index=False)
            print(f"[SUCCESS] Simple extraction saved {len(df)} transactions")
            return True, "Successfully extracted with simple text method"
        except Exception as e:
            return False, f"Error saving Excel in simple extraction: {str(e)}"

    return False, "Simple extraction found no transactions"


# MAIN FUNCTION TO USE
def extract_data_with_gemini_full(pdf_path, output_excel_path, api_key, max_pages=None, progress_callback=None):
    """Main function - tries Gemini first, falls back to simple extraction."""

    print("=" * 60)
    print("BROADVIEW FEDERAL CREDIT UNION STATEMENT EXTRACTION")
    print("=" * 60)

    # First try with Gemini
    print("\n[1/2] Trying Gemini API extraction...")
    success, msg = False, "Initialized"
    
    if api_key:
        try:
            success, msg = extract_data_with_gemini_correct(pdf_path, output_excel_path, api_key, max_pages=max_pages, progress_callback=progress_callback)
            if success:
                return True, msg
        except Exception as e:
            print(f"[ERROR] Gemini extraction main loop error: {e}")
            msg = f"Gemini extraction error: {str(e)}"
    else:
        msg = "No API Key provided"

    # If Gemini fails, try simple extraction
    print(f"\n[2/2] Gemini failed ({msg}), trying simple text extraction...")
    
    try:
        success_simple, msg_simple = extract_with_simple_text(pdf_path, output_excel_path)
        if success_simple:
            return True, msg_simple
    except Exception as e:
        msg_simple = f"Simple extraction error: {str(e)}"

    print("\n[ERROR] All extraction methods failed")
    return False, f"All methods failed. Gemini: {msg}. Simple: {msg_simple}"
