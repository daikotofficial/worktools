from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import logging
import os
import shutil
import time
from threading import Lock
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from statement_analyzer.consolidation import (
    ConsolidationResult,
    WorkbookPreview,
    consolidate_analyzed_workbooks,
    inspect_analyzed_workbook,
)
from statement_analyzer.models import TransactionDirection
from statement_analyzer.parsers.generic import AdaptiveReviewRequired, load_adaptive_templates
from statement_analyzer.parsers.pdf_utils import is_password_error
from statement_analyzer.service import (
    AdaptiveReviewSummary,
    AnalysisSummary,
    StatementAnalysisService,
    category_options_for,
    save_custom_category_option,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = PROJECT_ROOT / 'templates'
STATIC_DIR = PROJECT_ROOT / 'static'
RUNTIME_DIR = Path(os.getenv('STATEMENT_ANALYZER_RUNTIME_DIR', '/tmp/statement_analyzer'))
UPLOAD_DIR = RUNTIME_DIR / 'uploads'
OUTPUT_DIR = RUNTIME_DIR / 'outputs'
CONSOLIDATION_UPLOAD_DIR = RUNTIME_DIR / 'consolidation_uploads'
ADSENSE_CLIENT_ID = os.getenv('ADSENSE_CLIENT_ID', '').strip()
ADSENSE_PUBLISHER_ID = os.getenv('ADSENSE_PUBLISHER_ID', '').strip()
SOCIAL_LINKS = (
    ("X", os.getenv("SOCIAL_X_URL", "").strip()),
    ("Facebook", os.getenv("SOCIAL_FACEBOOK_URL", "").strip()),
    ("LinkedIn", os.getenv("SOCIAL_LINKEDIN_URL", "").strip()),
    ("Instagram", os.getenv("SOCIAL_INSTAGRAM_URL", "").strip()),
    ("TikTok", os.getenv("SOCIAL_TIKTOK_URL", "").strip()),
)

for directory in (UPLOAD_DIR, OUTPUT_DIR, CONSOLIDATION_UPLOAD_DIR):
    directory.mkdir(parents=True, exist_ok=True)

PRODUCT_NAME = 'Daikot Worktools'
app = FastAPI(title=PRODUCT_NAME)
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
service = StatementAnalysisService()
logger = logging.getLogger("uvicorn.error")
logger.setLevel(logging.INFO)

SUPPORTED_LAYOUTS = (
    {"bank": "Zenith-style", "status": "Live", "notes": "Reconciles cleanly and exports classified workbooks."},
    {"bank": "UBA", "status": "Live", "notes": "Header totals and balances reconcile exactly."},
    {"bank": "FCMB", "status": "Live", "notes": "Summary/details layout supported end to end."},
    {"bank": "Providus", "status": "Live", "notes": "Multiline remarks layout supported."},
    {"bank": "FirstBank", "status": "Live", "notes": "Large personal-statement layout now parses across all pages."},
    {"bank": "Fidelity Bank", "status": "Live", "notes": "Business-account statements now parse with wrapped Online Banking continuations and reconciled opening/closing balances."},
    {"bank": "GTBank", "status": "Live", "notes": "Cross-page remarks and totals now parse correctly."},
    {"bank": "Globus Bank", "status": "Live", "notes": "Single-page corporate summary layout supported."},
    {"bank": "Lotus Bank", "status": "Live", "notes": "Bundled multi-period statement exports now parse into one normalized flow."},
    {"bank": "Standard Chartered", "status": "Live", "notes": "Savings-account statements now parse with carry-forward dates, wrapped descriptions, and reconciled balances."},
    {"bank": "TAJ Bank", "status": "Live", "notes": "Corporate current-account rows now parse and reconcile directly from the fixed-width layout."},
    {"bank": "Jaiz Bank", "status": "Live", "notes": "OCR-noisy corporate statement rows now parse with wrapped narration and repaired split balances."},
    {"bank": "Customer Account Statement Layout", "status": "Live", "notes": "Pdfmake-generated DATE / REFERENCE / NARRATION statements now parse with split-date rows, wrapped narration, and reconciled totals."},
    {"bank": "Unknown Summary Layout", "status": "Live", "notes": "Corporate withdrawals/lodgements statements now parse with summary metadata and cross-page detail attachment."},
    {"bank": "Clear Junction", "status": "Live", "notes": "Signed Amount rows are split into inflow/outflow columns with transaction fees kept separate."},
    {"bank": "Wema Treasure", "status": "Live", "notes": "Individual Wema Treasure statements parse with account metadata, totals, and balances."},
    {"bank": "Moniepoint", "status": "Live", "notes": "Business account statement exports parse across long multi-page PDFs with timestamped transaction rows."},
    {"bank": "Adaptive Unknown PDF", "status": "Guarded", "notes": "New text-based statements can now fall back to header inference, confidence-gated parsing, and saved reusable layout templates."},
)

PENDING_LAYOUTS = ()


@dataclass(slots=True)
class JobState:
    filename: str
    pdf_path: Path
    output_path: Path
    pdf_password: str | None = None
    training_bank_name: str | None = None
    status: str = 'queued'
    summary: AnalysisSummary | None = None
    adaptive_review: AdaptiveReviewSummary | None = None
    error: str | None = None


@dataclass(slots=True)
class ConsolidationSession:
    files: list[WorkbookPreview]
    output_path: Path
    result: ConsolidationResult | None = None
    error: str | None = None


_jobs: dict[str, JobState] = {}
_jobs_lock = Lock()
_analysis_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='statement-analysis')
_consolidation_sessions: dict[str, ConsolidationSession] = {}
_consolidation_lock = Lock()


def page_context(
    request: Request,
    *,
    summary=None,
    download_token: str | None = None,
    active_job_id: str | None = None,
    active_job_status: str | None = None,
    active_job_kind: str | None = None,
    filename: str | None = None,
    error: str | None = None,
    adaptive_review: AdaptiveReviewSummary | None = None,
    adaptive_review_token: str | None = None,
    training_bank_name: str | None = None,
    training_mode: bool = False,
    active_page: str | None = None,
    header_cta_href: str | None = None,
    header_cta_label: str | None = None,
) -> dict[str, object]:
    resolved_active_page = active_page or ('training' if training_mode else 'analysis')
    return {
        'request': request,
        'product_name': PRODUCT_NAME,
        'adsense_client_id': ADSENSE_CLIENT_ID,
        'social_links': social_links_for_page(),
        'active_page': resolved_active_page,
        'header_cta_href': header_cta_href or ('/analyze-bank-statement' if training_mode else '/consolidate'),
        'header_cta_label': header_cta_label or ('Analyze Statement' if training_mode else 'Consolidate'),
        'summary': summary,
        'download_token': download_token,
        'active_job_id': active_job_id,
        'active_job_status': active_job_status,
        'active_job_kind': active_job_kind,
        'filename': filename,
        'error': error,
        'adaptive_review': adaptive_review,
        'adaptive_review_token': adaptive_review_token,
        'training_bank_name': training_bank_name,
        'training_mode': training_mode,
        'supported_layouts': supported_layouts_for_page(),
        'pending_layouts': PENDING_LAYOUTS,
    }


def supported_layouts_for_page() -> tuple[dict[str, str], ...]:
    layouts = list(SUPPORTED_LAYOUTS)
    known_names = {layout["bank"].lower() for layout in layouts}
    try:
        adaptive_templates = load_adaptive_templates()
    except Exception:
        adaptive_templates = []

    for template in adaptive_templates:
        name = template.name.strip()
        if not name or name.startswith("adaptive-template:") or name == "adaptive-line-fallback":
            continue
        if name.lower() in known_names:
            continue
        layouts.append(
            {
                "bank": name,
                "status": "Trained",
                "notes": "Learned from an approved unknown-layout training run and reused by the adaptive parser.",
            }
        )
        known_names.add(name.lower())
    return tuple(layouts)


def consolidation_page_context(
    request: Request,
    *,
    files: list[WorkbookPreview] | None = None,
    result: ConsolidationResult | None = None,
    session_token: str | None = None,
    manual_required: bool = False,
    error: str | None = None,
) -> dict[str, object]:
    return {
        'request': request,
        'product_name': PRODUCT_NAME,
        'adsense_client_id': ADSENSE_CLIENT_ID,
        'social_links': social_links_for_page(),
        'active_page': 'consolidation',
        'header_cta_href': '/analyze-bank-statement',
        'header_cta_label': 'Analyze Statement',
        'files': files or [],
        'result': result,
        'session_token': session_token,
        'manual_required': manual_required,
        'error': error,
    }


def refresh_summary_review_options(summary: AnalysisSummary) -> AnalysisSummary:
    for row in summary.review_rows:
        row.category_options = list(category_options_for(TransactionDirection(row.direction)))
    return summary


def social_links_for_page() -> tuple[dict[str, str | bool], ...]:
    return tuple(
        {"label": label, "href": href or "/contact", "is_external": bool(href)}
        for label, href in SOCIAL_LINKS
    )


@app.get('/', response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name='landing.html',
        context=page_context(
            request,
            active_page='home',
            header_cta_href='/analyze-bank-statement',
            header_cta_label='Start Analysis',
        ),
    )


@app.get('/analyze-bank-statement', response_class=HTMLResponse)
async def analyze_workspace(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name='analyze.html',
        context=page_context(request),
    )


@app.get('/train-analyzer', response_class=HTMLResponse)
async def training_workspace(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name='analyze.html',
        context=page_context(request, training_mode=True),
    )


@app.get('/afs', response_class=HTMLResponse)
async def afs_workspace(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name='coming_soon.html',
        context=page_context(
            request,
            active_page='afs',
            header_cta_href='/analyze-bank-statement',
            header_cta_label='Start Analysis',
        ),
    )


@app.get('/privacy', response_class=HTMLResponse)
async def privacy_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name='privacy.html',
        context=page_context(
            request,
            active_page='privacy',
            header_cta_href='/analyze-bank-statement',
            header_cta_label='Start Analysis',
        ),
    )


@app.get('/terms', response_class=HTMLResponse)
async def terms_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name='terms.html',
        context=page_context(
            request,
            active_page='terms',
            header_cta_href='/analyze-bank-statement',
            header_cta_label='Start Analysis',
        ),
    )


@app.get('/contact', response_class=HTMLResponse)
async def contact_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name='contact.html',
        context=page_context(
            request,
            active_page='contact',
            header_cta_href='/analyze-bank-statement',
            header_cta_label='Start Analysis',
        ),
    )


@app.get('/robots.txt', response_class=PlainTextResponse)
async def robots_txt(request: Request) -> PlainTextResponse:
    base_url = str(request.base_url).rstrip('/')
    return PlainTextResponse(f"User-agent: *\nAllow: /\nSitemap: {base_url}/sitemap.xml\n")


@app.get('/ads.txt', response_class=PlainTextResponse)
async def ads_txt() -> PlainTextResponse:
    if not ADSENSE_PUBLISHER_ID:
        return PlainTextResponse("")
    return PlainTextResponse(f"google.com, {ADSENSE_PUBLISHER_ID}, DIRECT, f08c47fec0942fa0\n")


@app.get('/sitemap.xml', response_class=PlainTextResponse)
async def sitemap_xml(request: Request) -> PlainTextResponse:
    base_url = str(request.base_url).rstrip('/')
    paths = ('/', '/analyze-bank-statement', '/consolidate', '/train-analyzer', '/afs', '/privacy', '/terms', '/contact')
    urls = "\n".join(f"  <url><loc>{base_url}{path}</loc></url>" for path in paths)
    return PlainTextResponse(
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}\n</urlset>\n',
        media_type='application/xml',
    )


@app.get('/consolidate', response_class=HTMLResponse)
async def consolidate_workspace(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name='consolidate.html',
        context=consolidation_page_context(request),
    )


@app.post('/consolidate', response_class=HTMLResponse)
async def prepare_consolidation(
    request: Request,
    statements: list[UploadFile] = File(...),
) -> HTMLResponse:
    if not statements:
        return templates.TemplateResponse(
            request=request,
            name='consolidate.html',
            context=consolidation_page_context(
                request,
                error='Please upload at least one analyzed Excel workbook.',
            ),
            status_code=400,
        )

    session_token = uuid.uuid4().hex
    session_dir = CONSOLIDATION_UPLOAD_DIR / session_token
    session_dir.mkdir(parents=True, exist_ok=True)
    previews: list[WorkbookPreview] = []

    for index, statement in enumerate(statements, start=1):
        filename = Path(statement.filename or f'analyzed-statement-{index}.xlsx').name
        if Path(filename).suffix.lower() not in {'.xlsx', '.xlsm'}:
            return templates.TemplateResponse(
                request=request,
                name='consolidate.html',
                context=consolidation_page_context(
                    request,
                    files=previews,
                    error='Please upload only analyzed Excel workbooks with .xlsx or .xlsm extensions.',
                ),
                status_code=400,
            )

        workbook_path = session_dir / f'{index}_{filename}'
        with workbook_path.open('wb') as buffer:
            shutil.copyfileobj(statement.file, buffer)

        try:
            previews.append(inspect_analyzed_workbook(workbook_path, filename=filename))
        except Exception:
            logger.exception("Failed to inspect workbook %s", filename)
            return templates.TemplateResponse(
                request=request,
                name='consolidate.html',
                context=consolidation_page_context(
                    request,
                    files=previews,
                    error=f'{filename} could not be read as an analyzed workbook. Please upload the Excel files downloaded from this app.',
                ),
                status_code=400,
            )

    output_path = OUTPUT_DIR / f'CONSOLIDATED_{session_token}.xlsx'
    session = ConsolidationSession(files=previews, output_path=output_path)
    with _consolidation_lock:
        _consolidation_sessions[session_token] = session

    if any(preview.needs_manual_details for preview in previews):
        return templates.TemplateResponse(
            request=request,
            name='consolidate.html',
            context=consolidation_page_context(
                request,
                files=previews,
                session_token=session_token,
                manual_required=True,
            ),
        )

    try:
        result = consolidate_analyzed_workbooks(previews, output_path)
    except Exception as exc:
        session.error = str(exc)
        return templates.TemplateResponse(
            request=request,
            name='consolidate.html',
            context=consolidation_page_context(
                request,
                files=previews,
                session_token=session_token,
                error=f'Consolidation failed: {exc}',
            ),
            status_code=400,
        )

    with _consolidation_lock:
        session.result = result
    return templates.TemplateResponse(
        request=request,
        name='consolidate.html',
        context=consolidation_page_context(
            request,
            files=previews,
            result=result,
            session_token=session_token,
        ),
    )


@app.post('/consolidate/{token}/complete', response_class=HTMLResponse)
async def complete_consolidation(token: str, request: Request) -> HTMLResponse:
    session = get_consolidation_session(token)
    if not session:
        raise HTTPException(status_code=404, detail='Consolidation session not found.')

    form = await request.form()
    overrides: dict[int, tuple[str, str]] = {}
    missing_files: list[str] = []
    for index, preview in enumerate(session.files):
        bank_name = str(form.get(f'bank_{index}', preview.bank_name or '')).strip()
        account_number = str(form.get(f'account_{index}', preview.account_number or '')).strip()
        if not bank_name or not account_number:
            missing_files.append(preview.filename)
        overrides[index] = (bank_name, account_number)

    if missing_files:
        return templates.TemplateResponse(
            request=request,
            name='consolidate.html',
            context=consolidation_page_context(
                request,
                files=session.files,
                session_token=token,
                manual_required=True,
                error='Please provide both bank name and account number for every uploaded workbook.',
            ),
            status_code=400,
        )

    try:
        result = consolidate_analyzed_workbooks(
            session.files,
            session.output_path,
            detail_overrides=overrides,
        )
    except Exception as exc:
        session.error = str(exc)
        return templates.TemplateResponse(
            request=request,
            name='consolidate.html',
            context=consolidation_page_context(
                request,
                files=session.files,
                session_token=token,
                manual_required=True,
                error=f'Consolidation failed: {exc}',
            ),
            status_code=400,
        )

    with _consolidation_lock:
        session.result = result
    return templates.TemplateResponse(
        request=request,
        name='consolidate.html',
        context=consolidation_page_context(
            request,
            files=result.files,
            result=result,
            session_token=token,
        ),
    )


@app.post('/analyze', response_class=HTMLResponse)
async def analyze(
    request: Request,
    statement: UploadFile = File(...),
    pdf_password: str = Form(""),
) -> HTMLResponse:
    filename = statement.filename or 'statement.pdf'
    if not filename.lower().endswith('.pdf'):
        return templates.TemplateResponse(
            request=request,
            name='analyze.html',
            context=page_context(
                request,
                filename=filename,
                error='Please upload a PDF bank statement.',
            ),
            status_code=400,
        )

    job_id = uuid.uuid4().hex
    pdf_path = UPLOAD_DIR / f'{job_id}.pdf'
    output_path = analysis_output_path(job_id, filename)

    with pdf_path.open('wb') as buffer:
        shutil.copyfileobj(statement.file, buffer)

    with _jobs_lock:
        _jobs[job_id] = JobState(
            filename=filename,
            pdf_path=pdf_path,
            output_path=output_path,
            pdf_password=pdf_password.strip() or None,
            status='queued',
        )
    start_analysis_job(job_id)
    return templates.TemplateResponse(
        request=request,
        name='analyze.html',
        context=page_context(
            request,
            active_job_id=job_id,
            active_job_status='queued',
            active_job_kind='analysis',
            filename=filename,
        ),
    )


@app.post('/train-analyzer', response_class=HTMLResponse)
async def train_analyzer(
    request: Request,
    statement: UploadFile = File(...),
    training_bank_name: str = Form(""),
    pdf_password: str = Form(""),
) -> HTMLResponse:
    filename = statement.filename or 'statement.pdf'
    bank_name = training_bank_name.strip()
    if not bank_name:
        return templates.TemplateResponse(
            request=request,
            name='analyze.html',
            context=page_context(
                request,
                filename=filename,
                error='Enter the bank or layout name before training the analyzer.',
                training_bank_name=bank_name,
                training_mode=True,
            ),
            status_code=400,
        )
    if not filename.lower().endswith('.pdf'):
        return templates.TemplateResponse(
            request=request,
            name='analyze.html',
            context=page_context(
                request,
                filename=filename,
                error='Please upload a PDF bank statement for training.',
                training_bank_name=bank_name,
                training_mode=True,
            ),
            status_code=400,
        )

    job_id = uuid.uuid4().hex
    pdf_path = UPLOAD_DIR / f'{job_id}.pdf'
    output_path = analysis_output_path(job_id, filename)

    with pdf_path.open('wb') as buffer:
        shutil.copyfileobj(statement.file, buffer)

    with _jobs_lock:
        _jobs[job_id] = JobState(
            filename=filename,
            pdf_path=pdf_path,
            output_path=output_path,
            pdf_password=pdf_password.strip() or None,
            training_bank_name=bank_name,
            status='queued',
        )
    start_analysis_job(job_id, training_bank_name=bank_name)
    return templates.TemplateResponse(
        request=request,
        name='analyze.html',
        context=page_context(
            request,
            active_job_id=job_id,
            active_job_status='queued',
            active_job_kind='training',
            filename=filename,
            training_bank_name=bank_name,
            training_mode=True,
        ),
    )


@app.post('/approve/{token}', response_class=HTMLResponse)
async def approve_review(token: str, request: Request) -> HTMLResponse:
    job = get_job(token)
    if not job or not job.pdf_path.exists():
        raise HTTPException(status_code=404, detail='Analysis session not found.')

    form = await request.form()
    manual_classifications: dict[int, str] = {}
    for key, value in form.items():
        if not key.startswith('category_'):
            continue
        category = str(value).strip()
        if not category:
            continue
        try:
            transaction_index = int(key.removeprefix('category_'))
        except ValueError:
            continue
        manual_classifications[transaction_index] = category
    remember_approvals = str(form.get('remember_approvals', '')).lower() in {'on', 'true', '1', 'yes'}

    new_job_id = uuid.uuid4().hex
    output_path = analysis_output_path(new_job_id, job.filename)
    with _jobs_lock:
        _jobs[new_job_id] = JobState(
            filename=job.filename,
            pdf_path=job.pdf_path,
            output_path=output_path,
            pdf_password=job.pdf_password,
            training_bank_name=job.training_bank_name,
            status='queued',
        )
    start_analysis_job(
        new_job_id,
        manual_classifications=manual_classifications,
        remember_approvals=remember_approvals,
        training_bank_name=job.training_bank_name,
    )
    return templates.TemplateResponse(
        request=request,
        name='analyze.html',
        context=page_context(
            request,
            active_job_id=new_job_id,
            active_job_status='queued',
            active_job_kind='approval',
            filename=job.filename,
        ),
    )


@app.post('/categories/{token}')
async def save_category(token: str, request: Request) -> RedirectResponse:
    job = get_job(token)
    if not job:
        raise HTTPException(status_code=404, detail='Analysis session not found.')

    form = await request.form()
    direction = str(form.get('category_direction', '')).strip().lower()
    category_name = str(form.get('category_name', '')).strip()
    save_custom_category_option(direction, category_name)
    if job.summary is not None:
        refresh_summary_review_options(job.summary)
    return RedirectResponse(url=f'/result/{token}#review-queue', status_code=303)


@app.post('/adaptive-approve/{token}', response_class=HTMLResponse)
async def approve_adaptive_review(token: str, request: Request) -> HTMLResponse:
    job = get_job(token)
    if not job or not job.pdf_path.exists():
        raise HTTPException(status_code=404, detail='Analysis session not found.')

    form = await request.form()
    adaptive_column_overrides: dict[int, str] = {}
    for key, value in form.items():
        if not key.startswith('column_role_'):
            continue
        try:
            column_index = int(key.removeprefix('column_role_'))
        except ValueError:
            continue
        adaptive_column_overrides[column_index] = str(value).strip().lower()
    training_bank_name = str(form.get('training_bank_name') or job.training_bank_name or '').strip() or None

    new_job_id = uuid.uuid4().hex
    output_path = analysis_output_path(new_job_id, job.filename)
    with _jobs_lock:
        _jobs[new_job_id] = JobState(
            filename=job.filename,
            pdf_path=job.pdf_path,
            output_path=output_path,
            pdf_password=job.pdf_password,
            training_bank_name=training_bank_name,
            status='queued',
        )
    start_analysis_job(
        new_job_id,
        allow_low_confidence_adaptive=True,
        adaptive_column_overrides=adaptive_column_overrides,
        training_bank_name=training_bank_name,
    )
    return templates.TemplateResponse(
        request=request,
        name='analyze.html',
        context=page_context(
            request,
            active_job_id=new_job_id,
            active_job_status='queued',
            active_job_kind='adaptive-approval',
            filename=job.filename,
            training_bank_name=training_bank_name,
            training_mode=bool(training_bank_name),
        ),
    )


@app.get('/download/{token}')
async def download(token: str) -> FileResponse:
    job = get_job(token)
    output_path = None
    filename = None
    if job and job.status == 'completed' and job.output_path.exists():
        output_path = job.output_path
        filename = download_filename(job.filename)
    else:
        output_path = find_analysis_output_by_token(token)
        filename = recovered_download_filename(output_path) if output_path else None

    if output_path is None or not output_path.exists():
        logger.warning(
            "Download missing for token %s: job_found=%s job_status=%s job_output=%s",
            token,
            bool(job),
            job.status if job else None,
            str(job.output_path) if job else None,
        )
        raise HTTPException(
            status_code=404,
            detail='The generated workbook is no longer available. Please run the analysis again and download it after completion.',
        )
    return FileResponse(
        output_path,
        filename=filename or output_path.name,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.get('/download-consolidated/{token}')
async def download_consolidated(token: str) -> FileResponse:
    session = get_consolidation_session(token)
    if not session or not session.result or not session.output_path.exists():
        raise HTTPException(status_code=404, detail='File not found.')
    return FileResponse(
        session.output_path,
        filename=session.output_path.name,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@app.get('/result/{token}', response_class=HTMLResponse)
async def result(token: str, request: Request) -> HTMLResponse:
    job = get_job(token)
    if not job:
        raise HTTPException(status_code=404, detail='Analysis session not found.')
    if job.status == 'completed' and job.summary is not None:
        refresh_summary_review_options(job.summary)
        return templates.TemplateResponse(
            request=request,
            name='analyze.html',
            context=page_context(
                request,
                summary=job.summary,
                download_token=token,
                filename=job.filename,
                training_bank_name=job.training_bank_name,
                training_mode=bool(job.training_bank_name),
            ),
        )
    if job.status == 'failed':
        return templates.TemplateResponse(
            request=request,
            name='analyze.html',
            context=page_context(
                request,
                filename=job.filename,
                error=job.error or 'Analysis failed.',
                training_bank_name=job.training_bank_name,
                training_mode=bool(job.training_bank_name),
            ),
            status_code=400,
        )
    if job.status == 'review_required' and job.adaptive_review is not None:
        return templates.TemplateResponse(
            request=request,
            name='analyze.html',
            context=page_context(
                request,
                filename=job.filename,
                adaptive_review=job.adaptive_review,
                adaptive_review_token=token,
                training_bank_name=job.training_bank_name,
                training_mode=bool(job.training_bank_name),
            ),
            status_code=200,
        )
    return templates.TemplateResponse(
        request=request,
        name='analyze.html',
        context=page_context(
            request,
            active_job_id=token,
            active_job_status=job.status,
            active_job_kind='training' if job.training_bank_name else 'analysis',
            filename=job.filename,
            training_bank_name=job.training_bank_name,
            training_mode=bool(job.training_bank_name),
        ),
    )


@app.get('/status/{token}')
async def status(token: str) -> JSONResponse:
    job = get_job(token)
    if not job:
        raise HTTPException(status_code=404, detail='Analysis session not found.')
    return JSONResponse(
        {
            'status': job.status,
            'error': job.error,
            'result_url': f'/result/{token}',
            'download_url': f'/download/{token}' if job.status == 'completed' else None,
        }
    )


@app.get('/health')
async def health() -> dict[str, str]:
    return {'status': 'ok'}


def start_analysis_job(
    job_id: str,
    *,
    manual_classifications: dict[int, str] | None = None,
    remember_approvals: bool = False,
    allow_low_confidence_adaptive: bool = False,
    adaptive_column_overrides: dict[int, str] | None = None,
    training_bank_name: str | None = None,
) -> None:
    _analysis_executor.submit(
        run_analysis_job,
        job_id,
        manual_classifications=manual_classifications,
        remember_approvals=remember_approvals,
        allow_low_confidence_adaptive=allow_low_confidence_adaptive,
        adaptive_column_overrides=adaptive_column_overrides,
        training_bank_name=training_bank_name,
    )


def run_analysis_job(
    job_id: str,
    *,
    manual_classifications: dict[int, str] | None = None,
    remember_approvals: bool = False,
    allow_low_confidence_adaptive: bool = False,
    adaptive_column_overrides: dict[int, str] | None = None,
    training_bank_name: str | None = None,
) -> None:
    job = get_job(job_id)
    if not job:
        return

    started_at = time.perf_counter()
    with _jobs_lock:
        job.status = 'running'
        job.error = None
        job.adaptive_review = None
    logger.info("Analysis job %s started for %s", job_id, job.filename)

    try:
        result = service.analyze(
            job.pdf_path,
            job.output_path,
            manual_classifications=manual_classifications,
            remember_approvals=remember_approvals,
            allow_low_confidence_adaptive=allow_low_confidence_adaptive,
            adaptive_column_overrides=adaptive_column_overrides,
            pdf_password=job.pdf_password,
            training_bank_name=training_bank_name or job.training_bank_name,
        )
    except AdaptiveReviewRequired as exc:
        elapsed = time.perf_counter() - started_at
        with _jobs_lock:
            job.status = 'review_required'
            job.adaptive_review = service.build_adaptive_review_summary(
                job.pdf_path,
                exc,
                pdf_password=job.pdf_password,
            )
            job.error = None
        logger.info("Analysis job %s paused for adaptive review after %.2fs", job_id, elapsed)
        return
    except Exception as exc:
        elapsed = time.perf_counter() - started_at
        logger.error("Analysis job %s failed for %s after %.2fs", job_id, job.pdf_path, elapsed, exc_info=True)
        message = friendly_job_error(exc)
        if 'No parser matched statement' in message:
            message = 'This bank statement format is not supported yet in the current version, and no known layout signature matched it yet.'
        with _jobs_lock:
            job.status = 'failed'
            job.error = message
            job.adaptive_review = None
        return

    with _jobs_lock:
        job.output_path = result.excel_path
        job.summary = result.summary
        job.status = 'completed'
        job.error = None
        job.adaptive_review = None
    elapsed = time.perf_counter() - started_at
    logger.info("Analysis job %s completed in %.2fs", job_id, elapsed)


def get_job(job_id: str) -> JobState | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def get_consolidation_session(token: str) -> ConsolidationSession | None:
    with _consolidation_lock:
        return _consolidation_sessions.get(token)


def friendly_job_error(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message

    if is_password_error(exc):
        return 'This PDF is password-protected. Enter the PDF password and upload it again.'

    exception_name = type(exc).__name__
    if exception_name == 'AssertionError':
        return (
            'The statement could not be analyzed because the PDF text extraction returned an unexpected structure. '
            'This usually happens with scanned statements, protected PDFs, or a new layout family that still needs review.'
        )
    if exception_name in {'PDFPasswordIncorrect', 'PasswordProtected'}:
        return 'This PDF is password-protected. Please remove the password and upload it again.'
    if exception_name in {'PDFSyntaxError', 'PSEOF', 'PDFEncryptionError'}:
        return 'This PDF could not be read cleanly. Please export a fresh text-based statement PDF and try again.'

    return (
        f'{exception_name}: The statement could not be analyzed automatically. '
        'Please try again, or upload a text-based PDF export if this file is scanned or image-only.'
    )


def analysis_output_path(job_id: str, filename: str) -> Path:
    stem = Path(filename or "statement").stem or "statement"
    return OUTPUT_DIR / f'{stem}_{job_id}_ANALYZED.xlsx'


def download_filename(filename: str) -> str:
    stem = Path(filename or "statement").stem or "statement"
    return f"{stem}_ANALYZED.xlsx"


def find_analysis_output_by_token(token: str) -> Path | None:
    if not token or any(character in token for character in "/\\"):
        return None
    matches = sorted(OUTPUT_DIR.glob(f"*_{token}_ANALYZED.xlsx"))
    return matches[0] if matches else None


def recovered_download_filename(output_path: Path) -> str:
    suffix = "_ANALYZED"
    stem = output_path.stem
    parts = stem.rsplit("_", 2)
    if len(parts) == 3 and parts[2] == "ANALYZED":
        return f"{parts[0]}{suffix}.xlsx"
    return output_path.name
