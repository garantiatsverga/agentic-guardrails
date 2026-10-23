"""FastAPI-приложение Document Agent (спринт 1)."""
from __future__ import annotations

from pathlib import Path

import colordebug as cd
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from document_agent.models import ApplicationResult
from document_agent.pipeline import get_audit_chain, run_pipeline


MAX_FILES = 3
MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".txt"}

app = FastAPI(title="Document Agent — Sprint 1")
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

RESULTS: dict[str, ApplicationResult] = {}
ESCALATIONS: dict[str, str] = {}


def _validate_upload(files: list[UploadFile]) -> str | None:
    if not files or all(not f.filename for f in files):
        return "Загрузите хотя бы один файл"
    real = [f for f in files if f.filename]
    if len(real) > MAX_FILES:
        return f"Не более {MAX_FILES} файлов на заявку"
    for f in real:
        ext = Path(f.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return f"Недопустимый формат: {f.filename}. Загрузите PDF или TXT"
    return None


def _render_upload(request: Request, error: str | None = None, status: int = 200):
    return templates.TemplateResponse(
        request, "upload.html",
        {"max_files": MAX_FILES,
         "max_mb": MAX_FILE_BYTES // (1024 * 1024),
         "error": error},
        status_code=status,
    )


def _get_extraction_meta(app_id: str) -> dict:
    """sources, roles, conflicts из последней extraction.done в аудите."""
    chain = get_audit_chain()
    for e in reversed(chain.entries):
        if e.event == "extraction.done" and e.payload.get("app_id") == app_id:
            p = e.payload
            return {
                "sources": p.get("sources", {}),
                "roles": p.get("roles", {}),
                "conflicts": p.get("conflicts", []),
            }
    return {"sources": {}, "roles": {}, "conflicts": []}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return _render_upload(request)


@app.post("/upload", response_class=HTMLResponse)
async def upload(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    business_description: str = Form(default=""),
):
    real_files = [f for f in files if f.filename]
    cd.info(
        f"[WEB] POST /upload — файлов: {len(real_files)}, "
        f"описание: {business_description[:40]!r}"
    )

    err = _validate_upload(files)
    if err:
        cd.warning(f"[WEB] Отклонено на валидации: {err}")
        return _render_upload(request, error=err, status=400)

    for f in real_files:
        content = await f.read()
        if len(content) > MAX_FILE_BYTES:
            msg = (
                f"Файл слишком большой: {f.filename} "
                f"({len(content) // (1024 * 1024)} МБ, максимум "
                f"{MAX_FILE_BYTES // (1024 * 1024)} МБ)"
            )
            cd.warning(f"[WEB] Отклонено на валидации: {msg}")
            return _render_upload(request, error=msg, status=400)
        await f.seek(0)
        cd.debug(f"[WEB]   файл: {f.filename} ({len(content)} байт)")

    cd.info("[WEB] Запуск pipeline...")
    with cd.timer("[WEB] pipeline"):
        result = await run_pipeline(
            files=real_files,
            business_description=business_description,
        )

    RESULTS[result.application_id] = result
    _log_pipeline_result(result)

    return RedirectResponse(url=f"/result/{result.application_id}", status_code=303)


def _log_pipeline_result(result: ApplicationResult) -> None:
    app_short = result.application_id[:8]
    status = result.extraction_status

    if status == "success":
        cd.success(f"[WEB] Результат {app_short}: success | review={result.requires_manual_review}")
    elif status == "partial":
        cd.warning(f"[WEB] Результат {app_short}: partial | missing={result.missing_data}")
    else:
        cd.error(f"[WEB] Результат {app_short}: failed | {result.escalation_reason}")

    meta = _get_extraction_meta(result.application_id)
    if meta["roles"]:
        cd.debug(f"[WEB]   роли: {meta['roles']}")
    if meta["sources"]:
        cd.debug(f"[WEB]   источники: {meta['sources']}")
    if meta["conflicts"]:
        cd.warning(f"[WEB]   КОНФЛИКТЫ ({len(meta['conflicts'])}):")
        for c in meta["conflicts"]:
            cd.warning(
                f"[WEB]     {c['field']}: "
                f"{c['values']} -> взято {c['chosen']} из {c['chosen_source']}"
            )

    for v in result.validation_results:
        line = f"[WEB]   {v.field}: {v.status}"
        if v.message:
            line += f" — {v.message}"
        if v.status == "passed":
            cd.debug(line)
        elif v.status == "warning":
            cd.warning(line)
        else:
            cd.error(line)

    if result.escalation_reason:
        cd.info(f"[WEB]   причина эскалации: {result.escalation_reason}")


@app.get("/result/{app_id}", response_class=HTMLResponse)
async def result_page(request: Request, app_id: str):
    result = RESULTS.get(app_id)
    if result is None:
        cd.warning(f"[WEB] GET /result/{app_id[:8]} — не найдена")
        return _render_upload(request, error=f"Заявка {app_id} не найдена", status=404)
    cd.debug(f"[WEB] GET /result/{app_id[:8]} — отдаю HTML")
    meta = _get_extraction_meta(app_id)
    return templates.TemplateResponse(
        request, "result.html",
        {"r": result,
         "escalated": app_id in ESCALATIONS,
         "escalation_reason": ESCALATIONS.get(app_id),
         "external": _external_preview(result),
         "sources": meta["sources"],
         "roles": meta["roles"],
         "conflicts": meta["conflicts"]},
    )


@app.get("/result/{app_id}.json")
async def result_json(app_id: str):
    result = RESULTS.get(app_id)
    if result is None:
        cd.warning(f"[WEB] GET /result/{app_id[:8]}.json — не найдена")
        return JSONResponse({"error": "not found", "application_id": app_id}, status_code=404)
    cd.info(f"[WEB] GET /result/{app_id[:8]}.json — JSON §6.1, status={result.extraction_status}")
    return JSONResponse(result.model_dump())


@app.post("/result/{app_id}/escalate")
async def escalate(app_id: str, reason: str = Form(default="Ручная проверка по запросу менеджера")):
    if app_id not in RESULTS:
        cd.warning(f"[WEB] POST escalate {app_id[:8]} — не найдена")
        return JSONResponse({"error": "not found"}, status_code=404)
    ESCALATIONS[app_id] = reason
    get_audit_chain().append("escalation.requested", {"app_id": app_id, "reason": reason})
    cd.warning(f"[WEB] Эскалация {app_id[:8]}: {reason} (audit_entries={len(get_audit_chain())})")
    return RedirectResponse(url=f"/result/{app_id}", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    chain = get_audit_chain()
    entries = [
        {"seq": e.seq, "event": e.event,
         "timestamp": e.timestamp,
         "entry_hash_short": e.entry_hash[:16] + "..."}
        for e in chain.entries
    ]
    ok = chain.verify()
    if ok:
        cd.success(f"[WEB] GET /audit — verify=True, записей: {len(chain)}")
    else:
        cd.critical("[WEB] GET /audit — verify=False, журнал повреждён!")
    return templates.TemplateResponse(
        request, "audit.html",
        {"entries": entries, "verify_ok": ok, "total": len(chain)},
    )


@app.get("/health")
async def health():
    cd.debug(f"[WEB] GET /health — applications={len(RESULTS)}")
    return {"status": "ok", "applications": len(RESULTS)}


def _external_preview(result: ApplicationResult) -> dict | None:
    if result.extraction_status == "failed":
        return None
    from document_agent.mocks import collect_external
    return collect_external(result.extracted_data.inn)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    import uvicorn
    cd.info(f"[WEB] Старт: http://{host}:{port}")
    cd.info("[WEB]   GET  /        — форма загрузки")
    cd.info("[WEB]   GET  /audit   — журнал аудита (hash-chain)")
    cd.info("[WEB]   GET  /health  — healthcheck")
    uvicorn.run(app, host=host, port=port, log_level="info")
