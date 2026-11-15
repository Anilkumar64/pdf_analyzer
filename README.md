# PDF Localizer – Banking Document Localization Engine

Fully local, layout-aware PDF localization engine built for banking applications.

**Goal**:  
Translate only human-readable **labels**, headings, terms & conditions and descriptive text into a user-selected Indian regional language, while keeping **every dynamic value** (loan numbers, amounts, names, dates, account numbers, reference numbers, etc.) exactly as they appear in the original PDF.

---

## What This Project Does

When a user uploads a banking PDF and selects a target language (Hindi, Telugu, Tamil, Marathi, etc.), the system:

1. Extracts all text with exact positions from the PDF
2. Classifies every piece of text as either **Label** or **Value**
3. Translates only the Labels (and Terms & Conditions)
4. Generates a new PDF that keeps the original layout, images, logos and all dynamic values unchanged

The engine is designed for typical banking document patterns such as:
- Payment / Transaction Advice
- Loan Statements / EMI Schedules
- Account Statements
- Certificates
- Sanction / Offer Letters
- Tax Invoices
- Admit Cards / Official forms

---

## Project Structure

```text
pdf_localizer/
├── main.py              # Entry point – runs the full pipeline
├── extractor.py         # Stage 1: Text + layout extraction
├── protector.py         # Stage 2: Label vs Value classification (core logic)
├── translator.py        # Stage 3: Local translation (NLLB-200)
├── generator.py         # Stage 4: PDF reconstruction + validation
├── fonts/               # Indian language fonts (Noto Sans family)
├── requirements.txt
└── README.md
```

---

## Libraries Used and Their Responsibility

| Library            | Responsibility                                                                 |
|--------------------|---------------------------------------------------------------------------------|
| **PyMuPDF (fitz)** | Reads PDF, extracts text spans with exact positions, redacts old text, writes new text, handles fonts and layout |
| **torch**          | Deep learning backend required by the translation model                        |
| **transformers**   | Loads and runs the NLLB-200 translation model                                  |
| **sentencepiece**  | Tokenizer used by NLLB-200                                                     |
| **Pillow**         | Image handling support (used internally by PyMuPDF in some cases)              |
| **regex**          | Advanced pattern matching for detecting numbers, dates, IDs, emails, etc.      |

---

## Pipeline Flow (How the libraries work together)

```text
User PDF + Target Language
          │
          ▼
┌─────────────────────────┐
│  1. extractor.py        │  Uses PyMuPDF
│  Extract all text spans │  → text + bbox + font size + page
│  with layout info       │
└─────────────────────────┘
          │
          ▼
┌─────────────────────────┐
│  2. protector.py        │  Uses regex + banking rules
│  Decide Label vs Value  │  → protect values, mark labels
│  Build placeholders     │
└─────────────────────────┘
          │
          ▼
┌─────────────────────────┐
│  3. translator.py       │  Uses torch + transformers + sentencepiece
│  Translate only labels  │  → NLLB-200 model (local)
│  Restore original values│
└─────────────────────────┘
          │
          ▼
┌─────────────────────────┐
│  4. generator.py        │  Uses PyMuPDF + fonts
│  Redact old text        │  → write translated text
│  Keep images & layout   │  → validate protected values
│  Save final PDF         │
└─────────────────────────┘
          │
          ▼
   Localized PDF
```

### Detailed Stage Responsibilities

**Stage 1 – Extraction (`extractor.py`)**
- Opens the PDF with PyMuPDF
- Extracts every text span along with its exact coordinates, font size, font name and color
- Output: structured list of text pieces with layout information

**Stage 2 – Protection (`protector.py`)** – Core Logic
- Classifies each span as Label or Value
- Strong rules for banking documents:
  - Values (never translated): numbers, amounts, dates, loan/account/reference numbers, names, emails, phones, GSTIN, IFSC, addresses
  - Labels (translated): field names, table headers, section titles
  - Terms & Conditions / Disclaimers / Instructions → translated
- Creates placeholders so original values can be restored after translation
- Safer default: when unsure → treat as Value (protect)

**Stage 3 – Translation (`translator.py`)**
- Loads the local NLLB-200 model (facebook/nllb-200-distilled-600M)
- Sends only the marked labels for translation
- Restores all original values exactly
- If a placeholder is lost during translation → keeps original English (safety fallback)

**Stage 4 – Generation (`generator.py`)**
- Uses the original PDF as base
- Truly redacts only the text that needs to be replaced (images, logos, lines stay)
- Writes the new translated text using proper Indian language fonts (Noto Sans)
- Validates that every protected value is still present in the final PDF
- Saves the localized PDF

---

## Supported Languages

| Language   | Code          |
|------------|---------------|
| Hindi      | hindi / hi    |
| Telugu     | telugu / te   |
| Marathi    | marathi / mr  |
| Tamil      | tamil / ta    |
| Kannada    | kannada / kn  |
| Malayalam  | malayalam / ml|
| Bengali    | bengali / bn  |
| Gujarati   | gujarati / gu |
| Punjabi    | punjabi / pa  |

---

## Setup Instructions

### 1. Go to the project folder

```bash
cd pdf_localizer
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv venv
source venv/bin/activate          # Linux / Mac
# or
venv\Scripts\activate             # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Indian language fonts (important)

Download Noto Sans fonts and place them in the `fonts/` folder:

- NotoSansDevanagari-Regular.ttf  (Hindi / Marathi)
- NotoSansTelugu-Regular.ttf
- NotoSansTamil-Regular.ttf
- NotoSansKannada-Regular.ttf
- NotoSansMalayalam-Regular.ttf
- NotoSansBengali-Regular.ttf
- NotoSansGujarati-Regular.ttf
- NotoSansGurmukhi-Regular.ttf    (Punjabi)
- NotoSans-Regular.ttf            (Latin fallback)

You can get them from Google Fonts / Noto fonts repository.

On Ubuntu/Debian you can also install system-wide:

```bash
sudo apt install fonts-noto-core fonts-noto-ui-core
```

### 5. First run note

On the first run the NLLB-200 model (~2.5 GB) will be downloaded automatically by `transformers`.  
This happens only once.

---

## How to Run

```bash
# Basic usage
python main.py "path/to/document.pdf" telugu

# Specify output file
python main.py "payment_advice.pdf" hindi -o output_hindi.pdf

# Test protection + generation without loading the translation model
python main.py "invoice.pdf" telugu --skip-translation
```

### Example

```bash
python main.py "Laptop bill document.pdf" hindi
python main.py "CS26S35015674_AdmitCard.pdf" tamil
```

---

## Important Design Principles

1. **Never translate values** – Loan numbers, amounts, names, dates, account numbers must stay exactly the same.
2. **Safer default** – When the system is unsure whether something is a label or a value, it protects it (does not translate).
3. **Fully local** – No external translation API. Everything runs on the machine after the one-time model download.
4. **Layout preservation** – Original positions, images, logos and table structures are kept as much as possible.
5. **Banking focused** – Rules are tuned for typical banking document patterns (payment advice, statements, certificates, etc.).

---

## Validation

After generating the localized PDF the system automatically checks:

- Every protected value is still present exactly
- Old English text has been removed from rewritten spans
- No rewritten span is left empty

A validation report is printed at the end of every run.

---

## Notes for Production Use

- The protection logic (Stage 2) is the most critical part. It can be improved further by adding more banking-specific label patterns as you encounter new document types.
- For high volume, the translation model can be loaded once and kept in memory (or served via a small API).
- Font quality strongly affects the visual result. Always use good Noto Sans fonts for Indian scripts.
