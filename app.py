from __future__ import annotations

import copy
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from schemas import CopilotRequest
from ai_analyzer import analyze_runtime_with_ai, analyze_with_ai, generate_embedding, chat_copilot
from database import (
    build_similar_cases_context,
    database_stats,
    delete_case,
    find_similar_cases,
    get_case,
    initialize_database,
    list_cases,
    save_case,
    delete_runtime_case,
    get_runtime_case,
    list_runtime_cases,
    runtime_database_stats,
    save_runtime_case,
)
from deterministic import pre_scan
from log_engine import combine_for_prompt, prepare_file
from runtime_analyzer import analyze_runtime

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.getenv("PHONEBOT_DB_PATH", "").strip() or str(DATA_DIR / "phonebot_cases.db"))

MAX_TOTAL_UPLOAD_MB = int(os.getenv("MAX_TOTAL_UPLOAD_MB", "25"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "8"))
SERVER_MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "220000"))
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
SIMILAR_CASE_MIN_SCORE = float(os.getenv("SIMILAR_CASE_MIN_SCORE", "0.58"))
REUSE_CASE_MIN_SCORE = float(os.getenv("REUSE_CASE_MIN_SCORE", "0.94"))
ALLOWED_EXTENSIONS = {".log", ".txt", ".json", ".csv", ".xml", ".plist"}
SUPPORTED_PROVIDERS = {"openai", "gemini", "ollama"}
SERVER_ERROR_LOG = BASE_DIR / "server_error.log"


def _redact_exception_text(text: str) -> str:
    """Remove likely API keys/tokens before writing or returning errors."""
    patterns = [
        re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
        re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
        re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}\b", re.I),
    ]
    output = str(text)
    for pattern in patterns:
        output = pattern.sub("[REDACTED_API_KEY]", output)
    return output


def _write_server_error(request_path: str, exc: Exception) -> str:
    error_id = datetime.now(timezone.utc).strftime("ERR-%Y%m%d-%H%M%S-%f")
    raw_trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    safe_trace = _redact_exception_text(raw_trace)
    with SERVER_ERROR_LOG.open("a", encoding="utf-8") as handle:
        handle.write("\n" + "=" * 90 + "\n")
        handle.write(f"{error_id} | path={request_path}\n")
        handle.write(safe_trace)
    return error_id


PORT_PATH_PATTERNS = [
    re.compile(r"(?:^|[/\\])port[ _-]*0*([1-9]\d?)(?=$|[/\\])", re.I),
    re.compile(r"(?:^|[/\\])(?:slot|channel|ch)[ _-]*0*([1-9]\d?)(?=$|[/\\])", re.I),
    re.compile(r"(?:^|[/\\])p[ _-]*0*([1-9]\d?)(?=$|[/\\])", re.I),
]
PORT_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:port|slot|channel|ch)\s*[#:=_-]?\s*0*([1-9]\d?)(?!\d)",
    re.I,
)


def _normalize_port_key(number: str | int) -> str:
    return f"port{int(number)}"


def _port_label(port_key: str) -> str:
    match = re.fullmatch(r"port(\d+)", str(port_key).strip().lower())
    return f"Port {int(match.group(1))}" if match else str(port_key)


def _detect_port_from_filename(filename: str) -> str | None:
    normalized = str(filename or "").replace("\\", "/")
    # Folder path is the strongest signal. The basename is intentionally not
    # used for generic names such as trace.log/logfile1.txt.
    for pattern in PORT_PATH_PATTERNS:
        match = pattern.search(normalized)
        if match:
            return _normalize_port_key(match.group(1))
    # Also accept explicit basename prefixes such as port1_trace.log.
    basename = Path(normalized).name
    match = re.search(r"(?:^|[_ .-])port[ _-]*0*([1-9]\d?)(?=$|[_ .-])", basename, re.I)
    return _normalize_port_key(match.group(1)) if match else None


def _detect_unique_port_from_text(text: str) -> str | None:
    ports = {_normalize_port_key(match.group(1)) for match in PORT_TEXT_RE.finditer(text[:250_000])}
    return next(iter(ports)) if len(ports) == 1 else None


class NoCacheStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

initialize_database(DB_PATH)

app = FastAPI(title="PhoneBot AI Assistant", version="0.9.5")
app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc: Exception):
    """Always return JSON so the dashboard never tries to parse plain text as JSON."""
    error_id = _write_server_error(request.url.path, exc)
    safe_message = _redact_exception_text(str(exc)).strip()
    detail = f"{type(exc).__name__}: {safe_message}" if safe_message else type(exc).__name__
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"Lỗi nội bộ tại {request.url.path}: {detail}",
            "error_id": error_id,
            "log_file": "server_error.log",
        },
    )


@app.middleware("http")
async def disable_browser_cache(request, call_next):
    response = await call_next(request)
    if request.url.path in ("/", "/uph", "/PB_UPH_v6.html") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.get("/")
def index() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/uph")
@app.get("/PB_UPH_v6.html")
def uph_tool() -> FileResponse:
    return FileResponse(
        BASE_DIR / "PB_UPH_v6.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": "0.9.5",
        "providers": sorted(SUPPORTED_PROVIDERS),
        "default_models": {
            "openai": DEFAULT_OPENAI_MODEL,
            "gemini": DEFAULT_GEMINI_MODEL,
            "ollama": "qwen2.5-coder:1.5b",
        },
        "database": database_stats(DB_PATH),
    }


@app.get("/api/database/stats")
def get_database_stats() -> dict:
    return database_stats(DB_PATH)


@app.get("/api/cases")
def get_cases(
    limit: int = Query(30, ge=1, le=200),
    query: str = Query("", max_length=250),
) -> dict:
    return {
        "cases": list_cases(DB_PATH, limit=limit, query=query),
        "stats": database_stats(DB_PATH),
    }


@app.get("/api/cases/{case_id}")
def read_case(case_id: int) -> dict:
    case = get_case(DB_PATH, case_id)
    if case is None:
        raise HTTPException(404, "Case not found.")
    return case


@app.delete("/api/cases/{case_id}")
def remove_case(case_id: int) -> dict:
    if not delete_case(DB_PATH, case_id):
        raise HTTPException(404, "Case not found.")
    return {"deleted": True, "case_id": case_id}


@app.get("/api/runtime-cases")
def get_runtime_cases(
    limit: int = Query(30, ge=1, le=200),
    query: str = Query("", max_length=250),
) -> dict:
    return {
        "cases": list_runtime_cases(DB_PATH, limit=limit, query=query),
        "stats": runtime_database_stats(DB_PATH),
    }


@app.get("/api/runtime-cases/{case_id}")
def read_runtime_case(case_id: int) -> dict:
    case = get_runtime_case(DB_PATH, case_id)
    if case is None:
        raise HTTPException(404, "Runtime case not found.")
    return case


@app.delete("/api/runtime-cases/{case_id}")
def remove_runtime_case(case_id: int) -> dict:
    if not delete_runtime_case(DB_PATH, case_id):
        raise HTTPException(404, "Runtime case not found.")
    return {"deleted": True, "case_id": case_id}


@app.post("/api/analyze")
async def analyze(
    reported_error: str = Form(...),
    provider: str = Form("openai"),
    api_key: str = Form(""),
    base_url: str = Form(""),
    model: str = Form(""),
    redact_sensitive_data: bool = Form(True),
    prompt_char_limit: int = Form(90000),
    use_case_database: bool = Form(True),
    reuse_strong_match: bool = Form(True),
    files: list[UploadFile] = File(...),
) -> dict:
    reported_error = reported_error.strip()
    provider = provider.strip().lower()
    base_url = base_url.strip()

    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(400, f"Unsupported AI provider: {provider}")
    if not reported_error:
        raise HTTPException(400, "Reported error is required.")
    if provider == "gemini" and base_url:
        raise HTTPException(400, "Base URL is currently supported only for OpenAI-compatible endpoints.")
    if not (10000 <= prompt_char_limit <= SERVER_MAX_PROMPT_CHARS):
        raise HTTPException(400, f"prompt_char_limit must be 10000-{SERVER_MAX_PROMPT_CHARS}.")

    if provider == "openai":
        env_key_name = "OPENAI_API_KEY"
        env_model_name = "OPENAI_MODEL"
        fallback_model = DEFAULT_OPENAI_MODEL
    elif provider == "gemini":
        env_key_name = "GEMINI_API_KEY"
        env_model_name = "GEMINI_MODEL"
        fallback_model = DEFAULT_GEMINI_MODEL
    else:  # ollama
        env_key_name = "OLLAMA_API_KEY"
        env_model_name = "OLLAMA_MODEL"
        fallback_model = "qwen2.5-coder:1.5b"

    request_key = api_key.strip()
    env_key = os.getenv(env_key_name, "").strip()
    resolved_key = request_key or env_key
    if provider == "ollama" and not resolved_key:
        resolved_key = "ollama"
    resolved_model = model.strip() or os.getenv(env_model_name, fallback_model).strip()

    # Mismatch correction logic
    if provider == "openai" and not base_url and "gemini" in resolved_model.lower():
        provider = "gemini"
        env_key_name = "GEMINI_API_KEY"
        resolved_key = request_key or os.getenv(env_key_name, "").strip()
    elif provider == "gemini" and "gpt" in resolved_model.lower():
        provider = "openai"
        env_key_name = "OPENAI_API_KEY"
        resolved_key = request_key or os.getenv(env_key_name, "").strip()
    elif provider == "ollama" and ("gemini" in resolved_model.lower() or "gpt" in resolved_model.lower()):
        resolved_model = "qwen2.5-coder:1.5b"

    total_bytes = 0
    parsed_files = []
    skipped_files: list[str] = []

    for uploaded in files:
        filename = uploaded.filename or "unnamed.log"
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            skipped_files.append(filename)
            continue

        data = await uploaded.read()
        total_bytes += len(data)
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"{filename} exceeds {MAX_FILE_MB} MB.")
        if total_bytes > MAX_TOTAL_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"Total upload exceeds {MAX_TOTAL_UPLOAD_MB} MB.")

        parsed_files.append(
            prepare_file(filename, data, reported_error, redact_sensitive_data)
        )

    if not parsed_files:
        raise HTTPException(400, "No supported log files were uploaded.")

    prompt_logs = combine_for_prompt(parsed_files, prompt_char_limit)
    scan_summary = pre_scan(parsed_files)
    file_names = [p.name for p in parsed_files]

    similar_cases = []
    query_embedding = None
    if use_case_database:
        if resolved_key:
            try:
                query_embedding = generate_embedding(
                    reported_error,
                    provider=provider,
                    api_key=resolved_key,
                    base_url=base_url or None,
                )
            except Exception as e:
                print(f"Failed to generate query embedding: {e}")

        similar_cases = find_similar_cases(
            DB_PATH,
            reported_error=reported_error,
            deterministic_summary=scan_summary,
            query_embedding=query_embedding,
            limit=3,
            minimum_score=SIMILAR_CASE_MIN_SCORE,
        )

    top_match = similar_cases[0] if similar_cases else None
    reused_case_id: int | None = None

    if (
        use_case_database
        and reuse_strong_match
        and top_match is not None
        and top_match.score >= REUSE_CASE_MIN_SCORE
    ):
        analysis_dict = copy.deepcopy(top_match.analysis)
        analysis_dict["reported_error"] = reported_error
        source_mode = "database_cache"
        reused_case_id = top_match.case_id
        response_provider = "database"
        response_model = f"cached case #{top_match.case_id}"
        api_key_source = "not_used"
    else:
        if not resolved_key:
            match_note = ""
            if top_match is not None:
                match_note = f" Best database match was only {top_match.score * 100:.1f}%."
            raise HTTPException(
                400,
                f"No strong reusable database case was found.{match_note} "
                f"Enter an API key or configure {env_key_name}.",
            )

        history_context = build_similar_cases_context(similar_cases)
        try:
            result = analyze_with_ai(
                provider=provider,
                api_key=resolved_key,
                base_url=base_url or None,
                reported_error=reported_error,
                prepared_logs=prompt_logs,
                deterministic_summary=scan_summary,
                model=resolved_model,
                similar_cases_context=history_context,
            )
        except Exception as exc:
            safe_message = str(exc).replace(resolved_key, "[REDACTED_API_KEY]") if resolved_key != "ollama" else str(exc)
            raise HTTPException(502, f"AI analysis failed: {safe_message}") from exc

        analysis_dict = result.model_dump()
        source_mode = "ai_with_history" if similar_cases else "ai_new"
        response_provider = provider
        response_model = resolved_model
        api_key_source = "request" if request_key else env_key_name

    public_matches = [case.public_summary() for case in similar_cases]
    meta = {
        "version": "0.9.5",
        "provider": response_provider,
        "model": response_model,
        "requested_provider": provider,
        "requested_model": resolved_model,
        "base_url": base_url or None,
        "api_key_source": api_key_source,
        "api_key_stored": False,
        "redacted": redact_sensitive_data,
        "prompt_char_limit": prompt_char_limit,
        "selected_prompt_chars": len(prompt_logs),
        "skipped_files": skipped_files,
        "files": [
            {
                "name": p.name,
                "total_lines": p.total_lines,
                "selected_lines": p.selected_lines,
                "selected_text": p.selected_text
            }
            for p in parsed_files
        ],
        "deterministic_pre_scan": scan_summary,
        "database": {
            "enabled": use_case_database,
            "source_mode": source_mode,
            "similar_case_min_score": SIMILAR_CASE_MIN_SCORE,
            "reuse_case_min_score": REUSE_CASE_MIN_SCORE,
            "reused_from_case_id": reused_case_id,
            "similar_cases": public_matches,
            "raw_logs_stored": False,
            "selected_evidence_stored": True,
        },
    }

    if not query_embedding and resolved_key:
        try:
            query_embedding = generate_embedding(
                reported_error,
                provider=provider,
                api_key=resolved_key,
                base_url=base_url or None,
            )
        except Exception:
            pass

    saved_provider = top_match.provider if source_mode == "database_cache" and top_match else provider
    saved_model = top_match.model if source_mode == "database_cache" and top_match else resolved_model
    case_id = save_case(
        DB_PATH,
        reported_error=reported_error,
        deterministic_summary=scan_summary,
        evidence_excerpt=prompt_logs[:60000],
        provider=saved_provider,
        model=saved_model,
        analysis=analysis_dict,
        meta=meta,
        file_names=file_names,
        redacted=redact_sensitive_data,
        source_mode=source_mode,
        reused_from_case_id=reused_case_id,
        embedding=query_embedding,
    )
    meta["database"]["saved_case_id"] = case_id
    meta["database"]["stats"] = database_stats(DB_PATH)

    return {"analysis": analysis_dict, "meta": meta}


@app.post("/api/analyze-runtime")
async def analyze_runtime_endpoint(
    process_label: str = Form(""),
    selected_port: str = Form(""),
    slow_threshold_minutes: float = Form(13.0),
    gap_threshold_seconds: float = Form(30.0),
    use_ai_explanation: bool = Form(False),
    provider: str = Form("openai"),
    api_key: str = Form(""),
    base_url: str = Form(""),
    model: str = Form(""),
    redact_sensitive_data: bool = Form(True),
    prompt_char_limit: int = Form(90000),
    files: list[UploadFile] = File(...),
) -> dict:
    provider = provider.strip().lower()
    base_url = base_url.strip()
    process_label = process_label.strip()

    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(400, f"Unsupported AI provider: {provider}")
    if provider == "gemini" and base_url:
        raise HTTPException(400, "Base URL is currently supported only for OpenAI-compatible endpoints.")
    if not (1 <= slow_threshold_minutes <= 240):
        raise HTTPException(400, "slow_threshold_minutes must be 1-240.")
    if not (5 <= gap_threshold_seconds <= 3600):
        raise HTTPException(400, "gap_threshold_seconds must be 5-3600.")
    if not (10000 <= prompt_char_limit <= SERVER_MAX_PROMPT_CHARS):
        raise HTTPException(400, f"prompt_char_limit must be 10000-{SERVER_MAX_PROMPT_CHARS}.")

    if provider == "openai":
        env_key_name = "OPENAI_API_KEY"
        env_model_name = "OPENAI_MODEL"
        fallback_model = DEFAULT_OPENAI_MODEL
    elif provider == "gemini":
        env_key_name = "GEMINI_API_KEY"
        env_model_name = "GEMINI_MODEL"
        fallback_model = DEFAULT_GEMINI_MODEL
    else:  # ollama
        env_key_name = "OLLAMA_API_KEY"
        env_model_name = "OLLAMA_MODEL"
        fallback_model = "qwen2.5-coder:1.5b"

    request_key = api_key.strip()
    resolved_key = request_key or os.getenv(env_key_name, "").strip()
    if provider == "ollama" and not resolved_key:
        resolved_key = "ollama"
    resolved_model = model.strip() or os.getenv(env_model_name, fallback_model).strip()

    # Mismatch correction logic
    if provider == "openai" and not base_url and "gemini" in resolved_model.lower():
        provider = "gemini"
        env_key_name = "GEMINI_API_KEY"
        resolved_key = request_key or os.getenv(env_key_name, "").strip()
    elif provider == "gemini" and "gpt" in resolved_model.lower():
        provider = "openai"
        env_key_name = "OPENAI_API_KEY"
        resolved_key = request_key or os.getenv(env_key_name, "").strip()
    elif provider == "ollama" and ("gemini" in resolved_model.lower() or "gpt" in resolved_model.lower()):
        resolved_model = "qwen2.5-coder:1.5b"

    total_bytes = 0
    upload_records: list[dict] = []
    skipped_files: list[str] = []
    runtime_claim = "runtime elapsed duration timeout retry wait usb trust vision ocr robot z axis device service"

    for uploaded in files:
        filename = uploaded.filename or "unnamed.log"
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            skipped_files.append(filename)
            continue
        data = await uploaded.read()
        total_bytes += len(data)
        if len(data) > MAX_FILE_MB * 1024 * 1024:
            raise HTTPException(413, f"{filename} exceeds {MAX_FILE_MB} MB.")
        if total_bytes > MAX_TOTAL_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(413, f"Total upload exceeds {MAX_TOTAL_UPLOAD_MB} MB.")

        port_key = _detect_port_from_filename(filename)
        if port_key is None:
            # Text detection is only a fallback. If a shared machine log mentions
            # several ports, it intentionally remains unassigned rather than being
            # mixed into an arbitrary port analysis.
            sample = data.decode("utf-8", errors="ignore")
            port_key = _detect_unique_port_from_text(sample)
        upload_records.append({"filename": filename, "data": data, "port_key": port_key})

    if not upload_records:
        raise HTTPException(400, "No supported log files were uploaded.")

    detected_ports = sorted(
        {record["port_key"] for record in upload_records if record["port_key"]},
        key=lambda value: int(value.replace("port", "")),
    )
    requested_port = selected_port.strip().lower()
    if requested_port:
        match = re.fullmatch(r"(?:port)?\s*0*([1-9]\d?)", requested_port, re.I)
        if not match:
            raise HTTPException(400, f"Port không hợp lệ: {selected_port}")
        requested_port = _normalize_port_key(match.group(1))
        if detected_ports and requested_port not in detected_ports:
            labels = ", ".join(_port_label(item) for item in detected_ports)
            raise HTTPException(400, f"Không tìm thấy {_port_label(requested_port)} trong folder. Port phát hiện được: {labels}.")
    elif len(detected_ports) > 1:
        labels = ", ".join(_port_label(item) for item in detected_ports)
        raise HTTPException(
            400,
            f"Folder có nhiều port ({labels}). Hãy chọn đúng port trước khi phân tích để tránh trộn thời gian giữa các port.",
        )
    elif len(detected_ports) == 1:
        requested_port = detected_ports[0]

    if requested_port:
        selected_records = [record for record in upload_records if record["port_key"] == requested_port]
        excluded_unassigned = [record["filename"] for record in upload_records if not record["port_key"]]
        excluded_other_ports = [
            record["filename"] for record in upload_records
            if record["port_key"] and record["port_key"] != requested_port
        ]
    else:
        selected_records = upload_records
        excluded_unassigned = []
        excluded_other_ports = []

    if not selected_records:
        raise HTTPException(400, "Không có file log thuộc port đã chọn.")

    parsed_files = [
        prepare_file(record["filename"], record["data"], runtime_claim, redact_sensitive_data)
        for record in selected_records
    ]

    try:
        effective_process_label = process_label.strip()
        if requested_port:
            port_name = _port_label(requested_port)
            if port_name.lower() not in effective_process_label.lower():
                effective_process_label = f"{effective_process_label} · {port_name}" if effective_process_label else port_name
        deterministic_result = analyze_runtime(
            parsed_files,
            slow_threshold_minutes=slow_threshold_minutes,
            gap_threshold_seconds=gap_threshold_seconds,
            process_label=effective_process_label,
            per_phone_mode=bool(requested_port),
        )
        analysis_dict = deterministic_result.model_dump()
    except Exception as exc:
        error_id = _write_server_error("/api/analyze-runtime:runtime_engine", exc)
        safe_message = _redact_exception_text(str(exc))
        raise HTTPException(
            500,
            f"Runtime Analyzer lỗi ({error_id}): {type(exc).__name__}: {safe_message}. "
            "Xem server_error.log để biết dòng code gây lỗi.",
        ) from exc
    source_mode = "deterministic_only"
    ai_warning = ""

    prompt_logs = combine_for_prompt(parsed_files, prompt_char_limit)
    if use_ai_explanation:
        if not resolved_key:
            ai_warning = f"Không có API key; đã hoàn tất bằng deterministic runtime analyzer. Cấu hình {env_key_name} hoặc nhập key để dùng AI."
        else:
            try:
                insight = analyze_runtime_with_ai(
                    provider=provider,
                    api_key=resolved_key,
                    base_url=base_url or None,
                    model=resolved_model,
                    runtime_analysis=analysis_dict,
                    prepared_logs=prompt_logs,
                )
                insight_dict = insight.model_dump()
                deterministic_reason = analysis_dict.get("slow_reason_summary", "")
                analysis_dict["ai_used"] = True
                analysis_dict["ai_summary"] = insight_dict.get("slow_reason_summary", "")
                if insight_dict.get("slow_reason_summary"):
                    analysis_dict["slow_reason_summary"] = (
                        deterministic_reason + "\n\nAI interpretation: " + insight_dict["slow_reason_summary"]
                    ).strip()
                if insight_dict.get("root_cause_candidates"):
                    analysis_dict["root_cause_candidates"] = insight_dict["root_cause_candidates"]
                for key in ("recommended_checks", "missing_logs_or_data"):
                    merged = []
                    for value in analysis_dict.get(key, []) + insight_dict.get(key, []):
                        if value and value not in merged:
                            merged.append(value)
                    analysis_dict[key] = merged
                source_mode = "deterministic_plus_ai"
            except Exception as exc:
                safe_message = str(exc).replace(resolved_key, "[REDACTED_API_KEY]") if resolved_key != "ollama" else str(exc)
                ai_warning = f"AI runtime explanation failed; deterministic result was kept: {safe_message}"

    file_names = [item.name for item in parsed_files]
    response_provider = provider if source_mode == "deterministic_plus_ai" else "local_runtime_engine"
    response_model = resolved_model if source_mode == "deterministic_plus_ai" else "deterministic-v1"
    meta = {
        "version": "0.9.5",
        "analysis_type": "runtime",
        "selected_port": requested_port,
        "selected_port_label": _port_label(requested_port) if requested_port else "Không xác định",
        "detected_ports": detected_ports,
        "detected_port_labels": [_port_label(item) for item in detected_ports],
        "port_isolation_enabled": True,
        "timing_scope": analysis_dict.get("timing_scope", "individual_phone" if requested_port else "transaction"),
        "batch_envelope_is_reference_only": bool(requested_port),
        "batch_start_time": analysis_dict.get("batch_start_time", ""),
        "batch_end_time": analysis_dict.get("batch_end_time", ""),
        "batch_duration_seconds": analysis_dict.get("batch_duration_seconds"),
        "phone_boundary_explanation": analysis_dict.get("boundary_explanation", ""),
        "provider": response_provider,
        "model": response_model,
        "requested_provider": provider,
        "requested_model": resolved_model,
        "source_mode": source_mode,
        "ai_requested": use_ai_explanation,
        "ai_warning": ai_warning,
        "api_key_stored": False,
        "redacted": redact_sensitive_data,
        "slow_policy": f"slow when total runtime > {slow_threshold_minutes:g} minutes",
        "gap_threshold_seconds": gap_threshold_seconds,
        "prompt_char_limit": prompt_char_limit,
        "selected_prompt_chars": len(prompt_logs),
        "files": [
            {"name": item.name, "total_lines": item.total_lines, "selected_lines": item.selected_lines}
            for item in parsed_files
        ],
        "skipped_files": skipped_files,
        "excluded_other_port_files": excluded_other_ports,
        "excluded_unassigned_files": excluded_unassigned,
        "raw_logs_stored": False,
    }
    try:
        runtime_case_id = save_runtime_case(
            DB_PATH,
            process_label=effective_process_label,
            provider=response_provider,
            model=response_model,
            source_mode=source_mode,
            analysis=analysis_dict,
            meta=meta,
            file_names=file_names,
            redacted=redact_sensitive_data,
        )
    except Exception as exc:
        error_id = _write_server_error("/api/analyze-runtime:save_database", exc)
        safe_message = _redact_exception_text(str(exc))
        raise HTTPException(
            500,
            f"Phân tích đã hoàn tất nhưng không lưu được SQLite ({error_id}): "
            f"{type(exc).__name__}: {safe_message}. Xem server_error.log.",
        ) from exc
    meta["runtime_database"] = {
        "saved_case_id": runtime_case_id,
        "stats": runtime_database_stats(DB_PATH),
    }
    return {"analysis": analysis_dict, "meta": meta}


@app.post("/api/copilot-chat")
async def copilot_chat_endpoint(req: CopilotRequest):
    provider = req.provider
    if provider == "openai":
        env_key_name = "OPENAI_API_KEY"
        fallback_model = "gpt-4o-mini"
    elif provider == "gemini":
        env_key_name = "GEMINI_API_KEY"
        fallback_model = "gemini-2.5-flash"
    elif provider == "ollama":
        env_key_name = "OLLAMA_API_KEY"
        fallback_model = "qwen2.5-coder:1.5b"
    else:
        raise HTTPException(400, f"Unsupported provider: {provider}")

    resolved_key = (req.api_key or "").strip() or os.getenv(env_key_name, "").strip()
    if provider == "ollama" and not resolved_key:
        resolved_key = "ollama"

    if not resolved_key:
        raise HTTPException(
            400,
            f"Vui lòng cung cấp API Key hoặc thiết lập biến môi trường {env_key_name} để trò chuyện với AI Copilot."
        )

    resolved_model = (req.model or "").strip() or fallback_model

    # Mismatch correction logic
    if provider == "openai" and not req.base_url and "gemini" in resolved_model.lower():
        provider = "gemini"
        env_key_name = "GEMINI_API_KEY"
        resolved_key = (req.api_key or "").strip() or os.getenv(env_key_name, "").strip()
    elif provider == "gemini" and "gpt" in resolved_model.lower():
        provider = "openai"
        env_key_name = "OPENAI_API_KEY"
        resolved_key = (req.api_key or "").strip() or os.getenv(env_key_name, "").strip()
    elif provider == "ollama" and ("gemini" in resolved_model.lower() or "gpt" in resolved_model.lower()):
        resolved_model = "qwen2.5-coder:1.5b"

    system_prompt = (
        "You are PhoneBot FA Assistant AI Copilot, a highly precise mobile-phone failure analysis assistant.\n"
        "You are helping a technician debug a mobile phone diagnostic failure.\n\n"
        "Here is the context of the current case being debugged:\n"
        f"- Reported Error: {req.reported_error}\n"
        f"- AI Verdict: {req.verdict}\n"
        f"- Executive Summary: {req.executive_summary}\n"
        "The technician has provided the log excerpt of the failure below.\n"
        "Analyze these logs carefully to answer any technical questions, identify specific failures, point to line numbers or error messages, and suggest targeted repair procedures.\n\n"
        f"--- EVIDENCE LOG EXCERPT ---\n"
        f"{req.evidence_excerpt[:40000]}\n"
        "----------------------------\n\n"
        "STRICT INSTRUCTIONS:\n"
        "1. Focus directly on answering the user's questions based on the provided logs and summary.\n"
        "2. Be concise, technical, and concrete. Do not speculate if not supported by logs.\n"
        "3. Quote specific log lines or timestamps where possible.\n"
        "4. Answer in Vietnamese."
    )

    messages_list = [{"role": msg.role, "content": msg.content} for msg in req.messages]

    try:
        reply = chat_copilot(
            provider=provider,
            api_key=resolved_key,
            base_url=req.base_url or None,
            model=resolved_model,
            system_prompt=system_prompt,
            messages=messages_list,
        )
        return {"reply": reply}
    except Exception as exc:
        safe_message = str(exc).replace(resolved_key, "[REDACTED_API_KEY]") if resolved_key != "ollama" else str(exc)
        raise HTTPException(502, f"AI Copilot failed: {safe_message}") from exc


@app.post("/api/shutdown")
async def shutdown_server():
    import os
    import signal
    import asyncio

    async def kill_later():
        await asyncio.sleep(0.5)
        os.kill(os.getpid(), signal.SIGINT)

    asyncio.create_task(kill_later())
    return {"status": "ok", "message": "Server is shutting down..."}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=False)
