import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PyPDF2 import PdfReader, PdfWriter
import pdfplumber
import pytesseract
from PIL import Image
import PyPDF2
import re
import os
import sys
import shutil

# ---- Detect Tesseract for EXE / Source Code ----
if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS  # EXE mode
else:
    base_path = os.path.dirname(__file__)  # .py mode

# Try using bundled Tesseract first
bundled_tesseract = os.path.join(base_path, "tesseract", "tesseract.exe")

if os.path.exists(bundled_tesseract):
    pytesseract.pytesseract.tesseract_cmd = bundled_tesseract
else:
    # If not bundled, check if system Tesseract exists (installed)
    if shutil.which("tesseract"):
        pytesseract.pytesseract.tesseract_cmd = shutil.which("tesseract")
    else:
        messagebox.showerror(
            "Tesseract Missing",
            "Tesseract OCR not found."
        )
        sys.exit()  # Stop the app


# -------------------------------------------------------------
# 1. Extract drawing info from text block
# -------------------------------------------------------------
def extract_drawing_info_from_text(text):
    # ---- Extract drawing number ----
    # drg_pattern = r"DRAWING NO\.\s*:\s*([A-Z0-9\-]+)"
    drg_pattern = r"DRAWING NO\.?\s*:?\s*([A-Z0-9\-/]+)"
    drg_match = re.search(drg_pattern, text, flags=re.IGNORECASE | re.DOTALL)


    drawing_no = drg_match.group(1).strip() if drg_match else None

    # ---- Extract revision ----
    # Revision may be blank (REVISION:  )
    rev_pattern = r"REVISION\s*:?\s*([A-Z]?)\b"
    rev_match = re.search(rev_pattern, text, flags=re.IGNORECASE)

    revision = rev_match.group(1).strip() if rev_match else None

    # if revision is empty → treat as None
    if revision == "":
        revision = None

    return drawing_no, revision

# -------------------------------------------------------------
# 2A. Extract drawing no + revision from 1-page PDF
# -------------------------------------------------------------
def extract_drawing_info(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]  # only 1 page in split file

        width = page.width
        height = page.height

        # Crop rightmost 25%
        right_crop = (width * 0.75, 0, width, height)
        right_side = page.crop(right_crop)

        text = right_side.extract_text() or ""

        drawing_no, revision = extract_drawing_info_from_text(text)

        return drawing_no, revision

# -------------------------------------------------------------
# 2B. Extract drawing info using OCR (for non-selectable PDFs)
# -------------------------------------------------------------
def get_revision_from_ocr_table(img):
    text = pytesseract.image_to_string(img, config="--psm 6").upper()
    
    if "FOR TENDER ONLY" not in text:
        print("Table 'FOR TENDER ONLY' NOT found.")
        return None

    print("Table detected – searching latest revision...")

    # Split text into lines for row scanning
    lines = text.split("\n")

    pattern = r"(\d{2}[-\.]\d{2}[-\.]\d{4})\s+([A-Z])"  # date + revision
    rows = []
    
    table_started = False

    for line in lines:
        if "FOR TENDER ONLY" in line:
            table_started = True
            continue  # move to next line after table title
        
        if table_started:
            line = line.strip()

            # 📌 If line is fully EMPTY = table is finished → stop searching
            if line == "":
                print("Table ended. Stopped scanning further.")
                break
            
            # 🔎 Try match date + revision
            match = re.search(pattern, line)
            if match:
                rows.append(match.groups())
            else:
                # If line has no date syntax, stop scanning
                print(f"No date found → table ended at: '{line}'")
                break

    # If no rows found
    if not rows:
        print("No valid revision rows found in table.")
        return None

    # Find the latest date
    latest_date = None
    latest_rev = None
    from datetime import datetime

    for d, r in rows:
        try:
            current = datetime.strptime(d, "%d.%m.%Y")
            if latest_date is None or current > latest_date:
                latest_date = current
                latest_rev = r
        except:
            continue

    print(f"✔ Final revision found: {latest_rev}")
    return latest_rev



# -------------------------------------------------------------
# 2C. Extract drawing info using OCR (for non-selectable PDFs)
# -------------------------------------------------------------
def extract_drawing_info_ocr(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]  # Only 1 page

        # Crop right 25% area
        width, height = page.width, page.height
        crop_box = (width * 0.75, 0, width, height)
        cropped = page.crop(crop_box)

        # Convert to image
        img = cropped.to_image(resolution=300).original

        # Run OCR
        text = ""
        text = pytesseract.image_to_string(img)
        if not text:
            return None, None

        # Extract info using your existing logic
        drawing_no, revision = extract_drawing_info_from_text(text)

        if not revision:
            revision = get_revision_from_ocr_table(img)

        return drawing_no, revision


# -------------------------------------------------------------
# 3. Split PDF into pages and rename each
# -------------------------------------------------------------
def sanitize_filename(filename):
    """Replace invalid Windows filename characters with underscore"""
    return re.sub(r'[<>:"/\\|?*]', '_', filename)

def split_and_rename(pdf_path, output_folder, mode, log_func):
    os.makedirs(output_folder, exist_ok=True)

    reader = PyPDF2.PdfReader(pdf_path)
    total_pages = len(reader.pages)
    log_func(f"Total pages: {total_pages}\n")

    for i in range(total_pages):
        writer = PyPDF2.PdfWriter()
        writer.add_page(reader.pages[i])

        # Temp split file
        temp_path = os.path.join(output_folder, f"page_{i+1}.pdf")
        with open(temp_path, "wb") as f:
            writer.write(f)

        # Select mode
        if mode == "selectable":
            drawing_no, revision = extract_drawing_info(temp_path)
        else:
            drawing_no, revision = extract_drawing_info_ocr(temp_path)

        if not drawing_no:
            log_func(f"⚠️ Page {i+1}: Drawing number NOT FOUND\n")
            continue

        # Build final filename
        if revision:
            raw_name = f"{drawing_no}-{revision}"
        else:
            raw_name = drawing_no

        new_name = sanitize_filename(raw_name) + ".pdf"

        new_path = os.path.join(output_folder, new_name)

        # If duplicate name exists, add counter
        counter = 1
        base_name = new_name[:-4]
        while os.path.exists(new_path):
            new_path = os.path.join(output_folder, f"{base_name}_{counter}.pdf")
            counter += 1

        os.rename(temp_path, new_path)

        log_func(f"✔ Page {i+1} renamed → {os.path.basename(new_path)}\n")
 
    messagebox.showinfo("Completed", "Splitting and renaming complete!")


# -------------------------------------------------------------
# 4. GUI
# -------------------------------------------------------------
def create_gui():
    root = tk.Tk()
    root.title("PDF Split & Rename Tool")
    root.geometry("700x505")
    root.resizable(False, False)

    # ---- Variables ----
    source_pdf = tk.StringVar()
    output_folder = tk.StringVar()
    mode = tk.StringVar(value="selectable")

    # ---- Scrollable Frame ----
    main_frame = ttk.Frame(root)
    main_frame.pack(fill="both", expand=True)

    canvas = tk.Canvas(main_frame)
    scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
    scrollable_frame = ttk.Frame(canvas)

    scrollable_frame.bind(
        "<Configure>",
        lambda e: canvas.configure(
            scrollregion=canvas.bbox("all")
        )
    )

    canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    # ---- Layout ----

    # Source PDF
    ttk.Label(scrollable_frame, text="PDF File:").pack(anchor="w", padx=10, pady=(10, 0))
    frame_pdf = ttk.Frame(scrollable_frame)
    frame_pdf.pack(fill="x", padx=10)
    ttk.Entry(frame_pdf, textvariable=source_pdf, width=55).pack(side="left", padx=(0,5))
    ttk.Button(frame_pdf, text="Browse", command=lambda: source_pdf.set(filedialog.askopenfilename(
        filetypes=[("PDF Files", "*.pdf")]
    ))).pack(side="left")

    # Output folder
    ttk.Label(scrollable_frame, text="Output Folder:").pack(anchor="w", padx=10, pady=(10, 0))
    frame_output = ttk.Frame(scrollable_frame)
    frame_output.pack(fill="x", padx=10)
    ttk.Entry(frame_output, textvariable=output_folder, width=55).pack(side="left", padx=(0,5))
    ttk.Button(frame_output, text="Browse", command=lambda: output_folder.set(filedialog.askdirectory())).pack(side="left")

    # Radio buttons
    ttk.Label(scrollable_frame, text="PDF Type:").pack(anchor="w", padx=10, pady=(10, 0))
    ttk.Radiobutton(scrollable_frame, text="Selectable Text", variable=mode, value="selectable").pack(anchor="w", padx=25)
    ttk.Radiobutton(scrollable_frame, text="Non-Selectable Text", variable=mode, value="nonselectable").pack(anchor="w", padx=25)

    # Log window with scrollbar
    ttk.Label(scrollable_frame, text="Message:").pack(anchor="w", padx=10, pady=(10,0))
    log_frame = ttk.Frame(scrollable_frame)
    log_frame.pack(fill="both", expand=True, padx=10, pady=5)
    log_box = tk.Text(log_frame, height=15)
    log_box.pack(side="left", fill="both", expand=True)
    log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=log_box.yview)
    log_scroll.pack(side="right", fill="y")
    log_box.configure(yscrollcommand=log_scroll.set)

    def write_log(msg):
        log_box.insert(tk.END, msg + "\n")
        log_box.see(tk.END)

    # RUN Button
    def run():
        if not source_pdf.get():
            messagebox.showerror("Error", "Please select a source PDF file.")
            return
        if not output_folder.get():
            messagebox.showerror("Error", "Please select an output folder.")
            return

        log_box.delete("1.0", tk.END)  # Clear log window
        write_log("Starting...\n")
        root.update_idletasks()

        # Call processing function
        split_and_rename(source_pdf.get(), output_folder.get(), mode.get(), write_log)

        source_pdf.set("")  # Clear Source PDF path after completed

    ttk.Button(scrollable_frame, text="Run", command=run).pack(pady=5)

    root.mainloop()


# -------------------
# Start UI
# -------------------
if __name__ == "__main__":
    create_gui()


