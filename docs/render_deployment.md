# Deploying Daikot Worktools on Render

Render is the recommended host for this project because Daikot Worktools is a Python FastAPI web service that parses PDFs, runs background analysis jobs, and creates temporary Excel downloads.

## Why Render Over Vercel

Render runs this app as a persistent Python web service with Uvicorn. That fits the current architecture.

Vercel is excellent for frontend apps and serverless APIs, but this project is not a typical serverless workload. The app benefits from a long-running process, local runtime folders, and background job state while files are being analyzed.

## Files Render Uses

The project already includes `render.yaml`.

Render will use:

```bash
pip install -r requirements.txt
```

as the build command, and:

```bash
PYTHONPATH=src uvicorn statement_analyzer.webapp:app --host 0.0.0.0 --port $PORT
```

as the start command.

## Deployment Steps

1. Push this project to a GitHub repository.
2. Confirm sample PDFs and Excel files are not committed. They are already covered by `.gitignore`.
3. Go to Render and create a new Blueprint from the repository.
4. Render should detect `render.yaml`.
5. Deploy the `daikot-worktools` web service.
6. Open the generated Render URL and check `/health`.

## Environment Variables

Required:

```bash
STATEMENT_ANALYZER_RUNTIME_DIR=/tmp/daikot-worktools
PYTHON_VERSION=3.11.9
STATEMENT_ANALYZER_MAX_UPLOAD_MB=50
STATEMENT_ANALYZER_MAX_PAGES=250
```

Optional for AdSense:

```bash
ADSENSE_CLIENT_ID=ca-pub-xxxxxxxxxxxxxxxx
ADSENSE_PUBLISHER_ID=pub-xxxxxxxxxxxxxxxx
```

Optional for footer social links:

```bash
SOCIAL_X_URL=
SOCIAL_FACEBOOK_URL=
SOCIAL_LINKEDIN_URL=
SOCIAL_INSTAGRAM_URL=
SOCIAL_TIKTOK_URL=
```

If social URLs are not set, the footer social buttons route to the Contact page instead of fake accounts.

## Standard Plan Notes

The free plan is good for testing and a first public preview, but it has a 512 MB memory ceiling. That is too tight for larger PDF bank statements because text extraction can temporarily use much more memory than the PDF file size suggests.

The included Render config uses the Standard instance and limits statement analysis to 50 MB PDFs and 250 pages by default. These values can be raised later with `STATEMENT_ANALYZER_MAX_UPLOAD_MB` and `STATEMENT_ANALYZER_MAX_PAGES`, but keeping a ceiling protects the app from malformed, scanned, or unusually memory-heavy PDFs.

If the service was created manually in the Render dashboard, confirm the instance type there is also set to Standard. The `render.yaml` value applies cleanly when Render is managing the service from the blueprint.
