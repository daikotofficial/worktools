# Daikot Worktools

Daikot Worktools is a Python-based business operations suite. Its current live tool is a bank statement analysis workspace that:

- extracts transactions from PDF bank statements
- normalizes them into a common transaction schema
- separates inflows and outflows
- classifies transactions into business categories
- reconciles parsed totals against the source PDF totals
- exports a structured Excel workbook
- provides a local web interface for upload, analysis, review, correction, and download

The product is structured as a broader tool suite. Statement Analysis, Consolidation, and Train Model are separate workspaces under the same Daikot shell, so more business utilities can be added without renaming or restructuring the app.

The statement analysis engine is designed for multi-bank support. Different banks have different statement layouts, so the system is built around bank-specific parsers feeding one shared analysis pipeline.
When a PDF does not match a known bank/layout, the app now has a guarded adaptive fallback for text-based statements: it infers likely transaction columns, extracts rows, scores confidence, and only continues when the parse looks safe enough. Successful adaptive parses are now saved as reusable layout templates in `config/adaptive_layouts.json` so similar unseen statements can be recognized faster next time.

Current supported layouts in the repo include:

- Zenith-style
- UBA
- FCMB
- Providus
- FirstBank
- Fidelity Bank
- GTBank
- Wema Bank
- Wema Treasure individual statements
- Moniepoint business statements
- OPay wallet/savings statements
- Globus Bank
- Lotus Bank
- Standard Chartered
- TAJ Bank
- Jaiz Bank
- Customer Account Statement layout
- Unknown summary-details corporate layout
- Clear Junction signed-amount EUR statements

Adaptive fallback support:

- text-based unknown PDFs can attempt header/column inference automatically
- low-confidence parses are rejected instead of silently exporting bad data
- scanned/image-only statements still need OCR support

## Local web app

Run locally with:

```bash
cd "/home/rich2top/projects/MOTHerRePO/STATEMENT ANALYZER"
PYTHONPATH=".python_packages:src" python3 -m uvicorn statement_analyzer.webapp:app --reload --host 127.0.0.1 --port 8011
```

Then open:

- `http://127.0.0.1:8011`
- `http://127.0.0.1:8011/analyze-bank-statement`
- `http://127.0.0.1:8011/consolidate`
- `http://127.0.0.1:8011/train-analyzer`

To keep runtime uploads and generated Excel files somewhere other than `/tmp`, set:

```bash
STATEMENT_ANALYZER_RUNTIME_DIR="/path/to/runtime"
```

## Render deployment

Yes, this app can be deployed to Render as a Python web service. This repo includes `render.yaml`, so the fastest path is:

1. Push the project to GitHub.
2. In Render, create a new Blueprint from the repo.
3. Render will use:
   - build command: `pip install -r requirements.txt`
   - start command: `PYTHONPATH=src uvicorn statement_analyzer.webapp:app --host 0.0.0.0 --port $PORT`
   - health check: `/health`

Uploads and generated Excel files use `STATEMENT_ANALYZER_RUNTIME_DIR`. On Render, the included config points it to `/tmp/daikot-worktools`, which is fine for immediate processing and download. For long-term file retention, add a Render disk or object storage later.

The Render config uses the Standard instance and sets conservative processing limits:

```bash
STATEMENT_ANALYZER_MAX_UPLOAD_MB=50
STATEMENT_ANALYZER_MAX_PAGES=250
```

These limits prevent very large statements from exhausting the app instance. Set either value to `0` or `unlimited` only on a server with enough memory.

Detailed guide:

- `docs/render_deployment.md`

## Other hosting

This is a Python FastAPI app. For Whogohost, the recommended option is a Linux VPS with Nginx and systemd. See:

- `docs/whogohost_deployment.md`

## Command-line use

```bash
cd "/home/rich2top/projects/MOTHerRePO/STATEMENT ANALYZER"
PYTHONPATH=".python_packages:src" python3 -m statement_analyzer.cli "sample-statement.pdf" -o output_sample.xlsx
```

## Smarter classification

Default business-specific classification rules now live in:

- `config/business_rules.json`

This lets us keep making the classifier smarter without hard-coding everything into Python.

## Review and approval workflow

Low-confidence or unclassified rows now appear in the web review queue and in the exported workbook.

- approve corrections in the browser and regenerate the workbook
- optionally remember approved categories so future uploads learn from them
- edit `APPROVED CATEGORY` directly inside the `Inflows`, `Outflows`, or `Review` sheets
- keep `SUGGESTED CATEGORY` side by side with the applied result for auditability
- preserve reconciliation checks while improving category coverage

## Train Model

The Train Model page is for unsupported text-based PDF statement layouts.

- enter a clear layout name
- upload a representative PDF
- approve the detected columns if review is requested
- after validation, the adaptive layout is saved in `config/adaptive_layouts.json`
- future statements with the same structure can be matched and analyzed through the normal Statement Analysis flow

Scanned/image-only PDFs still need OCR support before they can be trained reliably.

## AdSense readiness

The app includes public footer links, contact information, privacy/terms pages, `robots.txt`, `sitemap.xml`, and optional `ads.txt` support.

Set these environment variables only after Google provides your AdSense details:

```bash
ADSENSE_CLIENT_ID="ca-pub-xxxxxxxxxxxxxxxx"
ADSENSE_PUBLISHER_ID="pub-xxxxxxxxxxxxxxxx"
```

If `ADSENSE_CLIENT_ID` is set, the Google ad script is loaded. If `ADSENSE_PUBLISHER_ID` is set, `/ads.txt` returns the authorized seller line.

Footer social links are controlled by:

```bash
SOCIAL_X_URL="https://x.com/daikotofficial"
SOCIAL_INSTAGRAM_URL="https://www.instagram.com/daikotofficial/"
SOCIAL_WHATSAPP_URL="https://wa.me/2349076669331"
SOCIAL_LINKEDIN_URL="https://www.linkedin.com/company/daikotofficial"
```

## Current focus

1. Tighten classification rules against the reference workbook.
2. Persist learned rules per business.
3. Add OCR support for scanned PDF statements.
4. Expand each supported bank/layout with more regression samples.
5. Save successful adaptive unknown-layout parses as reusable templates.
