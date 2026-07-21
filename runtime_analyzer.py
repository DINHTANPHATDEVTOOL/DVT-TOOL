from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from log_engine import ParsedFile
from schemas import (
    RuntimeAnalysisResult,
    RuntimeEvidenceItem,
    RuntimeGapItem,
    RuntimeProcessItem,
    RuntimeRootCauseCandidate,
    RuntimeStageItem,
    RuntimeRetryTimeoutItem,
    RuntimeRetryTimeoutGroup,
    RuntimePlainContributor,
    RuntimeTimelineInterval,
)


# Common timestamp formats found in PhoneBot trace, Vision/OCR and service logs.
TIMESTAMP_PARSERS: list[tuple[re.Pattern[str], tuple[str, ...]]] = [
    (
        re.compile(r"\[(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]"),
        ("%d-%m-%Y %H:%M:%S.%f", "%d-%m-%Y %H:%M:%S"),
    ),
    (
        re.compile(
            r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
            r"(\d{1,2}-[A-Za-z]{3}-\d{4})[.\s]+"
            r"(\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\s*[AP]M)",
            re.I,
        ),
        ("%d-%b-%Y %I:%M:%S.%f %p", "%d-%b-%Y %I:%M:%S %p"),
    ),
    (
        re.compile(r"\b(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\b"),
        ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"),
    ),
    (
        re.compile(r"\b(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\b"),
        ("%Y/%m/%d %H:%M:%S.%f", "%Y/%m/%d %H:%M:%S"),
    ),
    (
        re.compile(r"\b(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\b"),
        ("%d/%m/%Y %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S"),
    ),
]

BEGIN_RE = re.compile(r"\bBEGIN\s+TRANSACTION\b|\bSTART\s+TRANSACTION\b", re.I)
END_RE = re.compile(r"\bEND\s+TRANSACTION\b|\bFINISH(?:ED)?\s+TRANSACTION\b", re.I)

# BEGIN/END TRANSACTION in PhoneBot can describe the whole 4-port batch, not one phone.
# Per-phone runtime therefore uses port-specific activity boundaries instead.
PHONE_START_RE = re.compile(
    r"(?:start|begin|prepare|load|detect)\s+(?:testing\s+)?(?:phone|device|mobile|process)"
    r"|(?:phone|device|mobile)\s+(?:test|process)\s+(?:start|begin)"
    r"|start\s+(?:test|testing)\s+(?:on\s+)?port\s*\d+",
    re.I,
)
PHONE_END_RE = re.compile(
    r"(?:phone|device|mobile|port\s*\d+)\s+(?:test|process)\s+(?:complete|completed|finish|finished|done)"
    r"|(?:test|process)\s+(?:complete|completed|finish|finished|done)\s+(?:for\s+)?(?:phone|device|mobile|port\s*\d+)"
    r"|(?:final|overall)\s+result\s*[:=]\s*(?:pass|passed|fail|failed)"
    r"|(?:save|write|upload)\s+(?:test\s+)?result(?:.*?(?:pass|passed|fail|failed|success|completed?))?"
    r"|(?:port\s*\d+).*?(?:result\s*[:=]\s*(?:pass|passed|fail|failed)|complete|finished|done)",
    re.I,
)
GLOBAL_BATCH_ONLY_RE = re.compile(
    r"\bBEGIN\s+TRANSACTION\b|\bEND\s+TRANSACTION\b|"
    r"\bSTART\s+TRANSACTION\b|\bFINISH(?:ED)?\s+TRANSACTION\b|"
    r"\bbatch\b|all\s+ports?|all\s+phones?|"
    r"countTimeTouchByZ_Axis|timeTouchByZ_Axis",
    re.I,
)

# Lines below may continue after one phone has already finished because the machine
# is still waiting for other ports. They must not extend an individual-phone runtime.
BACKGROUND_TAIL_RE = re.compile(
    r"heartbeat|keepalive|health\s*check|poll(?:ing)?|watchdog|"
    r"background|service\s+alive|thread\s+alive|timer\s+tick|"
    r"waiting\s+for\s+(?:other|remaining)\s+(?:ports?|phones?)|"
    r"all\s+ports?|all\s+phones?|batch\s+(?:complete|finish|end)|"
    r"countTimeTouchByZ_Axis|timeTouchByZ_Axis",
    re.I,
)

# A fallback phone end must be a real test action/result, not merely any timestamped
# line from debug_service.log or a shared machine process.
STRONG_PHONE_ACTIVITY_RE = re.compile(
    r"(?:start|begin|run|running|test|testing|check|checking|detect|detected|"
    r"found\s+screen|not\s+found|ocr|vision|popup|touch|tap|press|robot|"
    r"z[_ -]?axis|plug|connect|disconnect|trust|pair|handshake|read\s+(?:device|information)|"
    r"camera|display|lcd|face\s*id|sensor|audio|headjack|battery|nfc|"
    r"wireless\s+charging|bluetooth|wi-?fi|flashlight|"
    r"pass(?:ed)?|fail(?:ed)?|success|complete(?:d)?|finish(?:ed)?|done|result)",
    re.I,
)
RETRY_RE = re.compile(r"\bretr(?:y|ies|ying)\b|\btry\s*#?\d+\b|attempt\s*#?\d+", re.I)
TIMEOUT_RE = re.compile(r"time\s*out|timeout|timed\s*out", re.I)
ATTEMPT_RE = re.compile(r"(?:try|attempt|retry)\s*[#:=_-]?\s*(\d+)", re.I)
TIME_VALUE_RE = re.compile(
    r"(?:timeout|time\s*out|timed\s*out|wait(?:ing)?|delay|sleep|elapsed(?:\s*time)?)"
    r"[^\d]{0,24}(\d+(?:\.\d+)?)\s*(ms|msec|milliseconds?|s|sec|seconds?|m|min|minutes?)",
    re.I,
)
COORD_RE = re.compile(
    r"(?:x\s*[=:]\s*(-?\d+(?:\.\d+)?)\D{0,20}y\s*[=:]\s*(-?\d+(?:\.\d+)?)|"
    r"(?:point|rect|coordinate)\s*\(?\s*(-?\d+)\s*[,;]\s*(-?\d+))",
    re.I,
)
SCREEN_ID_RE = re.compile(r"\[([A-Za-z0-9_.-]*(?:popup|screen|button|ok|trust)[A-Za-z0-9_.-]*)\]", re.I)
FAIL_RE = re.compile(r"\bFAILED\b|\bFAIL\b|\bERROR\b|Exception|Could not|Unable to", re.I)
SUCCESS_RE = re.compile(r"\bSUCCESS\b|\bPASSED?\b|completed successfully", re.I)

ELAPSED_RE = re.compile(
    r"(?:Elapsed\s*time|duration|time\s*cost|cost\s*time)\s*[:=]\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:\((?P<paren>ms|s|sec|seconds?)\)|(?P<unit>ms|milliseconds?|s|sec|seconds?))?",
    re.I,
)

STAGE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("finalization", re.compile(r"END TRANSACTION|save result|write result|upload result|finali[sz]|cleanup|release resource|close session", re.I)),
    ("initialization", re.compile(r"BEGIN TRANSACTION|prepare|initiali[sz]|start process|load phone|place phone|home screen|hello screen", re.I)),
    ("robot_z", re.compile(r"Z[_ -]?Axis|robot|countTimeTouch|timeTouch|touch|tap|press button|move[_ -]?[XYZ]|coordinate|actuator", re.I)),
    ("usb_connection", re.compile(r"USB|Plug_usb|cable|Multiplexor|enumerat|connected to v\d|disconnect", re.I)),
    ("trust_pairing", re.compile(r"trust|pairing|pair record|lockdown|handshake|requestShowTrust", re.I)),
    ("device_service", re.compile(r"readInfo|read information|device information|device service|idevice|activation|MCInstall|service session|serialnumber|UDID|IMEI|mobilegestalt", re.I)),
    ("vision_ocr", re.compile(r"vision|OCR|detect screen|found screen|not found button|popup|rect\(|template|confidence|camera image|screenshot", re.I)),
    ("functional_test", re.compile(r"camera|display|face\s*id|speaker|microphone|audio|headjack|NFC|wireless charging|flash\s*light|LCD|sensor|battery|bluetooth|wifi|vibration", re.I)),
    ("retry_timeout", re.compile(r"retry|timeout|timed out|waiting|wait for|sleep|delay|backoff", re.I)),
]

STAGE_LABELS = {
    "initialization": "Khởi tạo / chuẩn bị",
    "usb_connection": "USB / kết nối thiết bị",
    "trust_pairing": "Trust / pairing",
    "device_service": "Đọc thông tin / device service",
    "vision_ocr": "Vision / OCR / popup",
    "robot_z": "Robot / trục Z",
    "functional_test": "Functional test",
    "retry_timeout": "Retry / timeout",
    "finalization": "Kết thúc / ghi kết quả",
    "unknown": "Không xác định / thiếu log",
}

MODULE_LABELS = {
    "software_orchestrator": "SW điều phối",
    "robot_z": "Robot / trục Z",
    "vision_ocr": "Vision / OCR",
    "usb_device": "USB / device connection",
    "device_service": "Device service",
    "trust_pairing": "Trust / pairing",
    "functional_test": "Functional test",
    "finalization": "Lưu kết quả / cleanup",
    "unknown": "Không xác định",
}

TEST_ITEM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Camera", re.compile(r"camera", re.I)),
    ("Face ID", re.compile(r"face\s*id", re.I)),
    ("Display/LCD", re.compile(r"display|lcd|screen test", re.I)),
    ("USB debugging", re.compile(r"usb\s*debug|debug\s*mode", re.I)),
    ("Trust PC", re.compile(r"trust this computer|popup_trust|could not trust|pairing|handshake", re.I)),
    ("Device information", re.compile(r"readInfo|read information|device information|mobilegestalt|serialnumber|udid|imei", re.I)),
    ("Activation", re.compile(r"activation|MCInstall", re.I)),
    ("Audio/Headjack", re.compile(r"audio|speaker|microphone|headjack|headset", re.I)),
    ("NFC", re.compile(r"\bnfc\b", re.I)),
    ("Wireless charging", re.compile(r"wireless charging|wireless charge", re.I)),
    ("Flashlight", re.compile(r"flash\s*light|flashlight", re.I)),
    ("Battery", re.compile(r"battery", re.I)),
    ("Bluetooth/Wi-Fi", re.compile(r"bluetooth|wi-?fi", re.I)),
    ("Sensor", re.compile(r"sensor|proximity|accelerometer|gyroscope", re.I)),
]

# Timestamp/source trust rules for per-phone runtime boundaries.
# Some exported Android/service debug files contain a stale/static timestamp inside
# every file. Those timestamps are still useful as textual evidence, but they must
# never define the beginning or end of the current 4-port batch.
BOUNDARY_CLOCK_TOLERANCE_SECONDS = 2.0
AUXILIARY_BOUNDARY_FILE_RE = re.compile(
    r"debug[_ -]?android|debug[_ -]?service|service[_ -]?debug|dump|snapshot|diagnostic",
    re.I,
)


def _source_reliability(filename: str) -> int:
    name = filename.replace("\\", "/").split("/")[-1].lower()
    if "trace" in name:
        return 100
    if "logfile" in name or "main" in name or "station" in name:
        return 90
    if "vision" in name or "ocr" in name:
        return 80
    if AUXILIARY_BOUNDARY_FILE_RE.search(name):
        return 20
    if "service" in name:
        return 35
    return 60


def _prefer_authoritative_boundary_events(events: list[Event]) -> list[Event]:
    """Prefer station/trace/Vision events for start/end boundaries.

    Auxiliary debug files remain available inside the selected window for retry and
    timeout evidence, but cannot pull the runtime hours before/after the actual run.
    """
    authoritative = [event for event in events if _source_reliability(event.file) >= 60]
    return authoritative if len(authoritative) >= 2 else events



@dataclass
class OperationInfo:
    stage: str
    module: str
    operation: str
    execution_mode: str
    test_item: str = ""
    confidence: str = "medium"



@dataclass
class Event:
    dt: datetime
    file: str
    line: int
    text: str
    stage: str

    @property
    def time_text(self) -> str:
        return self.dt.isoformat(sep=" ", timespec="milliseconds")

    def evidence(self, interpretation: str = "") -> RuntimeEvidenceItem:
        return RuntimeEvidenceItem(
            file=self.file,
            line_start=self.line,
            line_end=self.line,
            time=self.time_text,
            quote=self.text[:700],
            interpretation=interpretation,
        )


@dataclass
class Transaction:
    index: int
    events: list[Event]
    boundary_source: str
    complete: bool



MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
}


def parse_android_phone_timestamp(line: str) -> datetime | None:
    """Parse Android phone logs timestamp (e.g. 01_June_2026_07_13_52_AM) in a locale-independent way."""
    match = re.search(r"^(\d{2})_([A-Za-z]+)_(\d{4})_(\d{2})_(\d{2})_(\d{2})_([AP]M)", line)
    if not match:
        return None
    day = int(match.group(1))
    month_name = match.group(2).lower()
    year = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    second = int(match.group(6))
    period = match.group(7).upper()
    
    month = MONTH_MAP.get(month_name)
    if not month:
        return None
        
    if period == "PM" and hour < 12:
        hour += 12
    elif period == "AM" and hour == 12:
        hour = 0
        
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def parse_weekday_month_timestamp(line: str) -> datetime | None:
    """Parse English weekday month log timestamps (e.g. Thursday, 09-Jul-2026.01:49:03 PM) in a locale-independent way."""
    match = re.search(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s*"
        r"(\d{1,2})-([A-Za-z]{3})-(\d{4})[.\s]+"
        r"(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?\s*([AP]M)",
        line,
        re.I
    )
    if not match:
        return None
        
    day = int(match.group(1))
    month_name = match.group(2).lower()
    year = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    second = int(match.group(6))
    ms = int(match.group(7)) if match.group(7) else 0
    period = match.group(8).upper()
    
    month = MONTH_MAP.get(month_name)
    if not month:
        return None
        
    if period == "PM" and hour < 12:
        hour += 12
    elif period == "AM" and hour == 12:
        hour = 0
        
    try:
        return datetime(year, month, day, hour, minute, second, ms * 1000)
    except ValueError:
        return None


def parse_filename_timestamp(filename: str) -> datetime | None:
    """Extract a timestamp from a filename (e.g. debug_android_port3_09_07_26__14_00_24_func_F18_HPHONE.txt)."""
    # Look for DD_MM_YY__HH_MM_SS pattern
    match = re.search(r"(\d{2})_(\d{2})_(\d{2})__(\d{2})_(\d{2})_(\d{2})", filename)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = 2000 + int(match.group(3))
        hour = int(match.group(4))
        minute = int(match.group(5))
        second = int(match.group(6))
        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            pass
            
    # Also support standard YYYYMMDD_HHMMSS or YYYY-MM-DD_HH-MM-SS patterns
    match2 = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})[_-](\d{2})[-_]?(\d{2})[-_]?(\d{2})", filename)
    if match2:
        year = int(match2.group(1))
        month = int(match2.group(2))
        day = int(match2.group(3))
        hour = int(match2.group(4))
        minute = int(match2.group(5))
        second = int(match2.group(6))
        try:
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            pass
            
    return None


def parse_timestamp(line: str) -> datetime | None:
    # 1. Try custom Android phone format first: 01_June_2026_07_13_52_AM
    res = parse_android_phone_timestamp(line)
    if res is not None:
        return res

    # 2. Try weekday month format: Thursday, 09-Jul-2026.01:49:03 PM
    res2 = parse_weekday_month_timestamp(line)
    if res2 is not None:
        return res2

    # 3. Try other standard numeric formats
    normalized = line.replace(",", ".", 1) if re.search(r"\d{2}:\d{2}:\d{2},\d+", line) else line
    for pattern, formats in TIMESTAMP_PARSERS:
        # Skip index 1 pattern (weekday month format) since it's already handled
        if pattern == TIMESTAMP_PARSERS[1][0]:
            continue
            
        match = pattern.search(normalized)
        if not match:
            continue
            
        # Prevent matching timestamps deep inside messages/JSON payloads
        if match.start() > 15:
            continue
            
        value = " ".join(group for group in match.groups() if group is not None).strip()
        value = re.sub(r"(?<=\d),(?=\d)", ".", value)
        for fmt in formats:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None



def classify_stage(text: str) -> str:
    for name, pattern in STAGE_PATTERNS:
        if pattern.search(text):
            return name
    return "unknown"


def collect_events(parsed_files: Iterable[ParsedFile], max_events: int = 250_000) -> list[Event]:
    events: list[Event] = []
    for parsed in parsed_files:
        file_events = []
        for line_no, line in enumerate(parsed.raw_text.splitlines(), start=1):
            dt = parse_timestamp(line)
            if dt is None:
                continue
            file_events.append((line_no, line, dt))
            
        if not file_events:
            continue
            
        # Detect if we need to apply clock alignment for this file
        filename_dt = parse_filename_timestamp(parsed.name)
        offset = None
        if filename_dt:
            last_file_dt = file_events[-1][2]
            diff = filename_dt - last_file_dt
            # If the clock difference is significant (> 5 minutes), we treat it as unsynced phone clock
            if abs(diff.total_seconds()) > 300:
                offset = diff
                
        for line_no, line, dt in file_events:
            if offset:
                dt = dt + offset
            events.append(Event(dt=dt, file=parsed.name, line=line_no, text=line.strip(), stage=classify_stage(line)))
            if len(events) >= max_events:
                break
        if len(events) >= max_events:
            break
            
    events.sort(key=lambda item: (item.dt, item.file, item.line))
    return events


def build_transactions(events: list[Event]) -> list[Transaction]:
    if not events:
        return []

    transactions: list[Transaction] = []
    current: list[Event] | None = None
    boundary_source = "explicit_begin_end"

    for event in events:
        if BEGIN_RE.search(event.text):
            if current:
                transactions.append(Transaction(len(transactions) + 1, current, boundary_source, False))
            current = [event]
            boundary_source = "explicit_begin_end"
            continue

        if current is not None:
            current.append(event)
            if END_RE.search(event.text):
                transactions.append(Transaction(len(transactions) + 1, current, boundary_source, True))
                current = None

    if current:
        transactions.append(Transaction(len(transactions) + 1, current, "begin_to_last_timestamp", False))

    if transactions:
        return [item for item in transactions if len(item.events) >= 2]

    # No explicit BEGIN/END. Fall back to all timestamped lines as one estimated process.
    return [Transaction(1, events, "first_to_last_timestamp", False)] if len(events) >= 2 else []


def batch_envelope(events: list[Event]) -> tuple[Event | None, Event | None]:
    """Return one authoritative complete batch window for reference and clipping.

    The old implementation used the earliest BEGIN and latest END across every
    uploaded file. With archived/debug files this could span hours or multiple runs.
    We now pair each BEGIN with the first following END, prefer same-file trace
    markers, and select the latest/highest-trust complete batch.
    """
    begins = [event for event in events if BEGIN_RE.search(event.text)]
    ends = [event for event in events if END_RE.search(event.text)]
    if not begins or not ends:
        return None, None

    pairs: list[tuple[int, datetime, Event, Event]] = []
    for begin in sorted(begins, key=lambda item: item.dt):
        # First prefer an END from the same source file.
        same_file = [
            event for event in ends
            if event.file == begin.file and event.dt >= begin.dt
        ]
        candidates = same_file or [event for event in ends if event.dt >= begin.dt]
        if not candidates:
            continue
        end = min(candidates, key=lambda item: item.dt)
        duration = (end.dt - begin.dt).total_seconds()
        if duration < 0:
            continue
        same_file_bonus = 40 if end.file == begin.file else 0
        trust = min(_source_reliability(begin.file), _source_reliability(end.file))
        score = trust + same_file_bonus
        pairs.append((score, end.dt, begin, end))

    if not pairs:
        return None, None

    # Highest-trust pair first; for equal trust use the most recent completed run.
    _, _, start, end = max(pairs, key=lambda item: (item[0], item[1]))
    return start, end


def _is_phone_activity_boundary_candidate(event: Event) -> bool:
    text = event.text.strip()
    if not text or GLOBAL_BATCH_ONLY_RE.search(text):
        return False
    info = operation_info(event)
    if info.execution_mode == "aggregate_only":
        return False
    # Ignore pure generic cleanup/heartbeat lines which commonly continue until
    # the last phone of the batch completes. Keep explicit per-phone result lines.
    if event.stage == "finalization" and not PHONE_END_RE.search(text):
        return False
    if BACKGROUND_TAIL_RE.search(text):
        return False
    return True


def _is_strong_phone_activity(event: Event) -> bool:
    """Whether an event can safely define the active end of one phone.

    A selected port folder can contain shared/background logs that continue until
    the final phone of the 4-port batch finishes. Unknown debug/service lines are
    still useful as evidence inside the active window, but they are not allowed to
    extend the phone runtime when no explicit per-phone completion marker exists.
    """
    text = event.text.strip()
    if not _is_phone_activity_boundary_candidate(event):
        return False
    if PHONE_START_RE.search(text) or PHONE_END_RE.search(text):
        return True
    if BACKGROUND_TAIL_RE.search(text):
        return False
    if event.stage in {"robot_z", "vision_ocr", "functional_test", "trust_pairing"}:
        return True
    if event.stage == "usb_connection":
        # classify_stage also treats a bare "Port 2" as USB context. A port label
        # by itself must not make a shared/background line a strong phone action.
        return bool(re.search(r"USB|Plug_usb|cable|Multiplexor|enumerat|connect(?:ed|ion)?|disconnect", text, re.I))
    if event.stage == "device_service":
        # Device-service output is a boundary only when it contains an actual
        # operation/result. Generic debug lines may continue after phone finish.
        return bool(
            STRONG_PHONE_ACTIVITY_RE.search(text)
            or SUCCESS_RE.search(text)
            or FAIL_RE.search(text)
        )
    if event.stage == "finalization":
        return bool(PHONE_END_RE.search(text) or SUCCESS_RE.search(text) or FAIL_RE.search(text))
    # Unknown-stage lines are not reliable end boundaries unless they explicitly
    # describe an operation or result.
    return bool(STRONG_PHONE_ACTIVITY_RE.search(text) and not re.search(r"debug|trace|status\s*[:=]?\s*idle", text, re.I))


def _choose_fallback_phone_end(candidates: list[Event], start_event: Event) -> Event | None:
    """Choose the last meaningful test activity, excluding the shared batch tail."""
    strong = [event for event in candidates if event.dt > start_event.dt and _is_strong_phone_activity(event)]
    if not strong:
        return None

    # If the final strong event is followed only by weak/background events, keep
    # the final strong event. This is the key fix for 4-port folders where a
    # debug/service line appears again at global END TRANSACTION time.
    return strong[-1]


def build_phone_activity_transaction(events: list[Event]) -> tuple[list[Transaction], dict]:
    """Build one phone/port active window using the current batch clock domain.

    Critical invariant: an individual phone runtime can never be longer than the
    batch that contains it. Events outside the authoritative BEGIN/END window are
    excluded before choosing any per-phone boundary. This prevents stale timestamps
    in debug_android/debug_service exports from creating multi-hour runtimes.
    """
    batch_start, batch_end = batch_envelope(events)
    batch_duration = (
        round((batch_end.dt - batch_start.dt).total_seconds(), 3)
        if batch_start and batch_end else None
    )
    meta = {
        "batch_start_time": batch_start.time_text if batch_start else "",
        "batch_end_time": batch_end.time_text if batch_end else "",
        "batch_duration_seconds": batch_duration,
        "boundary_source": "",
        "boundary_explanation": "",
        "ignored_outside_batch_events": 0,
        "boundary_files": [],
    }

    working_events = events
    if batch_start and batch_end:
        from datetime import timedelta
        lower = batch_start.dt - timedelta(seconds=BOUNDARY_CLOCK_TOLERANCE_SECONDS)
        upper = batch_end.dt + timedelta(seconds=BOUNDARY_CLOCK_TOLERANCE_SECONDS)
        working_events = [event for event in events if lower <= event.dt <= upper]
        meta["ignored_outside_batch_events"] = len(events) - len(working_events)

    raw_candidates = [
        event for event in working_events
        if _is_phone_activity_boundary_candidate(event)
    ]
    candidates = _prefer_authoritative_boundary_events(raw_candidates)
    if len(candidates) < 2:
        return [], meta

    explicit_starts = [event for event in candidates if PHONE_START_RE.search(event.text)]
    if explicit_starts:
        start_event = explicit_starts[0]
    else:
        strong_starts = [event for event in candidates if _is_strong_phone_activity(event)]
        start_event = strong_starts[0] if strong_starts else candidates[0]

    explicit_ends = [
        event for event in candidates
        if event.dt > start_event.dt and PHONE_END_RE.search(event.text)
    ]
    fallback_end = _choose_fallback_phone_end(candidates, start_event)
    end_event = explicit_ends[-1] if explicit_ends else fallback_end

    if end_event is None or end_event.dt <= start_event.dt:
        return [], meta

    # Final sanity rule: never allow an individual phone to exceed its batch.
    if batch_duration is not None:
        phone_duration = (end_event.dt - start_event.dt).total_seconds()
        if phone_duration > batch_duration + (2 * BOUNDARY_CLOCK_TOLERANCE_SECONDS):
            return [], meta

    scoped = [
        event for event in working_events
        if start_event.dt <= event.dt <= end_event.dt
        and not GLOBAL_BATCH_ONLY_RE.search(event.text)
    ]
    if len(scoped) < 2:
        return [], meta

    explicit_start = bool(explicit_starts and start_event in explicit_starts)
    explicit_end = bool(explicit_ends and end_event in explicit_ends)
    if explicit_start and explicit_end:
        source = "explicit_phone_start_end"
        explanation = "Dùng marker bắt đầu và hoàn tất riêng của phone/port; chỉ nhận event nằm trong batch hiện tại."
    elif explicit_end:
        source = "first_port_activity_to_explicit_phone_end"
        explanation = "Dùng hoạt động mạnh đầu tiên của port đến marker hoàn tất riêng; các timestamp ngoài batch hiện tại đã bị loại."
    elif explicit_start:
        source = "explicit_phone_start_to_last_port_activity"
        explanation = "Dùng marker bắt đầu riêng đến hoạt động test mạnh cuối cùng trong batch hiện tại."
    else:
        source = "port_active_window_batch_clipped"
        explanation = (
            "Runtime là ước lượng từ hoạt động test mạnh đầu tiên đến hoạt động test mạnh cuối cùng của đúng port, "
            "sau khi khóa toàn bộ event vào BEGIN/END của batch hiện tại. Debug/service có timestamp cũ hoặc lệch clock "
            "không được phép làm mốc bắt đầu/kết thúc."
        )

    meta["boundary_source"] = source
    meta["boundary_explanation"] = explanation
    meta["boundary_files"] = sorted({start_event.file, end_event.file})
    return [Transaction(1, scoped, source, explicit_end)], meta


def seconds_text(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = max(0.0, float(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    if hours:
        return f"{hours} giờ {minutes:02d} phút {sec:04.1f} giây"
    if minutes:
        return f"{minutes} phút {sec:04.1f} giây"
    return f"{sec:.1f} giây"


def elapsed_value_seconds(match: re.Match[str]) -> float:
    value = float(match.group("value"))
    unit = (match.group("paren") or match.group("unit") or "ms").lower()
    return value / 1000.0 if unit.startswith("m") else value


def _unit_to_seconds(value: float, unit: str) -> float:
    unit = unit.lower()
    if unit.startswith("ms") or unit.startswith("msec") or unit.startswith("millisecond"):
        return value / 1000.0
    if unit in {"m", "min"} or unit.startswith("minute"):
        return value * 60.0
    return value


def extract_declared_timeout_seconds(text: str) -> float | None:
    match = TIME_VALUE_RE.search(text)
    if not match:
        return None
    return round(_unit_to_seconds(float(match.group(1)), match.group(2)), 3)


def extract_test_item(text: str) -> str:
    for label, pattern in TEST_ITEM_PATTERNS:
        if pattern.search(text):
            return label
    return ""


def module_from_filename(filename: str) -> str:
    lower = filename.lower()
    if any(token in lower for token in ("vision", "ocr", "image", "screen")):
        return "vision_ocr"
    if any(token in lower for token in ("robot", "zaxis", "z_axis", "motion", "plc", "axis")):
        return "robot_z"
    if any(token in lower for token in ("idevice", "lockdown", "service", "libimobile", "activation")):
        return "device_service"
    return "software_orchestrator"


def _screen_target(text: str) -> str:
    match = SCREEN_ID_RE.search(text)
    if match:
        return match.group(1)
    if "trust this computer" in text.lower():
        return "Trust This Computer"
    if "camera not recognized" in text.lower():
        return "Camera Not Recognized"
    return ""


def operation_info(event: Event) -> OperationInfo:
    text = event.text
    lower = text.lower()
    test_item = extract_test_item(text)
    screen_target = _screen_target(text)

    if re.search(r"countTimeTouchByZ_Axis|timeTouchByZ_Axis", text, re.I):
        return OperationInfo("robot_z", "robot_z", "Tổng hợp số lần và thời gian chạm của trục Z", "aggregate_only", test_item, "high")
    if re.search(r"Z[_ -]?Axis|touch|tap|press button|move[_ -]?[XYZ]|coordinate|actuator", text, re.I):
        target = screen_target or test_item
        operation = "Trục Z chạm/nhấn trên màn hình"
        if re.search(r"move[_ -]?[XYZ]|Z[_ -]?Axis.*move|move.*Z[_ -]?Axis", text, re.I):
            operation = "Robot di chuyển trục Z"
        if target:
            operation += f" để xử lý {target}"
        return OperationInfo("robot_z", "robot_z", operation, "physical_action", test_item, "high")

    if re.search(r"Not Found Button|not found.*(?:screen|button|popup)|detect screen|Found screen|OCR|template|confidence|rect\(", text, re.I):
        if re.search(r"Not Found|not found", text, re.I):
            operation = "Vision không tìm thấy nút/màn hình mục tiêu"
        elif "ocr" in lower:
            operation = "OCR đọc nội dung trên màn hình phone"
        else:
            operation = "Vision nhận diện màn hình hoặc popup"
        if screen_target:
            operation += f" ({screen_target})"
        elif test_item:
            operation += f" của bước {test_item}"
        return OperationInfo("vision_ocr", "vision_ocr", operation, "software_detection", test_item, "high")

    if re.search(r"Plug_usb_cable|plug.*usb|insert.*cable", text, re.I):
        return OperationInfo("usb_connection", "usb_device", "Thực hiện cắm cáp USB vào phone", "physical_connection_command", test_item, "high")
    if re.search(r"disconnect|reconnect|enumerat|Connected to v\d|Apple USB Multiplexor|waiting.*device|detect.*device", text, re.I):
        if "disconnect" in lower:
            operation = "Xử lý thiết bị USB bị ngắt kết nối"
        elif "reconnect" in lower:
            operation = "Kết nối lại thiết bị USB"
        else:
            operation = "Chờ hệ điều hành enumerate/nhận phone qua USB"
        return OperationInfo("usb_connection", "usb_device", operation, "device_connection", test_item, "high")

    if re.search(r"trust|pairing|pair record|handshake|requestShowTrust", text, re.I):
        if re.search(r"popup|requestShowTrust|show.*trust", text, re.I):
            operation = "Hiển thị, nhận diện hoặc xử lý popup Trust"
        else:
            operation = "Thiết lập Trust/pairing và handshake với phone"
        return OperationInfo("trust_pairing", "trust_pairing", operation, "software_handshake", test_item or "Trust PC", "high")

    if re.search(r"readInfo|read information|device information|mobilegestalt|serialnumber|UDID|IMEI", text, re.I):
        return OperationInfo("device_service", "device_service", "Đọc thông tin thiết bị từ iOS/device service", "service_call", test_item or "Device information", "high")
    if re.search(r"activation|MCInstall|device service|service session|lockdown", text, re.I):
        operation = "Gọi iOS service/activation session"
        return OperationInfo("device_service", "device_service", operation, "service_call", test_item or "Activation", "high")

    if test_item:
        if re.search(r"test|checking|check|run|start|execute|failed|success|pass", text, re.I):
            return OperationInfo("functional_test", "functional_test", f"Đang chạy bài test {test_item}", "test_execution", test_item, "high")
        return OperationInfo("functional_test", "functional_test", f"Đang xử lý bước liên quan {test_item}", "test_execution", test_item, "medium")

    if event.stage == "finalization":
        return OperationInfo("finalization", "finalization", "Lưu kết quả, đóng service hoặc cleanup", "software_finalize", "", "high")
    if event.stage == "initialization":
        return OperationInfo("initialization", "software_orchestrator", "Khởi tạo process và chuẩn bị phone", "software_setup", "", "high")

    source_module = module_from_filename(event.file)
    if event.stage not in {"retry_timeout", "unknown"}:
        mapped_module = {
            "usb_connection": "usb_device",
            "robot_z": "robot_z",
            "vision_ocr": "vision_ocr",
            "trust_pairing": "trust_pairing",
            "device_service": "device_service",
            "functional_test": "functional_test",
            "finalization": "finalization",
        }.get(event.stage, source_module)
        return OperationInfo(event.stage, mapped_module, STAGE_LABELS.get(event.stage, event.stage), "software_operation", test_item, "medium")
    return OperationInfo("unknown", source_module, "Không xác định được thao tác đang chạy", "unknown", test_item, "low")


def _has_direct_operation_marker(text: str) -> bool:
    return bool(re.search(
        r"Z[_ -]?Axis|touch|tap|press button|move[_ -]?[XYZ]|coordinate|actuator|"
        r"\bvision\b|\bOCR\b|detect screen|found screen|not found button|template|confidence|rect\(|"
        r"Plug_usb_cable|plug.*usb|disconnect|reconnect|enumerat|Connected to v\d|Apple USB Multiplexor|"
        r"trust|pairing|handshake|requestShowTrust|readInfo|read information|device information|device service|"
        r"activation|MCInstall|service session|lockdown",
        text,
        re.I,
    ))


def _meaningful_context(events: list[Event], index: int, direction: int, limit: int = 50, max_seconds: float = 900.0) -> tuple[int | None, OperationInfo | None]:
    steps = 0
    cursor = index + direction
    while 0 <= cursor < len(events) and steps < limit:
        candidate = events[cursor]
        delta = abs((candidate.dt - events[index].dt).total_seconds())
        if delta > max_seconds:
            break
        info = operation_info(candidate)
        if info.stage not in {"unknown"} and info.execution_mode != "aggregate_only" and not (RETRY_RE.search(candidate.text) or TIMEOUT_RE.search(candidate.text)):
            return cursor, info
        cursor += direction
        steps += 1
    return None, None


def _latest_test_item(events: list[Event], index: int, limit: int = 100) -> str:
    for cursor in range(index, max(-1, index - limit), -1):
        value = extract_test_item(events[cursor].text)
        if value:
            return value
    return ""


def _likely_trigger(events: list[Event], index: int, fallback: str) -> str:
    for cursor in range(index - 1, max(-1, index - 20), -1):
        event = events[cursor]
        if (events[index].dt - event.dt).total_seconds() > 300:
            break
        if FAIL_RE.search(event.text) or re.search(r"not found|isTrust\s*=\s*0|disconnect|no response|invalid|cannot", event.text, re.I):
            return f"Ngay trước đó log ghi: {event.text[:260]}"
    return fallback


def _declared_attempt(text: str) -> int | None:
    """Return a human-readable attempt number when the log provides one.

    Some PhoneBot logs use a zero-based counter such as ``retry=0`` or
    ``attempt: 0``.  That value is a counter state, not a valid human attempt
    number for the Pydantic schema (which starts at 1).  Keep the raw log line
    as evidence, but return ``None`` instead of crashing the whole analysis.
    """
    match = ATTEMPT_RE.search(text)
    if not match:
        return None
    value = int(match.group(1))
    return value if value >= 1 else None


def _execution_mode_for(module: str, event_type: str) -> str:
    if module == "robot_z":
        return "Lặp thao tác vật lý bằng robot/trục Z" if event_type in {"retry", "repeated_action"} else "SW chờ phản hồi sau thao tác robot/trục Z"
    if module == "vision_ocr":
        return "Lặp nhận diện bằng Vision/OCR" if event_type in {"retry", "repeated_action"} else "Vision/OCR chờ tìm thấy màn hình hoặc nút"
    if module == "usb_device":
        return "Lặp kết nối, enumerate hoặc thao tác cáp USB" if event_type in {"retry", "repeated_action"} else "SW chờ phone xuất hiện/phản hồi qua USB"
    if module == "trust_pairing":
        return "Lặp xử lý Trust/pairing/handshake" if event_type in {"retry", "repeated_action"} else "SW chờ Trust/pairing hoàn tất"
    if module == "device_service":
        return "Gọi lại device service/API" if event_type in {"retry", "repeated_action"} else "SW chờ device service trả kết quả"
    if module == "functional_test":
        return "Chạy lại test item bằng SW" if event_type in {"retry", "repeated_action"} else "SW chờ test item hoàn tất"
    if module == "finalization":
        return "Lặp ghi kết quả/cleanup" if event_type in {"retry", "repeated_action"} else "SW chờ lưu kết quả hoặc đóng tài nguyên"
    return "Retry/wait do SW điều phối nhưng chưa xác định được module đích"


def _initiator(event: Event, target_module: str) -> str:
    source_module = module_from_filename(event.file)
    if source_module == "robot_z":
        return "Bộ điều khiển robot"
    if source_module == "vision_ocr":
        return "Vision/OCR engine"
    if source_module == "device_service":
        return "Device service"
    if target_module == "robot_z":
        return "SW điều phối (ra lệnh cho robot/trục Z)"
    return "SW điều phối"


def _action_signature(event: Event, info: OperationInfo) -> str:
    text = event.text
    screen = _screen_target(text)
    coord = COORD_RE.search(text)
    if info.module == "robot_z" and coord:
        groups = [g for g in coord.groups() if g is not None]
        return f"robot:{','.join(groups)}:{screen}:{info.test_item}"
    if info.module == "robot_z" and screen:
        return f"robot-screen:{screen}:{info.test_item}"
    if info.module == "vision_ocr" and screen:
        action = "not-found" if re.search(r"not found", text, re.I) else "detect"
        return f"vision:{action}:{screen}:{info.test_item}"
    if re.search(r"Plug_usb_cable", text, re.I):
        return "usb:plug-cable"
    if info.module == "trust_pairing" and re.search(r"handshake|pairing|requestShowTrust", text, re.I):
        normalized_operation = re.sub(r"\d+", "#", info.operation.lower())
        return f"trust:{normalized_operation}"
    return ""


def _event_context_evidence(events: list[Event], index: int, before: bool, count: int = 2) -> list[RuntimeEvidenceItem]:
    output: list[RuntimeEvidenceItem] = []
    if before:
        indexes = range(max(0, index - count), index)
    else:
        indexes = range(index + 1, min(len(events), index + count + 1))
    for cursor in indexes:
        output.append(events[cursor].evidence("Ngữ cảnh trước sự kiện" if before else "Ngữ cảnh sau sự kiện"))
    return output


def build_retry_timeout_analysis(
    events: list[Event],
    gaps: list[RuntimeGapItem],
    gap_threshold_seconds: float,
) -> tuple[list[RuntimeRetryTimeoutItem], list[RuntimeRetryTimeoutGroup], dict[str, int], dict[str, int]]:
    raw_items: list[dict] = []

    for index, event in enumerate(events):
        explicit_retry = bool(RETRY_RE.search(event.text))
        explicit_timeout = bool(TIMEOUT_RE.search(event.text))
        if not explicit_retry and not explicit_timeout:
            continue

        direct = operation_info(event)
        previous_index, previous_info = _meaningful_context(events, index, -1)
        next_index, next_info = _meaningful_context(events, index, 1)
        if _has_direct_operation_marker(event.text):
            info = direct
        elif explicit_retry and next_info is not None and next_index is not None and (events[next_index].dt - event.dt).total_seconds() <= 15:
            # A generic SW retry marker followed immediately by an operation means that operation is the repeated target.
            info = next_info
        else:
            info = previous_info or next_info or direct
        test_item = info.test_item or _latest_test_item(events, index)
        event_type = "timeout" if explicit_timeout else "retry"
        declared_timeout = extract_declared_timeout_seconds(event.text) if explicit_timeout else None

        observed_wait: float | None = None
        if explicit_timeout and previous_index is not None:
            observed_wait = max(0.0, (event.dt - events[previous_index].dt).total_seconds())
        elif explicit_retry and index + 1 < len(events):
            # Retry impact starts at the marker and ends at the next logged event, even if that event is a timeout.
            observed_wait = max(0.0, (events[index + 1].dt - event.dt).total_seconds())
        if observed_wait is not None and observed_wait > 3600:
            observed_wait = None

        target_module = info.module if info.module != "software_orchestrator" else module_from_filename(event.file)
        if target_module == "software_orchestrator" and previous_info:
            target_module = previous_info.module
        operation = info.operation
        what = (
            f"Đang {operation.lower()}"
            + (f" trong test item {test_item}" if test_item and test_item.lower() not in operation.lower() else "")
        )
        if event_type == "timeout":
            fallback_trigger = f"Không nhận được kết quả đúng hạn khi {operation.lower()}."
            impact = (
                f"Process phải chờ đến timeout{f' {seconds_text(declared_timeout)}' if declared_timeout is not None else ''} trước khi tiếp tục hoặc chuyển sang xử lý lỗi."
            )
        else:
            fallback_trigger = f"Kết quả lần trước chưa đạt điều kiện nên SW/module lặp lại bước {operation.lower()}."
            impact = "Thao tác hoặc lời gọi được thực hiện lại, làm tăng runtime theo số lần retry và thời gian chờ giữa các lần."

        raw_items.append({
            "event_type": event_type,
            "detection": "explicit",
            "event": event,
            "index": index,
            "stage": info.stage if info.stage != "unknown" else "retry_timeout",
            "operation": operation,
            "what": what,
            "initiator": _initiator(event, target_module),
            "target_module": target_module,
            "execution_mode": _execution_mode_for(target_module, event_type),
            "test_item": test_item,
            "attempt": _declared_attempt(event.text),
            "declared_timeout": declared_timeout,
            "observed_wait": observed_wait,
            "trigger": _likely_trigger(events, index, fallback_trigger),
            "impact": impact,
            "confidence": "high" if direct.stage not in {"unknown", "retry_timeout"} or previous_info is not None else "medium",
            "before": _event_context_evidence(events, index, True),
            "after": _event_context_evidence(events, index, False),
        })

    # Infer repeated actions only when the target is distinctive (same coordinate/screen/action signature).
    last_signatures: dict[str, tuple[int, Event, OperationInfo]] = {}
    for index, event in enumerate(events):
        if RETRY_RE.search(event.text) or TIMEOUT_RE.search(event.text):
            continue
        info = operation_info(event)
        signature = _action_signature(event, info)
        if not signature:
            continue
        previous = last_signatures.get(signature)
        last_signatures[signature] = (index, event, info)
        if not previous:
            continue
        previous_index, previous_event, previous_info = previous
        delta = (event.dt - previous_event.dt).total_seconds()
        if delta <= 0 or delta > 180:
            continue
        test_item = info.test_item or _latest_test_item(events, index)
        raw_items.append({
            "event_type": "repeated_action",
            "detection": "inferred",
            "event": event,
            "index": index,
            "stage": info.stage,
            "operation": info.operation,
            "what": f"Thao tác {info.operation.lower()} được thực hiện lại sau {seconds_text(delta)}",
            "initiator": _initiator(event, info.module),
            "target_module": info.module,
            "execution_mode": _execution_mode_for(info.module, "repeated_action"),
            "test_item": test_item,
            "attempt": None,
            "declared_timeout": None,
            "observed_wait": delta,
            "trigger": "Hai event có cùng mục tiêu/tọa độ hoặc cùng screen ID xuất hiện lặp lại trong thời gian ngắn. Đây là retry suy luận, không phải marker retry trực tiếp.",
            "impact": "Thao tác lặp lại làm tăng runtime; cần đối chiếu ảnh và log phản hồi để xác nhận lần đầu không thành công.",
            "confidence": "medium" if (COORD_RE.search(event.text) or _screen_target(event.text)) else "low",
            "before": [previous_event.evidence("Lần thực hiện trước của cùng thao tác")],
            "after": _event_context_evidence(events, index, False),
        })

    # Convert unexplained long gaps into suspected waits/timeouts, but do not call them confirmed timeouts.
    for gap in gaps[:12]:
        before = next((event for event in events if event.file == gap.before_event.file and event.line == gap.before_event.line_start), None)
        after = next((event for event in events if event.file == gap.after_event.file and event.line == gap.after_event.line_start), None)
        if before is None or after is None:
            continue
        if TIMEOUT_RE.search(before.text) or TIMEOUT_RE.search(after.text):
            continue
        if gap.duration_seconds < gap_threshold_seconds:
            continue
        before_index = events.index(before)
        info = operation_info(before)
        if info.execution_mode == "aggregate_only":
            continue
        test_item = info.test_item or _latest_test_item(events, before_index)
        raw_items.append({
            "event_type": "suspected_timeout",
            "detection": "inferred",
            "event": before,
            "index": before_index,
            "stage": info.stage,
            "operation": info.operation,
            "what": f"Sau bước {info.operation.lower()}, log im lặng {gap.duration_text}",
            "initiator": _initiator(before, info.module),
            "target_module": info.module,
            "execution_mode": _execution_mode_for(info.module, "timeout"),
            "test_item": test_item,
            "attempt": None,
            "declared_timeout": None,
            "observed_wait": gap.duration_seconds,
            "trigger": "Không có marker timeout trực tiếp. Tool suy luận SW/module có thể đang wait, sleep, bị block hoặc chờ phản hồi trong khoảng không có log.",
            "impact": f"Khoảng chờ này đóng góp {gap.duration_text} vào tổng runtime.",
            "confidence": "medium" if info.stage != "unknown" else "low",
            "before": [gap.before_event],
            "after": [gap.after_event],
        })

    # Sort chronologically and de-duplicate inferred events overlapping explicit markers.
    raw_items.sort(key=lambda item: (item["event"].dt, 0 if item["detection"] == "explicit" else 1))
    filtered: list[dict] = []
    for item in raw_items:
        duplicate = False
        for existing in filtered[-4:]:
            same_time = abs((item["event"].dt - existing["event"].dt).total_seconds()) <= 1.0
            same_target = item["target_module"] == existing["target_module"]
            if same_time and same_target and existing["detection"] == "explicit" and item["detection"] == "inferred":
                duplicate = True
                break
        if not duplicate:
            filtered.append(item)

    occurrences: list[RuntimeRetryTimeoutItem] = []
    for occurrence_index, item in enumerate(filtered, start=1):
        wait = item["observed_wait"]
        declared = item["declared_timeout"]
        stage = item["stage"] if item["stage"] in STAGE_LABELS else "unknown"
        occurrences.append(RuntimeRetryTimeoutItem(
            occurrence_index=occurrence_index,
            event_type=item["event_type"],
            detection=item["detection"],
            time=item["event"].time_text,
            stage=stage,
            stage_label=STAGE_LABELS.get(stage, stage),
            operation=item["operation"],
            what_was_happening=item["what"],
            initiator=item["initiator"],
            target_module=MODULE_LABELS.get(item["target_module"], item["target_module"]),
            execution_mode=item["execution_mode"],
            test_item=item["test_item"],
            attempt_number=(
                item["attempt"]
                if isinstance(item.get("attempt"), int) and item["attempt"] >= 1
                else None
            ),
            declared_timeout_seconds=declared,
            declared_timeout_text=seconds_text(declared) if declared is not None else "",
            observed_wait_seconds=round(wait, 3) if wait is not None else None,
            observed_wait_text=seconds_text(wait) if wait is not None else "",
            likely_trigger=item["trigger"],
            impact=item["impact"],
            confidence=item["confidence"],
            current_event=item["event"].evidence("Marker retry/timeout hoặc event lặp được phát hiện tại đây"),
            context_before=item["before"],
            context_after=item["after"],
        ))

    grouped: defaultdict[tuple, list[RuntimeRetryTimeoutItem]] = defaultdict(list)
    for item in occurrences:
        normalized_type = "retry" if item.event_type in {"retry", "repeated_action"} else "timeout"
        key = (normalized_type, item.stage, item.operation, item.initiator, item.target_module, item.execution_mode, item.test_item)
        grouped[key].append(item)

    groups: list[RuntimeRetryTimeoutGroup] = []
    for group_index, (key, items) in enumerate(sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[1][0].time)), start=1):
        event_type, stage, operation, initiator, target_module, execution_mode, test_item = key
        total_wait = sum(item.observed_wait_seconds or 0.0 for item in items)
        detections = {item.detection for item in items}
        detection = "mixed" if len(detections) > 1 else next(iter(detections))
        evidence_wording = "marker trực tiếp" if detection == "explicit" else ("suy luận từ event lặp/gap" if detection == "inferred" else "gồm cả marker trực tiếp và suy luận")
        if event_type == "retry":
            explanation = f"Phát hiện {len(items)} lần lặp ở bước '{operation}' ({evidence_wording}). Người khởi tạo: {initiator}; module bị lặp: {target_module}."
        else:
            explanation = f"Phát hiện {len(items)} timeout/wait khi '{operation}' ({evidence_wording}). SW/module đã chờ tổng cộng khoảng {seconds_text(total_wait)} theo timestamp quan sát được."
        groups.append(RuntimeRetryTimeoutGroup(
            group_index=group_index,
            event_type=event_type,
            detection=detection,
            stage=stage,
            stage_label=STAGE_LABELS.get(stage, stage),
            operation=operation,
            initiator=initiator,
            target_module=target_module,
            execution_mode=execution_mode,
            test_item=test_item,
            count=len(items),
            first_time=items[0].time,
            last_time=items[-1].time,
            total_observed_wait_seconds=round(total_wait, 3),
            total_observed_wait_text=seconds_text(total_wait),
            explanation=explanation,
            occurrence_indexes=[item.occurrence_index for item in items],
        ))

    groups.sort(key=lambda item: (item.total_observed_wait_seconds, item.count), reverse=True)
    for new_index, group in enumerate(groups, start=1):
        group.group_index = new_index

    retry_by_module: Counter[str] = Counter()
    timeout_by_module: Counter[str] = Counter()
    for item in occurrences:
        if item.event_type in {"retry", "repeated_action"}:
            retry_by_module[item.target_module] += 1
        else:
            timeout_by_module[item.target_module] += 1
    return occurrences, groups, dict(retry_by_module), dict(timeout_by_module)


def transaction_metrics(transaction: Transaction, slow_threshold_seconds: float, gap_threshold_seconds: float) -> dict:
    events = transaction.events
    start = events[0]
    end = events[-1]
    duration = max(0.0, (end.dt - start.dt).total_seconds())
    stage_seconds: defaultdict[str, float] = defaultdict(float)
    gaps: list[RuntimeGapItem] = []
    explicit_elapsed: list[dict] = []

    for event in events:
        for match in ELAPSED_RE.finditer(event.text):
            explicit_elapsed.append(
                {
                    "seconds": round(elapsed_value_seconds(match), 3),
                    "stage": event.stage,
                    "label": event.text[:260],
                    "evidence": event.evidence("Thời lượng được ghi trực tiếp trong log").model_dump(),
                }
            )

    for previous, current in zip(events, events[1:]):
        delta = (current.dt - previous.dt).total_seconds()
        if delta < 0 or delta > 12 * 3600:
            continue
        stage_seconds[previous.stage] += delta
        if delta >= gap_threshold_seconds:
            gaps.append(
                RuntimeGapItem(
                    duration_seconds=round(delta, 3),
                    duration_text=seconds_text(delta),
                    start_time=previous.time_text,
                    end_time=current.time_text,
                    suspected_stage=previous.stage,
                    suspected_stage_label=STAGE_LABELS.get(previous.stage, previous.stage),
                    before_event=previous.evidence("Sự kiện cuối cùng trước khoảng chờ"),
                    after_event=current.evidence("Sự kiện đầu tiên sau khoảng chờ"),
                )
            )

    gaps.sort(key=lambda item: item.duration_seconds, reverse=True)
    stage_items = [
        RuntimeStageItem(
            stage=stage,
            label=STAGE_LABELS.get(stage, stage),
            duration_seconds=round(value, 3),
            duration_text=seconds_text(value),
            percent_of_process=round(value / duration * 100, 1) if duration else 0.0,
        )
        for stage, value in stage_seconds.items()
        if value > 0
    ]
    stage_items.sort(key=lambda item: item.duration_seconds, reverse=True)

    retry_timeout_events, retry_timeout_groups, retry_by_module, timeout_by_module = build_retry_timeout_analysis(
        events, gaps, gap_threshold_seconds
    )

    # Build sequential timeline intervals
    timeline_intervals: list[RuntimeTimelineInterval] = []
    if events:
        curr_stage = events[0].stage
        curr_start = events[0].dt
        prev_event = events[0]
        for ev in events[1:]:
            delta = (ev.dt - prev_event.dt).total_seconds()
            if delta >= gap_threshold_seconds:
                dur = (prev_event.dt - curr_start).total_seconds()
                if dur > 0:
                    timeline_intervals.append(
                        RuntimeTimelineInterval(
                            stage=curr_stage,
                            label=STAGE_LABELS.get(curr_stage, curr_stage),
                            start_time=curr_start.isoformat(sep=" ", timespec="milliseconds"),
                            end_time=prev_event.dt.isoformat(sep=" ", timespec="milliseconds"),
                            duration_seconds=round(dur, 3),
                            duration_text=seconds_text(dur),
                            percent_of_process=round(dur / duration * 100, 1) if duration else 0.0,
                            is_gap=False,
                        )
                    )
                timeline_intervals.append(
                    RuntimeTimelineInterval(
                        stage="gap",
                        label="Khoảng chờ (Gap)",
                        start_time=prev_event.time_text,
                        end_time=ev.time_text,
                        duration_seconds=round(delta, 3),
                        duration_text=seconds_text(delta),
                        percent_of_process=round(delta / duration * 100, 1) if duration else 0.0,
                        is_gap=True,
                    )
                )
                curr_stage = ev.stage
                curr_start = ev.dt
            elif ev.stage != curr_stage:
                dur = (ev.dt - curr_start).total_seconds()
                if dur > 0:
                    timeline_intervals.append(
                        RuntimeTimelineInterval(
                            stage=curr_stage,
                            label=STAGE_LABELS.get(curr_stage, curr_stage),
                            start_time=curr_start.isoformat(sep=" ", timespec="milliseconds"),
                            end_time=ev.dt.isoformat(sep=" ", timespec="milliseconds"),
                            duration_seconds=round(dur, 3),
                            duration_text=seconds_text(dur),
                            percent_of_process=round(dur / duration * 100, 1) if duration else 0.0,
                            is_gap=False,
                        )
                    )
                curr_stage = ev.stage
                curr_start = ev.dt
            prev_event = ev

        dur = (events[-1].dt - curr_start).total_seconds()
        if dur > 0:
            timeline_intervals.append(
                RuntimeTimelineInterval(
                    stage=curr_stage,
                    label=STAGE_LABELS.get(curr_stage, curr_stage),
                    start_time=curr_start.isoformat(sep=" ", timespec="milliseconds"),
                    end_time=events[-1].dt.isoformat(sep=" ", timespec="milliseconds"),
                    duration_seconds=round(dur, 3),
                    duration_text=seconds_text(dur),
                    percent_of_process=round(dur / duration * 100, 1) if duration else 0.0,
                    is_gap=False,
                )
            )

    texts = "\n".join(event.text for event in events)
    explicit_retry_count = sum(1 for item in retry_timeout_events if item.event_type == "retry")
    inferred_repeated_action_count = sum(1 for item in retry_timeout_events if item.event_type == "repeated_action")
    retry_count = explicit_retry_count + inferred_repeated_action_count
    explicit_timeout_count = sum(1 for item in retry_timeout_events if item.event_type == "timeout")
    suspected_timeout_count = sum(1 for item in retry_timeout_events if item.event_type == "suspected_timeout")
    timeout_count = explicit_timeout_count + suspected_timeout_count
    fail_count = len(FAIL_RE.findall(texts))
    success_count = len(SUCCESS_RE.findall(texts))

    if fail_count and not success_count:
        result_status = "failed_or_error"
    elif success_count and fail_count:
        result_status = "completed_with_warnings_or_retries"
    elif success_count:
        result_status = "passed_or_success"
    elif transaction.complete:
        result_status = "completed_unknown_result"
    else:
        result_status = "incomplete_or_unknown"

    is_slow = duration > slow_threshold_seconds
    over = max(0.0, duration - slow_threshold_seconds)

    return {
        "process": RuntimeProcessItem(
            process_index=transaction.index,
            classification="slow" if is_slow else "within_expected",
            is_slow=is_slow,
            complete=transaction.complete,
            boundary_source=transaction.boundary_source,
            result_status=result_status,
            start_time=start.time_text,
            end_time=end.time_text,
            total_duration_seconds=round(duration, 3),
            total_duration_text=seconds_text(duration),
            threshold_seconds=round(slow_threshold_seconds, 3),
            threshold_text=seconds_text(slow_threshold_seconds),
            over_threshold_seconds=round(over, 3),
            over_threshold_text=seconds_text(over),
            event_count=len(events),
            retry_count=retry_count,
            explicit_retry_count=explicit_retry_count,
            inferred_repeated_action_count=inferred_repeated_action_count,
            timeout_count=timeout_count,
            explicit_timeout_count=explicit_timeout_count,
            suspected_timeout_count=suspected_timeout_count,
            error_marker_count=fail_count,
            stage_breakdown=stage_items,
            longest_gaps=gaps[:12],
            timeline_intervals=timeline_intervals,
            explicit_elapsed=sorted(explicit_elapsed, key=lambda item: item["seconds"], reverse=True)[:20],
            retry_timeout_events=retry_timeout_events,
            retry_timeout_groups=retry_timeout_groups,
            retry_by_module=retry_by_module,
            timeout_by_module=timeout_by_module,
            start_evidence=start.evidence("Mốc bắt đầu process"),
            end_evidence=end.evidence("Mốc kết thúc hoặc timestamp cuối cùng của process"),
        ),
        "events": events,
    }


def cause_for_stage(stage: str, gap: RuntimeGapItem | None, retry_count: int, timeout_count: int) -> RuntimeRootCauseCandidate:
    labels = {
        "initialization": (
            "Chậm ở giai đoạn khởi tạo hoặc chuẩn bị phone",
            "Khoảng chờ lớn xuất hiện sau bước khởi tạo. Có thể SW đang chờ phone vào đúng màn hình, chờ fixture ổn định hoặc chạy delay chuẩn bị quá dài.",
        ),
        "usb_connection": (
            "Chậm ở USB, cáp hoặc quá trình device enumeration",
            "Thời gian tập trung sau sự kiện USB. Cần kiểm tra thao tác cắm cáp, socket, port, disconnect/reconnect và thời gian hệ điều hành nhận thiết bị.",
        ),
        "trust_pairing": (
            "Chậm ở Trust, pairing hoặc handshake",
            "Log cho thấy khoảng chờ liên quan Trust/pairing. Có thể popup chưa được xử lý, pairing record không ổn định hoặc service chờ handshake đến timeout.",
        ),
        "device_service": (
            "Chậm khi đọc thông tin hoặc gọi device service",
            "Khoảng chờ nằm quanh bước đọc thông tin, activation hoặc service. Có thể service phản hồi chậm, retry hoặc chờ timeout.",
        ),
        "vision_ocr": (
            "Chậm ở Vision/OCR hoặc xử lý popup",
            "Khoảng chờ lớn xuất hiện sau sự kiện nhận diện màn hình/popup. Có thể Vision retry, không tìm thấy nút, màn hình tối hoặc popup chưa được đóng.",
        ),
        "robot_z": (
            "Chậm ở robot hoặc trục Z",
            "Thời gian lớn xuất hiện sau lệnh robot/touch. Có thể robot di chuyển hoặc retry, tọa độ không phù hợp, phone không nhận touch, hoặc SW chờ phản hồi sau thao tác.",
        ),
        "functional_test": (
            "Một bài functional test tiêu tốn nhiều thời gian",
            "Khoảng chờ tập trung tại một hạng mục test như camera, display, audio, NFC hoặc charging. Cần xem log chi tiết của đúng test item đó.",
        ),
        "retry_timeout": (
            "Nhiều lần retry hoặc timeout kéo dài process",
            "Các marker retry/timeout cho thấy process không fail ngay mà chờ đủ timeout hoặc lặp lại nhiều lần trước khi tiếp tục.",
        ),
        "finalization": (
            "Chậm ở bước kết thúc, lưu hoặc đồng bộ kết quả",
            "Process đã gần hoàn tất nhưng mất nhiều thời gian để ghi kết quả, cleanup, đóng service hoặc upload dữ liệu.",
        ),
        "unknown": (
            "Khoảng thời gian không có log hoặc SW bị block",
            "Có khoảng trống dài nhưng không có event đủ rõ để gán cho module. Có thể process con không ghi log, thread bị block hoặc SW đang sleep/chờ event.",
        ),
    }
    title, reasoning = labels.get(stage, labels["unknown"])
    if retry_count or timeout_count:
        reasoning += f" Trong process phát hiện {retry_count} lần retry/lặp thao tác và {timeout_count} timeout hoặc khoảng wait nghi ngờ."
    confidence = "medium"
    if stage == "unknown":
        confidence = "low"
    elif timeout_count > 0 or retry_count >= 2:
        confidence = "high"
    evidence = []
    if gap:
        evidence = [gap.before_event, gap.after_event]
    return RuntimeRootCauseCandidate(cause=title, confidence=confidence, reasoning=reasoning, evidence=evidence)




def _plain_evidence_level(detection: str) -> str:
    if detection == "explicit":
        return "confirmed"
    if detection == "mixed":
        return "mixed"
    return "inferred"


def _plain_contributor_title(group: RuntimeRetryTimeoutGroup) -> str:
    target = group.target_module.lower()
    test = f" – {group.test_item}" if group.test_item else ""
    if group.event_type == "timeout":
        if "vision" in target or "ocr" in target:
            return f"Vision/OCR chờ nhận diện quá lâu{test}"
        if "robot" in target or "trục z" in target:
            return f"SW chờ sau thao tác robot/trục Z{test}"
        if "usb" in target:
            return f"Chờ kết nối hoặc nhận diện USB{test}"
        if "device service" in target:
            return f"Chờ device service phản hồi{test}"
        if "trust" in target or "pair" in target:
            return f"Chờ Trust/pairing hoàn tất{test}"
        if "functional" in target:
            return f"Functional test không hoàn tất đúng thời gian{test}"
        return f"SW có khoảng chờ không xác định{test}"
    if "robot" in target or "trục z" in target:
        return f"Robot/trục Z phải thao tác lại{test}"
    if "vision" in target or "ocr" in target:
        return f"Vision/OCR phải nhận diện lại{test}"
    if "usb" in target:
        return f"USB phải kết nối hoặc enumerate lại{test}"
    if "device service" in target:
        return f"Device service được gọi lại{test}"
    if "functional" in target:
        return f"SW chạy lại functional test{test}"
    return f"SW lặp lại bước xử lý{test}"


def _plain_group_explanation(group: RuntimeRetryTimeoutGroup) -> str:
    test = f" trong bài test {group.test_item}" if group.test_item else ""
    wait = group.total_observed_wait_text or "0 giây"
    if group.event_type == "retry":
        action = (
            f"{group.initiator} đã yêu cầu {group.target_module} lặp bước “{group.operation}” "
            f"{group.count} lần{test}. Từ các marker này đến event kế tiếp ghi nhận tổng khoảng {wait}; "
            "con số này là thời gian liên quan, không có nghĩa module thực thi liên tục trong toàn bộ khoảng đó."
        )
    else:
        action = (
            f"Khi đang “{group.operation}”{test}, {group.initiator} chờ {group.target_module} "
            f"{group.count} lần, với tổng khoảng chờ quan sát được là {wait}."
        )
    if group.detection == "explicit":
        return action + " Log có marker retry/timeout trực tiếp nên sự kiện này được xác nhận rõ."
    if group.detection == "mixed":
        return action + " Một phần có marker trực tiếp, phần còn lại được suy luận từ thao tác lặp hoặc khoảng gap."
    return action + " Đây là suy luận từ thao tác lặp hoặc khoảng log im lặng, chưa chứng minh chắc chắn lỗi vật lý của module."


def _plain_impact(group: RuntimeRetryTimeoutGroup, process_seconds: float) -> str:
    wait = group.total_observed_wait_seconds
    if wait <= 0:
        return "Có lặp thao tác nhưng chưa đo được thời gian phát sinh đáng kể từ timestamp."
    percent = (wait / process_seconds * 100.0) if process_seconds else 0.0
    if wait >= 60:
        return f"Đây là contributor lớn: khoảng {group.total_observed_wait_text}, tương đương gần {percent:.1f}% tổng runtime."
    if wait >= 15:
        return f"Contributor đáng chú ý: khoảng {group.total_observed_wait_text} ({percent:.1f}% tổng runtime)."
    return f"Có ảnh hưởng nhưng nhỏ: khoảng {group.total_observed_wait_text} ({percent:.1f}% tổng runtime)."


def _priority_check_for_group(group: RuntimeRetryTimeoutGroup) -> str:
    target = group.target_module.lower()
    time_window = f"{group.first_time} → {group.last_time}" if group.first_time and group.last_time else "timestamp của nhóm"
    if "robot" in target or "trục z" in target:
        return (
            f"Ưu tiên kiểm tra robot/trục Z tại {time_window}: đối chiếu tọa độ, lệnh touch, trạng thái màn hình trước/sau "
            "và Vision có xác nhận phone đã chuyển màn hình hay chưa."
        )
    if "vision" in target or "ocr" in target:
        return (
            f"Mở ảnh/screenshot Vision tại {time_window}: kiểm tra template, độ sáng, popup che màn hình và lý do nút/màn hình không được nhận diện."
        )
    if "usb" in target:
        return f"Kiểm tra cáp, socket, port và USB enumeration tại {time_window}; tìm disconnect/reconnect hoặc device xuất hiện chậm."
    if "device service" in target or "trust" in target:
        return f"Kiểm tra callback, pairing/lockdown và thời gian phản hồi service tại {time_window}; xác định timeout cấu hình của lời gọi."
    if "functional" in target:
        return f"Tách log của test item {group.test_item or group.operation} tại {time_window} để xác định bước con nào được chạy lại."
    return f"Kiểm tra SW điều phối tại {time_window}: tìm timeout khoảng 30 giây, sleep, thread bị block hoặc callback không quay về."


def build_plain_language_diagnosis(
    primary: RuntimeProcessItem,
    events: list[Event],
) -> tuple[str, list[str], list[RuntimePlainContributor], list[str], list[str], str]:
    groups = list(primary.retry_timeout_groups)
    impactful = [g for g in groups if g.total_observed_wait_seconds >= 5 or g.count >= 3]
    impactful.sort(key=lambda g: (g.total_observed_wait_seconds, g.count), reverse=True)
    selected: list[RuntimeRetryTimeoutGroup] = []
    seen_occurrences: set[int] = set()
    seen_time_wait: set[tuple[str, int]] = set()
    for group in impactful:
        occurrence_set = set(group.occurrence_indexes)
        time_wait_key = (group.first_time, round(group.total_observed_wait_seconds))
        if occurrence_set and occurrence_set & seen_occurrences:
            continue
        if time_wait_key in seen_time_wait:
            continue
        selected.append(group)
        seen_occurrences.update(occurrence_set)
        seen_time_wait.add(time_wait_key)
        if len(selected) >= 5:
            break

    contributors: list[RuntimePlainContributor] = []
    for rank, group in enumerate(selected, start=1):
        contributors.append(RuntimePlainContributor(
            rank=rank,
            title=_plain_contributor_title(group),
            category=f"{group.event_type}/{group.target_module}",
            test_item=group.test_item,
            explanation=_plain_group_explanation(group),
            evidence_level=_plain_evidence_level(group.detection),
            count=group.count,
            observed_wait_seconds=group.total_observed_wait_seconds,
            observed_wait_text=group.total_observed_wait_text,
            impact_summary=_plain_impact(group, primary.total_duration_seconds),
        ))

    concurrency_event = next(
        (event for event in events if re.search(r"receive\s+new\s+request\s+while\s+testing|new request while testing", event.text, re.I)),
        None,
    )

    if contributors:
        first = contributors[0]
        conclusion = (
            f"Process {'CHẬM' if primary.is_slow else 'chưa vượt ngưỡng'}. Vị trí cần ưu tiên kiểm tra nhất là nhóm “{first.title}”. "
            f"Nhóm này xuất hiện {first.count} lần và liên quan khoảng {first.observed_wait_text}."
        )
        if len(contributors) > 1:
            second = contributors[1]
            conclusion += f" Nguyên nhân phụ là “{second.title}” với khoảng {second.observed_wait_text}."
    else:
        conclusion = (
            "Process vượt ngưỡng nhưng log chưa chỉ ra một retry/timeout có thời gian đủ lớn; "
            "cần ưu tiên xem stage breakdown và các khoảng timestamp im lặng."
            if primary.is_slow else
            "Process chưa vượt ngưỡng và không có nhóm retry/timeout đủ lớn để coi là bottleneck chính."
        )

    target_names = {g.target_module.lower() for g in selected}
    if any("robot" in name or "trục z" in name for name in target_names) and any("vision" in name or "ocr" in name for name in target_names):
        conclusion += (
            " Mẫu hành vi cho thấy SW ra lệnh thao tác màn hình, Vision kiểm tra trạng thái, rồi thao tác/nhận diện bị lặp; "
            "đây là dấu hiệu luồng SW–Vision–robot chưa đồng bộ, không tự động đồng nghĩa trục Z bị hỏng."
        )
    if concurrency_event:
        conclusion += (
            " Log còn có marker “receive new request while testing”, cho thấy SW có thể gửi lệnh mới khi lệnh trước chưa hoàn tất."
        )

    high_impact_events = [
        item for item in primary.retry_timeout_events
        if (item.observed_wait_seconds or 0) >= 15
    ]
    high_impact_events.sort(key=lambda item: item.occurrence_index)
    sequence = [
        f"Bắt đầu process lúc {primary.start_time}; kết thúc lúc {primary.end_time}; tổng thời gian {primary.total_duration_text}."
    ]
    for item in high_impact_events[:6]:
        wait = item.observed_wait_text or item.declared_timeout_text or "không đo được"
        test = f" trong test {item.test_item}" if item.test_item else ""
        certainty = "log xác nhận" if item.detection == "explicit" else "tool suy luận"
        activity = item.what_was_happening.strip()
        activity = re.sub(r"^(?:đang\s+){2,}", "Đang ", activity, flags=re.I)
        if not activity.lower().startswith("đang ") and not activity.lower().startswith("sau bước"):
            activity = "Đang " + activity.lower()
        sequence.append(
            f"{item.time}: {activity}{test}; {item.initiator} → {item.target_module}; "
            f"chờ/lặp {wait} ({certainty})."
        )
    if concurrency_event:
        sequence.append(
            f"{concurrency_event.time_text}: SW ghi “receive new request while testing”; có khả năng request mới chồng lên request đang chạy."
        )
    sequence.append(
        f"Kết quả: {'vượt ngưỡng ' + primary.over_threshold_text if primary.is_slow else 'không vượt ngưỡng hiện tại'}."
    )

    not_main: list[str] = []
    for group in groups:
        if group.count >= 3 and group.total_observed_wait_seconds < 5:
            not_main.append(
                f"{group.operation}{' (' + group.test_item + ')' if group.test_item else ''}: có {group.count} marker/lần lặp "
                f"nhưng wait quan sát chỉ {group.total_observed_wait_text}; chưa nên coi đây là nguyên nhân chính."
            )
        if len(not_main) >= 5:
            break
    if not not_main:
        not_main.append("Không có nhóm retry số lượng cao nhưng thời gian phát sinh thấp cần loại trừ riêng.")

    checks: list[str] = []
    for group in selected[:3]:
        check = _priority_check_for_group(group)
        if check not in checks:
            checks.append(check)
    if concurrency_event:
        checks.insert(0, "Kiểm tra cơ chế khóa/cờ is_testing và callback hoàn tất: không cho SW gửi request mới khi request trước vẫn đang chạy.")
    if not checks:
        checks.append("So sánh cùng model phone và cùng flow với một case PASS nhanh để tìm stage lệch thời gian.")

    certainty = (
        "Marker ghi rõ retry/timeout được xem là xác nhận. Repeated action và khoảng gap chỉ là suy luận. "
        "Observed wait là khoảng thời gian giữa event, có thể chồng lấp giữa các nhóm nên không cộng tất cả để suy ra chính xác phần thời gian vượt ngưỡng."
    )
    return conclusion, sequence, contributors, not_main, checks, certainty


def run_rca_dependency_chain(primary_metrics: dict) -> list[RuntimeRootCauseCandidate]:
    events = primary_metrics["events"]
    texts = "\n".join(ev.text for ev in events).lower()
    
    rca_candidates = []
    
    # 1. Check if ADB was offline or device not found
    adb_issue = "device offline" in texts or "device not found" in texts or "adb server" in texts
    # 2. Check if USB disconnect happened
    usb_issue = "usb disconnected" in texts or "plug usb" in texts or "apple usb multiplexor" in texts and "fail" in texts
    # 3. Check if screen mirroring failed
    mirror_issue = "mirror screen fail" in texts or "startmirror" in texts and "fail" in texts or "mirror_screen_failed" in texts
    
    # Trace the dependency chain:
    if usb_issue and adb_issue and mirror_issue:
        rca_candidates.append(
            RuntimeRootCauseCandidate(
                cause="Mất kết nối USB dẫn đến lỗi ADB và Màn hình",
                confidence="high",
                reasoning=(
                    "Chuỗi nhân quả: Cáp USB lỏng/mất kết nối -> ADB rơi vào trạng thái Offline "
                    "-> Không khởi động được Screen Mirroring -> Các bước kiểm thử tự động sau đó bị treo hoặc thất bại."
                ),
                evidence=[]
            )
        )
    elif adb_issue and mirror_issue:
        rca_candidates.append(
            RuntimeRootCauseCandidate(
                cause="ADB Offline dẫn đến lỗi Screen Mirroring",
                confidence="high",
                reasoning=(
                    "Chuỗi nhân quả: Kết nối ADB không ổn định (Offline) -> Trạm không thể giao tiếp với điện thoại "
                    "-> Không thể truyền hình ảnh màn hình (Mirroring) -> Robot/SW không nhận diện được UI và bị treo."
                ),
                evidence=[]
            )
        )
    elif mirror_issue:
        rca_candidates.append(
            RuntimeRootCauseCandidate(
                cause="Lỗi khởi động Screen Mirroring",
                confidence="high",
                reasoning=(
                    "Chuỗi nhân quả: Kết nối ADB bình thường nhưng dịch vụ Mirror Screen bị lỗi "
                    "-> Trạm không nhận diện được hình ảnh để điều khiển -> Treo các test case tự động sử dụng hình ảnh."
                ),
                evidence=[]
            )
        )
        
    # Check other popups/RCA dependencies
    camera_part_issue = "camera not recognized" in texts or "genuine apple part" in texts or "important camera message" in texts
    if camera_part_issue:
        rca_candidates.append(
            RuntimeRootCauseCandidate(
                cause="Popup linh kiện Camera không chính hãng chặn màn hình",
                confidence="high",
                reasoning=(
                    "Chuỗi nhân quả: Phát hiện popup quan trọng của iOS cảnh báo linh kiện camera không chính hãng "
                    "-> Popup xuất hiện đè lên giao diện -> Robot/SW không click được nút chức năng -> Dẫn đến treo và timeout."
                ),
                evidence=[]
            )
        )
        
    trust_popup_issue = "trust this computer" in texts or "popup_trust_pc" in texts
    if trust_popup_issue and ("could not trust" in texts or "is_trust = 0" in texts):
        rca_candidates.append(
            RuntimeRootCauseCandidate(
                cause="Không xác nhận tin cậy thiết bị (Trust popup)",
                confidence="high",
                reasoning=(
                    "Chuỗi nhân quả: Popup 'Tin cậy máy tính này' xuất hiện trên điện thoại nhưng không được click/xác nhận "
                    "-> Quá trình Handshake thất bại -> Trạm không có quyền đọc thông tin chi tiết của thiết bị."
                ),
                evidence=[]
            )
        )
        
    return rca_candidates


def analyze_runtime(
    parsed_files: list[ParsedFile],
    *,
    slow_threshold_minutes: float = 13.0,
    gap_threshold_seconds: float = 30.0,
    process_label: str = "",
    per_phone_mode: bool = False,
) -> RuntimeAnalysisResult:
    slow_threshold_seconds = slow_threshold_minutes * 60.0
    events = collect_events(parsed_files)
    phone_timing_meta = {
        "batch_start_time": "",
        "batch_end_time": "",
        "batch_duration_seconds": None,
        "boundary_source": "",
        "boundary_explanation": "",
        "ignored_outside_batch_events": 0,
        "boundary_files": [],
    }
    if per_phone_mode:
        transactions, phone_timing_meta = build_phone_activity_transaction(events)
    else:
        transactions = build_transactions(events)

    if not transactions:
        return RuntimeAnalysisResult(
            process_label=process_label,
            timing_scope="individual_phone" if per_phone_mode else "transaction",
            batch_start_time=phone_timing_meta.get("batch_start_time", ""),
            batch_end_time=phone_timing_meta.get("batch_end_time", ""),
            batch_duration_seconds=phone_timing_meta.get("batch_duration_seconds"),
            batch_duration_text=seconds_text(phone_timing_meta.get("batch_duration_seconds")),
            boundary_explanation=phone_timing_meta.get("boundary_explanation", ""),
            classification="insufficient_data",
            is_slow=False,
            confidence_score=10,
            threshold_minutes=slow_threshold_minutes,
            threshold_seconds=slow_threshold_seconds,
            total_duration_seconds=None,
            total_duration_text="Không xác định",
            over_threshold_seconds=0,
            over_threshold_text="0 giây",
            executive_summary="Không tìm thấy ít nhất hai timestamp hợp lệ để tính thời gian chạy.",
            slow_reason_summary="Chưa thể xác định process chậm hay không vì log thiếu timestamp hoặc định dạng timestamp chưa được hỗ trợ.",
            primary_process_index=None,
            processes=[],
            stage_breakdown=[],
            longest_gaps=[],
            root_cause_candidates=[],
            recommended_checks=[
                "Bổ sung trace log có BEGIN TRANSACTION và END TRANSACTION.",
                "Kiểm tra log có timestamp đầy đủ đến mili-giây hoặc giây.",
                "Bổ sung log SW, Vision/OCR, device service và robot cùng một lần chạy.",
            ],
            missing_logs_or_data=["Không đủ timestamp để dựng runtime."],
            deterministic_notes=[f"Đã đọc {len(events)} dòng có timestamp."],
            ai_used=False,
            ai_summary="",
        )

    metrics = [transaction_metrics(item, slow_threshold_seconds, gap_threshold_seconds) for item in transactions]
    process_items = [item["process"] for item in metrics]
    primary = max(process_items, key=lambda item: item.total_duration_seconds)
    primary_metrics = next(item for item in metrics if item["process"].process_index == primary.process_index)

    top_stage = primary.stage_breakdown[0] if primary.stage_breakdown else None
    top_gap = primary.longest_gaps[0] if primary.longest_gaps else None
    top_retry_timeout_group = primary.retry_timeout_groups[0] if primary.retry_timeout_groups else None

    causes: list[RuntimeRootCauseCandidate] = []
    
    # Run RCA Dependency Chain Engine
    dependency_causes = run_rca_dependency_chain(primary_metrics)
    causes.extend(dependency_causes)
    
    seen_stages: set[str] = set()
    for rc in dependency_causes:
        if "usb" in rc.cause.lower():
            seen_stages.add("usb_connection")
        if "adb" in rc.cause.lower():
            seen_stages.add("usb_connection")
            seen_stages.add("device_service")
        if "mirror" in rc.cause.lower():
            seen_stages.add("vision_ocr")
            
    if top_gap and top_gap.suspected_stage not in seen_stages:
        causes.append(cause_for_stage(top_gap.suspected_stage, top_gap, primary.retry_count, primary.timeout_count))
        seen_stages.add(top_gap.suspected_stage)
    if top_stage and top_stage.stage not in seen_stages:
        causes.append(cause_for_stage(top_stage.stage, None, primary.retry_count, primary.timeout_count))
        seen_stages.add(top_stage.stage)
    if primary.timeout_count or primary.retry_count >= 2:
        if "retry_timeout" not in seen_stages:
            causes.append(cause_for_stage("retry_timeout", top_gap, primary.retry_count, primary.timeout_count))
            seen_stages.add("retry_timeout")
    if top_gap and top_gap.duration_seconds >= max(120.0, primary.total_duration_seconds * 0.35) and top_gap.suspected_stage == "unknown":
        if "unknown" not in seen_stages:
            causes.append(cause_for_stage("unknown", top_gap, primary.retry_count, primary.timeout_count))

    causes = causes[:4]
    confidence = 88 if primary.complete else 68
    if primary.boundary_source == "first_to_last_timestamp":
        confidence = min(confidence, 55)
    if primary.boundary_source in {"port_activity_first_to_last", "port_active_window", "port_active_window_batch_clipped"}:
        confidence = min(confidence, 72)
    elif primary.boundary_source in {"first_port_activity_to_explicit_phone_end", "explicit_phone_start_to_last_port_activity"}:
        confidence = min(confidence, 82)
    if len(primary.longest_gaps) == 0:
        confidence = min(confidence, 65)

    if primary.is_slow:
        subject = process_label or ("Phone/port đã chọn" if per_phone_mode else f"Process #{primary.process_index}")
        summary = (
            f"{subject} có runtime riêng {primary.total_duration_text}, vượt ngưỡng "
            f"> {slow_threshold_minutes:g} phút là {primary.over_threshold_text}. "
            + ("BEGIN/END TRANSACTION của cả bộ phone không được dùng để tính thời gian riêng của phone này." if per_phone_mode else "Kết quả PASS/FAIL không làm thay đổi quy tắc đánh giá chậm.")
        )
        if top_retry_timeout_group:
            reason = (
                f"Nhóm retry/timeout nổi bật: {top_retry_timeout_group.explanation} "
                f"Tổng thời gian chờ quan sát được của nhóm là {top_retry_timeout_group.total_observed_wait_text}."
            )
            if top_gap:
                reason += f" Khoảng gap lớn nhất là {top_gap.duration_text} sau stage {top_gap.suspected_stage_label}."
        elif top_gap:
            reason = (
                f"Khoảng chờ lớn nhất là {top_gap.duration_text}, xuất hiện sau stage "
                f"{top_gap.suspected_stage_label}. Đây là vị trí cần ưu tiên kiểm tra."
            )
        elif top_stage:
            reason = f"Stage chiếm nhiều thời gian nhất là {top_stage.label}: {top_stage.duration_text}."
        else:
            reason = "Process vượt 13 phút nhưng log chưa đủ chi tiết để xác định module gây chậm."
    else:
        subject = process_label or ("Phone/port đã chọn" if per_phone_mode else f"Process #{primary.process_index}")
        summary = (
            f"{subject} có runtime riêng {primary.total_duration_text}, không vượt ngưỡng "
            f"> {slow_threshold_minutes:g} phút nên được xếp loại chưa chậm theo quy tắc hiện tại."
            + (" Thời gian BEGIN/END TRANSACTION của cả batch chỉ hiển thị để tham khảo." if per_phone_mode else "")
        )
        if top_retry_timeout_group:
            reason = (
                f"Dù chưa vượt ngưỡng, tool phát hiện {top_retry_timeout_group.explanation} "
                f"Tổng thời gian chờ quan sát được: {top_retry_timeout_group.total_observed_wait_text}."
            )
        elif top_gap:
            reason = f"Dù chưa vượt ngưỡng, khoảng chờ lớn nhất là {top_gap.duration_text} tại {top_gap.suspected_stage_label}."
        elif top_stage:
            reason = f"Stage chiếm nhiều thời gian nhất là {top_stage.label}: {top_stage.duration_text}."
        else:
            reason = "Không phát hiện khoảng chờ lớn đáng kể trong log có timestamp."

    checks = [
        "Đối chiếu event ngay trước và sau khoảng gap lớn nhất để xác định SW đang chờ module nào.",
        "Kiểm tra các dòng retry, timeout, wait hoặc sleep trong cùng thời điểm.",
        "So sánh cùng model phone, cùng flow, cùng SW version và cùng port với một case PASS nhanh.",
    ]
    if top_gap:
        stage_checks = {
            "usb_connection": "Đổi cáp/port và kiểm tra disconnect-reconnect hoặc thời gian enumeration.",
            "trust_pairing": "Kiểm tra popup Trust, pairing record và thời điểm robot bấm so với handshake.",
            "device_service": "Kiểm tra idevice/lockdown/activation service và timeout khi đọc thông tin.",
            "vision_ocr": "Kiểm tra ảnh Vision, độ sáng màn hình, template, OCR retry và popup che màn hình.",
            "robot_z": "Kiểm tra log tọa độ, thời gian di chuyển Z, số lần touch và việc iOS có nhận thao tác.",
            "functional_test": "Tách log của test item đang chạy để xem bước con nào chờ lâu.",
            "finalization": "Kiểm tra ghi database, upload result, đóng process con và cleanup resource.",
            "unknown": "Bật log chi tiết cho process/thread không ghi event trong khoảng trống này.",
            "initialization": "Kiểm tra thời gian chuẩn bị phone, detect Home/Hello và delay trước khi bắt đầu test.",
        }
        checks.append(stage_checks.get(top_gap.suspected_stage, "Bật log chi tiết cho stage nghi ngờ."))

    missing = []
    if not primary.complete:
        missing.append("Không có cặp BEGIN/END transaction đầy đủ; runtime có thể là ước lượng.")
    if top_gap and top_gap.suspected_stage == "unknown":
        missing.append("Thiếu event chi tiết trong khoảng gap lớn nhất.")
    all_names = " ".join(parsed.name.lower() for parsed in parsed_files)
    if "vision" not in all_names and "ocr" not in all_names:
        missing.append("Chưa thấy file Vision/OCR được nhận diện theo tên file.")
    if not any("z" in event.text.lower() or "robot" in event.text.lower() for event in primary_metrics["events"]):
        missing.append("Chưa thấy marker robot/trục Z rõ ràng.")

    (
        plain_conclusion,
        diagnosis_sequence,
        main_contributors,
        not_main_contributors,
        priority_checks,
        certainty_explanation,
    ) = build_plain_language_diagnosis(primary, primary_metrics["events"])

    return RuntimeAnalysisResult(
        process_label=process_label,
        timing_scope="individual_phone" if per_phone_mode else "transaction",
        batch_start_time=phone_timing_meta.get("batch_start_time", ""),
        batch_end_time=phone_timing_meta.get("batch_end_time", ""),
        batch_duration_seconds=phone_timing_meta.get("batch_duration_seconds"),
        batch_duration_text=seconds_text(phone_timing_meta.get("batch_duration_seconds")),
        boundary_explanation=phone_timing_meta.get("boundary_explanation", ""),
        classification="slow" if primary.is_slow else "within_expected",
        is_slow=primary.is_slow,
        confidence_score=confidence,
        threshold_minutes=slow_threshold_minutes,
        threshold_seconds=slow_threshold_seconds,
        total_duration_seconds=primary.total_duration_seconds,
        total_duration_text=primary.total_duration_text,
        over_threshold_seconds=primary.over_threshold_seconds,
        over_threshold_text=primary.over_threshold_text,
        executive_summary=summary,
        slow_reason_summary=reason,
        plain_language_conclusion=plain_conclusion,
        diagnosis_sequence=diagnosis_sequence,
        main_contributors=main_contributors,
        not_main_contributors=not_main_contributors,
        priority_checks=priority_checks,
        certainty_explanation=certainty_explanation,
        primary_process_index=primary.process_index,
        processes=process_items,
        stage_breakdown=primary.stage_breakdown,
        longest_gaps=primary.longest_gaps,
        timeline_intervals=primary.timeline_intervals,
        retry_timeout_events=primary.retry_timeout_events,
        retry_timeout_groups=primary.retry_timeout_groups,
        retry_by_module=primary.retry_by_module,
        timeout_by_module=primary.timeout_by_module,
        root_cause_candidates=causes,
        recommended_checks=checks,
        missing_logs_or_data=missing,
        deterministic_notes=[
            f"Đã đọc {len(events)} event có timestamp từ {len(parsed_files)} file.",
            f"Phát hiện {len(process_items)} cửa sổ runtime.",
            ("Đang ở chế độ individual phone: bỏ qua BEGIN/END TRANSACTION của cả batch và dùng activity riêng của port." if per_phone_mode else "Đang ở chế độ transaction."),
            phone_timing_meta.get("boundary_explanation", ""),
            f"Đã loại {phone_timing_meta.get('ignored_outside_batch_events', 0)} event có timestamp nằm ngoài batch hiện tại.",
            f"File dùng làm boundary: {', '.join(phone_timing_meta.get('boundary_files', [])) or 'không xác định'}.",
            "Quy tắc chậm: runtime riêng của phone phải lớn hơn ngưỡng cấu hình (không phụ thuộc PASS hay FAIL).",
            "Thời gian stage được ước lượng bằng khoảng cách giữa các event liên tiếp và gán cho event trước đó.",
            "Retry/timeout được gắn với thao tác gần nhất; mỗi record cho biết initiator, module đích, test item, evidence trước/sau và mức confidence.",
            "Repeated action và suspected timeout là suy luận, luôn được đánh dấu riêng; không được coi là marker trực tiếp.",
        ],
        ai_used=False,
        ai_summary="",
    )
