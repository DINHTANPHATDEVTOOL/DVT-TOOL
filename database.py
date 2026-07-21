from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable


ERROR_CODE_RE = re.compile(r"\b(?:EC\s*[:=]\s*)?([A-Z]{1,4}-?\d{2,6})\b", re.I)
WORD_RE = re.compile(r"[A-Za-z0-9_]+")
STOP_WORDS = {
    "could", "cannot", "please", "error", "failed", "failure", "test",
    "retest", "with", "from", "this", "that", "the", "and", "not",
    "phone", "device", "reported", "unable", "information", "read",
}


@dataclass
class SimilarCase:
    case_id: int
    score: float
    created_at: str
    reported_error: str
    verdict: str
    confidence_score: int
    failure_stage: str
    executive_summary: str
    provider: str
    model: str
    analysis: dict[str, Any]
    reasons: list[str]

    def public_summary(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "score": round(self.score, 4),
            "score_percent": round(self.score * 100, 1),
            "created_at": self.created_at,
            "reported_error": self.reported_error,
            "verdict": self.verdict,
            "confidence_score": self.confidence_score,
            "failure_stage": self.failure_stage,
            "executive_summary": self.executive_summary,
            "provider": self.provider,
            "model": self.model,
            "reasons": self.reasons,
        }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=20)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def initialize_database(db_path: Path) -> None:
    with connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                reported_error TEXT NOT NULL,
                normalized_error TEXT NOT NULL,
                error_codes_json TEXT NOT NULL,
                deterministic_summary TEXT NOT NULL,
                marker_counts_json TEXT NOT NULL,
                evidence_excerpt TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                verdict TEXT NOT NULL,
                confidence_score INTEGER NOT NULL,
                failure_stage TEXT NOT NULL,
                executive_summary TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                meta_json TEXT NOT NULL,
                file_names_json TEXT NOT NULL,
                redacted INTEGER NOT NULL DEFAULT 1,
                source_mode TEXT NOT NULL DEFAULT 'ai_new',
                reused_from_case_id INTEGER,
                embedding_json TEXT,
                FOREIGN KEY(reused_from_case_id) REFERENCES cases(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS idx_cases_created_at
                ON cases(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_cases_normalized_error
                ON cases(normalized_error);
            CREATE INDEX IF NOT EXISTS idx_cases_failure_stage
                ON cases(failure_stage);
            CREATE INDEX IF NOT EXISTS idx_cases_verdict
                ON cases(verdict);
            """
        )
        try:
            db.execute("ALTER TABLE cases ADD COLUMN embedding_json TEXT;")
        except sqlite3.OperationalError:
            pass


def normalize_error(text: str) -> str:
    words = [word.lower() for word in WORD_RE.findall(text)]
    return " ".join(word for word in words if word not in STOP_WORDS)


def extract_error_codes(text: str) -> list[str]:
    codes = {match.group(1).upper() for match in ERROR_CODE_RE.finditer(text)}
    return sorted(codes)


def parse_marker_counts(summary: str) -> dict[str, int]:
    markers: dict[str, int] = {}
    for line in summary.splitlines():
        match = re.match(r"\s*-\s*([a-zA-Z0-9_]+)\s*:\s*(\d+)", line)
        if match:
            markers[match.group(1)] = int(match.group(2))
    return markers


def token_set(text: str) -> set[str]:
    return {
        word.lower()
        for word in WORD_RE.findall(text)
        if len(word) >= 3 and word.lower() not in STOP_WORDS
    }


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


MARKER_WEIGHTS = {
    "trust_failure": 5.0,
    "read_failure": 5.0,
    "activation_failure": 5.0,
    "camera_warning": 3.0,
    "not_genuine_wording": 3.0,
    "installed_incorrectly_wording": 3.0,
    "trust_popup": 2.0,
    "usb_connected": 1.0,
    "trust_success": 1.0,
    "read_success": 1.0,
    "robot_touch": 1.0,
    "transaction_end": 1.0,
}


def marker_similarity(left: dict[str, int], right: dict[str, int]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
        
    dot_product = 0.0
    mag_left_sq = 0.0
    mag_right_sq = 0.0
    
    all_keys = set(left) | set(right)
    for key in all_keys:
        weight = MARKER_WEIGHTS.get(key, 1.0)
        val_l = left.get(key, 0) * weight
        val_r = right.get(key, 0) * weight
        dot_product += val_l * val_r
        mag_left_sq += val_l ** 2
        mag_right_sq += val_r ** 2
        
    if mag_left_sq == 0 or mag_right_sq == 0:
        return 0.0
        
    return dot_product / ((mag_left_sq * mag_right_sq) ** 0.5)




def code_similarity(left: set[str], right: set[str]) -> float:
    if left and right:
        return 1.0 if left & right else 0.0
    if not left and not right:
        return 0.5
    return 0.15


def cosine_similarity(v1: list[float], v2: list[float]) -> float:
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_a = sum(a * a for a in v1) ** 0.5
    norm_b = sum(b * b for b in v2) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def calculate_similarity(
    reported_error: str,
    deterministic_summary: str,
    row: sqlite3.Row,
    query_embedding: list[float] | None = None,
) -> tuple[float, list[str]]:
    current_normalized = normalize_error(reported_error)
    saved_normalized = row["normalized_error"]

    current_tokens = token_set(current_normalized)
    saved_tokens = token_set(saved_normalized)
    token_score = jaccard(current_tokens, saved_tokens)
    sequence_score = SequenceMatcher(None, current_normalized, saved_normalized).ratio()
    text_score = 0.6 * token_score + 0.4 * sequence_score

    vector_score = None
    if query_embedding and "embedding_json" in row.keys() and row["embedding_json"]:
        try:
            saved_embedding = json.loads(row["embedding_json"])
            if isinstance(saved_embedding, list):
                vector_score = cosine_similarity(query_embedding, saved_embedding)
        except Exception:
            pass

    if vector_score is not None:
        scaled_vector_score = max(0.0, vector_score)
        text_score = 0.8 * scaled_vector_score + 0.2 * text_score

    current_markers = parse_marker_counts(deterministic_summary)
    saved_markers = json.loads(row["marker_counts_json"] or "{}")
    markers_score = marker_similarity(current_markers, saved_markers)

    current_codes = set(extract_error_codes(reported_error))
    saved_codes = set(json.loads(row["error_codes_json"] or "[]"))
    codes_score = code_similarity(current_codes, saved_codes)

    score = 0.50 * text_score + 0.32 * markers_score + 0.18 * codes_score
    reasons: list[str] = []

    if vector_score is not None:
        if vector_score >= 0.85:
            reasons.append(f"Tương đồng ngữ nghĩa AI rất cao ({vector_score*100:.1f}%)")
        elif vector_score >= 0.70:
            reasons.append(f"Tương đồng ngữ nghĩa AI cao ({vector_score*100:.1f}%)")
        elif vector_score >= 0.50:
            reasons.append(f"Tương đồng ngữ nghĩa AI trung bình ({vector_score*100:.1f}%)")

    if current_normalized and current_normalized == saved_normalized:
        score = max(score, 0.96)
        reasons.append("Nội dung lỗi chuẩn hóa giống hệt")
    elif text_score >= 0.75 and vector_score is None:
        reasons.append("Nội dung lỗi gần giống")

    common_codes = current_codes & saved_codes
    if common_codes:
        reasons.append("Trùng mã lỗi: " + ", ".join(sorted(common_codes)))
    elif current_codes and saved_codes:
        reasons.append("Mã lỗi khác nhau")

    if markers_score >= 0.85:
        reasons.append("Dấu hiệu deterministic gần giống")
    elif markers_score >= 0.60:
        reasons.append("Một phần dấu hiệu log tương đồng")

    if text_score >= 0.78 and markers_score >= 0.85 and common_codes:
        score = max(score, 0.93)

    return min(max(score, 0.0), 1.0), reasons


def find_similar_cases(
    db_path: Path,
    *,
    reported_error: str,
    deterministic_summary: str,
    query_embedding: list[float] | None = None,
    limit: int = 3,
    minimum_score: float = 0.58,
    scan_limit: int = 1000,
) -> list[SimilarCase]:
    initialize_database(db_path)
    with connect(db_path) as db:
        rows = db.execute(
            "SELECT * FROM cases ORDER BY id DESC LIMIT ?",
            (scan_limit,),
        ).fetchall()

    matches: list[SimilarCase] = []
    for row in rows:
        score, reasons = calculate_similarity(reported_error, deterministic_summary, row, query_embedding)
        if score < minimum_score:
            continue
        try:
            analysis = json.loads(row["analysis_json"])
        except json.JSONDecodeError:
            continue
        matches.append(
            SimilarCase(
                case_id=row["id"],
                score=score,
                created_at=row["created_at"],
                reported_error=row["reported_error"],
                verdict=row["verdict"],
                confidence_score=row["confidence_score"],
                failure_stage=row["failure_stage"],
                executive_summary=row["executive_summary"],
                provider=row["provider"],
                model=row["model"],
                analysis=analysis,
                reasons=reasons,
            )
        )

    matches.sort(key=lambda item: (item.score, item.case_id), reverse=True)
    return matches[:limit]


def build_similar_cases_context(cases: Iterable[SimilarCase], max_chars: int = 18000) -> str:
    sections: list[str] = []
    used = 0
    for item in cases:
        analysis = item.analysis
        root_causes = analysis.get("root_cause_candidates", [])[:3]
        compact = {
            "case_id": item.case_id,
            "similarity": round(item.score, 3),
            "reported_error": item.reported_error,
            "verdict": item.verdict,
            "confidence_score": item.confidence_score,
            "failure_stage": item.failure_stage,
            "executive_summary": item.executive_summary,
            "root_cause_candidates": root_causes,
            "recommended_checks": analysis.get("recommended_checks", [])[:6],
            "what_is_proven": analysis.get("what_is_proven", [])[:5],
            "what_is_not_proven": analysis.get("what_is_not_proven", [])[:5],
        }
        section = json.dumps(compact, ensure_ascii=False, indent=2)
        if used + len(section) > max_chars:
            break
        sections.append(section)
        used += len(section)
    if not sections:
        return ""
    return (
        "Các case dưới đây chỉ là kinh nghiệm tham khảo, KHÔNG phải bằng chứng cho "
        "case hiện tại. Phải ưu tiên log mới và nêu rõ khi case mới khác case cũ.\n\n"
        + "\n\n--- PREVIOUS CASE ---\n".join(sections)
    )


def save_case(
    db_path: Path,
    *,
    reported_error: str,
    deterministic_summary: str,
    evidence_excerpt: str,
    provider: str,
    model: str,
    analysis: dict[str, Any],
    meta: dict[str, Any],
    file_names: list[str],
    redacted: bool,
    source_mode: str,
    reused_from_case_id: int | None = None,
    embedding: list[float] | None = None,
) -> int:
    initialize_database(db_path)
    marker_counts = parse_marker_counts(deterministic_summary)
    with connect(db_path) as db:
        cursor = db.execute(
            """
            INSERT INTO cases (
                created_at, reported_error, normalized_error, error_codes_json,
                deterministic_summary, marker_counts_json, evidence_excerpt,
                provider, model, verdict, confidence_score, failure_stage,
                executive_summary, analysis_json, meta_json, file_names_json,
                redacted, source_mode, reused_from_case_id, embedding_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                reported_error,
                normalize_error(reported_error),
                json.dumps(extract_error_codes(reported_error), ensure_ascii=False),
                deterministic_summary,
                json.dumps(marker_counts, ensure_ascii=False),
                evidence_excerpt,
                provider,
                model,
                analysis.get("verdict", "unknown"),
                int(analysis.get("confidence_score", 0)),
                analysis.get("failure_stage", ""),
                analysis.get("executive_summary", ""),
                json.dumps(analysis, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
                json.dumps(file_names, ensure_ascii=False),
                1 if redacted else 0,
                source_mode,
                reused_from_case_id,
                json.dumps(embedding) if embedding else None,
            ),
        )
        db.commit()
        return int(cursor.lastrowid)


def list_cases(db_path: Path, *, limit: int = 30, query: str = "") -> list[dict[str, Any]]:
    initialize_database(db_path)
    limit = max(1, min(limit, 200))
    with connect(db_path) as db:
        if query.strip():
            like = f"%{query.strip()}%"
            rows = db.execute(
                """
                SELECT id, created_at, reported_error, verdict, confidence_score,
                       failure_stage, executive_summary, provider, model, source_mode,
                       reused_from_case_id
                FROM cases
                WHERE reported_error LIKE ? OR executive_summary LIKE ?
                   OR failure_stage LIKE ? OR verdict LIKE ?
                ORDER BY id DESC LIMIT ?
                """,
                (like, like, like, like, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT id, created_at, reported_error, verdict, confidence_score,
                       failure_stage, executive_summary, provider, model, source_mode,
                       reused_from_case_id
                FROM cases ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_case(db_path: Path, case_id: int) -> dict[str, Any] | None:
    initialize_database(db_path)
    with connect(db_path) as db:
        row = db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    for key in ("analysis_json", "meta_json", "file_names_json", "error_codes_json", "marker_counts_json"):
        try:
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
        except json.JSONDecodeError:
            result[key.removesuffix("_json")] = None
            result.pop(key, None)
    return result



def delete_case(db_path: Path, case_id: int) -> bool:
    initialize_database(db_path)
    with connect(db_path) as db:
        cursor = db.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        db.commit()
        return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Runtime-performance history (separate from failure-analysis cases)
# ---------------------------------------------------------------------------

def initialize_runtime_database(db_path: Path) -> None:
    with connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                process_label TEXT NOT NULL DEFAULT '',
                classification TEXT NOT NULL,
                is_slow INTEGER NOT NULL DEFAULT 0,
                confidence_score INTEGER NOT NULL DEFAULT 0,
                threshold_minutes REAL NOT NULL,
                total_duration_seconds REAL,
                over_threshold_seconds REAL NOT NULL DEFAULT 0,
                executive_summary TEXT NOT NULL,
                slow_reason_summary TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                source_mode TEXT NOT NULL,
                analysis_json TEXT NOT NULL,
                meta_json TEXT NOT NULL,
                file_names_json TEXT NOT NULL,
                redacted INTEGER NOT NULL DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_cases_created_at
                ON runtime_cases(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_runtime_cases_classification
                ON runtime_cases(classification);
            CREATE INDEX IF NOT EXISTS idx_runtime_cases_duration
                ON runtime_cases(total_duration_seconds DESC);
            """
        )


def save_runtime_case(
    db_path: Path,
    *,
    process_label: str,
    provider: str,
    model: str,
    source_mode: str,
    analysis: dict[str, Any],
    meta: dict[str, Any],
    file_names: list[str],
    redacted: bool,
) -> int:
    initialize_runtime_database(db_path)
    with connect(db_path) as db:
        cursor = db.execute(
            """
            INSERT INTO runtime_cases (
                created_at, process_label, classification, is_slow,
                confidence_score, threshold_minutes, total_duration_seconds,
                over_threshold_seconds, executive_summary, slow_reason_summary,
                provider, model, source_mode, analysis_json, meta_json,
                file_names_json, redacted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                process_label,
                analysis.get("classification", "insufficient_data"),
                1 if analysis.get("is_slow") else 0,
                int(analysis.get("confidence_score", 0)),
                float(analysis.get("threshold_minutes", 13.0)),
                analysis.get("total_duration_seconds"),
                float(analysis.get("over_threshold_seconds", 0)),
                analysis.get("executive_summary", ""),
                analysis.get("slow_reason_summary", ""),
                provider,
                model,
                source_mode,
                json.dumps(analysis, ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
                json.dumps(file_names, ensure_ascii=False),
                1 if redacted else 0,
            ),
        )
        db.commit()
        return int(cursor.lastrowid)


def list_runtime_cases(db_path: Path, *, limit: int = 30, query: str = "") -> list[dict[str, Any]]:
    initialize_runtime_database(db_path)
    limit = max(1, min(limit, 200))
    with connect(db_path) as db:
        if query.strip():
            like = f"%{query.strip()}%"
            rows = db.execute(
                """
                SELECT id, created_at, process_label, classification, is_slow,
                       confidence_score, threshold_minutes, total_duration_seconds,
                       over_threshold_seconds, executive_summary, slow_reason_summary,
                       provider, model, source_mode
                FROM runtime_cases
                WHERE process_label LIKE ? OR executive_summary LIKE ?
                   OR slow_reason_summary LIKE ? OR classification LIKE ?
                ORDER BY id DESC LIMIT ?
                """,
                (like, like, like, like, limit),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT id, created_at, process_label, classification, is_slow,
                       confidence_score, threshold_minutes, total_duration_seconds,
                       over_threshold_seconds, executive_summary, slow_reason_summary,
                       provider, model, source_mode
                FROM runtime_cases ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_runtime_case(db_path: Path, case_id: int) -> dict[str, Any] | None:
    initialize_runtime_database(db_path)
    with connect(db_path) as db:
        row = db.execute("SELECT * FROM runtime_cases WHERE id = ?", (case_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    for key in ("analysis_json", "meta_json", "file_names_json"):
        try:
            result[key.removesuffix("_json")] = json.loads(result.pop(key))
        except json.JSONDecodeError:
            result[key.removesuffix("_json")] = None
            result.pop(key, None)
    return result


def delete_runtime_case(db_path: Path, case_id: int) -> bool:
    initialize_runtime_database(db_path)
    with connect(db_path) as db:
        cursor = db.execute("DELETE FROM runtime_cases WHERE id = ?", (case_id,))
        db.commit()
        return cursor.rowcount > 0


def runtime_database_stats(db_path: Path) -> dict[str, Any]:
    initialize_runtime_database(db_path)
    with connect(db_path) as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total_runtime_cases,
                   SUM(CASE WHEN is_slow = 1 THEN 1 ELSE 0 END) AS slow_cases,
                   SUM(CASE WHEN is_slow = 0 AND classification = 'within_expected' THEN 1 ELSE 0 END) AS normal_cases,
                   SUM(CASE WHEN source_mode = 'deterministic_plus_ai' THEN 1 ELSE 0 END) AS ai_runtime_runs,
                   AVG(total_duration_seconds) AS average_duration_seconds,
                   MAX(total_duration_seconds) AS longest_duration_seconds,
                   MAX(created_at) AS latest_runtime_at
            FROM runtime_cases
            """
        ).fetchone()
    return {
        "total_runtime_cases": int(row["total_runtime_cases"] or 0),
        "slow_cases": int(row["slow_cases"] or 0),
        "normal_cases": int(row["normal_cases"] or 0),
        "ai_runtime_runs": int(row["ai_runtime_runs"] or 0),
        "average_duration_seconds": round(float(row["average_duration_seconds"] or 0), 3),
        "longest_duration_seconds": round(float(row["longest_duration_seconds"] or 0), 3),
        "latest_runtime_at": row["latest_runtime_at"],
    }


# Redefine the aggregate stats function so the dashboard can display both databases.
def database_stats(db_path: Path) -> dict[str, Any]:
    initialize_database(db_path)
    initialize_runtime_database(db_path)
    with connect(db_path) as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total_cases,
                   SUM(CASE WHEN source_mode = 'database_cache' THEN 1 ELSE 0 END) AS cached_runs,
                   SUM(CASE WHEN source_mode = 'ai_with_history' THEN 1 ELSE 0 END) AS ai_with_history_runs,
                   SUM(CASE WHEN source_mode = 'ai_new' THEN 1 ELSE 0 END) AS new_ai_runs,
                   MAX(created_at) AS latest_case_at
            FROM cases
            """
        ).fetchone()
    runtime = runtime_database_stats(db_path)
    return {
        "total_cases": int(row["total_cases"] or 0),
        "cached_runs": int(row["cached_runs"] or 0),
        "ai_with_history_runs": int(row["ai_with_history_runs"] or 0),
        "new_ai_runs": int(row["new_ai_runs"] or 0),
        "latest_case_at": row["latest_case_at"],
        "database_path": str(db_path),
        **runtime,
    }
