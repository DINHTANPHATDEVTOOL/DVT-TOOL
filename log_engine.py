from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


TIMESTAMP_PATTERNS = [
    re.compile(r"\[(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]"),
    re.compile(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
        r"\d{2}-\w{3}-\d{4}\.(\d{2}:\d{2}:\d{2}\s+[AP]M)",
        re.I,
    ),
]

BASE_KEYWORDS = [
    "BEGIN TRANSACTION",
    "END TRANSACTION",
    "Plug_usb_cable",
    "Connected to v2.0 device",
    "requestShowTrustMessage",
    "already trusted",
    "isTrust",
    "Handshake with device",
    "popup_trust",
    "Not Found Button",
    "Camera Not Recognized",
    "Important Camera Message",
    "not genuine",
    "installed incorrectly",
    "Parts & Service History",
    "Read information Phone SUCCESS",
    "readInformationFor_iOS FAILED",
    "Could not read device",
    "Could not trust",
    "ActivationState",
    "Failed to retrieve activation info",
    "Failed to establish session",
    "MCInstall",
    "countTimeTouchByZ_Axis",
    "FAILED",
    "ERROR",
    "Exception",
    "timeout",
    "Elapsed time",
]


@dataclass
class ParsedFile:
    name: str
    raw_text: str
    numbered_text: str
    selected_text: str
    total_lines: int
    selected_lines: int


def decode_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_time(line: str) -> str:
    for pattern in TIMESTAMP_PATTERNS:
        match = pattern.search(line)
        if match:
            return match.group(1)
    return ""


def redact_sensitive(text: str) -> str:
    replacements = [
        # IMEI / ICCID / long IDs.
        (re.compile(r"(?<!\d)\d{14,22}(?!\d)"), "[REDACTED_LONG_ID]"),
        # Apple-style UDID / serial-like long hex strings.
        (re.compile(r"\b[0-9A-Fa-f]{8,}-[0-9A-Fa-f-]{8,}\b"), "[REDACTED_UDID]"),
        # MAC addresses.
        (re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "[REDACTED_MAC]"),
        # IPv4 addresses.
        (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[REDACTED_IP]"),
        # Common named fields.
        (
            re.compile(
                r"(?im)^(\s*(?:SerialNumber|UDID|IMEI_ESN|InternationalMobileEquipmentIdentity"
                r"|IntegratedCircuitCardIdentity|numberICC|numberIMS)\s*[=:]\s*).+$"
            ),
            r"\1[REDACTED]",
        ),
    ]
    output = text
    for pattern, replacement in replacements:
        output = pattern.sub(replacement, output)
    return output


def claim_keywords(reported_error: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9_]+", reported_error.lower())
    stop = {
        "could", "cannot", "please", "error", "failed", "failure", "test",
        "retest", "with", "from", "this", "that", "the", "and", "not",
    }
    return sorted({word for word in words if len(word) >= 4 and word not in stop})


def select_relevant_lines_keyword(
    text: str,
    reported_error: str,
    context_radius: int = 2,
    max_selected_lines: int = 1800,
) -> tuple[str, int]:
    lines = text.splitlines()
    lowered = [line.lower() for line in lines]

    keywords = [keyword.lower() for keyword in BASE_KEYWORDS]
    keywords.extend(claim_keywords(reported_error))

    selected_indexes: set[int] = set()

    for index, line in enumerate(lowered):
        if any(keyword in line for keyword in keywords):
            start = max(0, index - context_radius)
            end = min(len(lines), index + context_radius + 1)
            selected_indexes.update(range(start, end))

    # Preserve beginning and end for context even if there are few keyword hits.
    selected_indexes.update(range(0, min(35, len(lines))))
    selected_indexes.update(range(max(0, len(lines) - 35), len(lines)))

    ordered = sorted(selected_indexes)
    if len(ordered) > max_selected_lines:
        # Keep the first half and last half of selected evidence.
        half = max_selected_lines // 2
        ordered = ordered[:half] + ordered[-half:]

    numbered = [f"[L{index + 1:05d}] {lines[index]}" for index in ordered]
    return "\n".join(numbered), len(ordered)


def select_relevant_lines_smart(
    text: str,
    reported_error: str,
    context_radius: int = 2,
    max_selected_lines: int = 1800,
) -> tuple[str, str, int]:
    lines = text.splitlines()
    n = len(lines)
    partitions = []
    current_partition = None
    start_pat = re.compile(r"diagItem\.key\s*=\s*(\w+)|Function name\s*=\s*([^,]+),\s*type\s*=\s*\w+,\s*start time", re.I)
    end_pat = re.compile(r"Function name\s*=\s*([^,]+),\s*type\s*=\s*\w+,\s*result\s*=\s*([^,]+)", re.I)
    
    for i, line in enumerate(lines):
        start_match = start_pat.search(line)
        if start_match:
            if current_partition:
                current_partition["end"] = i - 1
                partitions.append(current_partition)
            name = start_match.group(1) or start_match.group(2) or "UnknownTest"
            current_partition = {"start": i, "end": n - 1, "name": name, "result": "unknown", "lines": [i]}
        elif current_partition:
            current_partition["lines"].append(i)
            end_match = end_pat.search(line)
            if end_match:
                current_partition["result"] = end_match.group(2).strip()
                current_partition["end"] = i
                partitions.append(current_partition)
                current_partition = None
    if current_partition:
        partitions.append(current_partition)
        
    if not partitions:
        return "keyword", "", 0
        
    selected_lines = []
    initial_context = set(range(0, min(35, n)))
    selected_lines.extend(initial_context)
    
    partitioned_lines = set()
    for p in partitions:
        partitioned_lines.update(p["lines"])
        
    outside_selected = set()
    keywords = [keyword.lower() for keyword in BASE_KEYWORDS] + claim_keywords(reported_error)
    for i, line in enumerate(lines):
        if i not in partitioned_lines and i not in initial_context:
            if any(kw in line.lower() for kw in keywords):
                for idx in range(max(0, i - context_radius), min(n, i + context_radius + 1)):
                    if idx not in partitioned_lines:
                        outside_selected.add(idx)
    selected_lines.extend(outside_selected)
    
    for p in partitions:
        p_lines = p["lines"]
        p_text = "\n".join(lines[idx] for idx in p_lines).lower()
        is_failed = p["result"].lower() in {"failed", "fail", "error"} or any(
            kw in p_text for kw in ["fail", "error", "exception", "timeout", "retry", "warning", "incorrect"]
        )
        if is_failed:
            selected_lines.extend(p_lines)
        else:
            if len(p_lines) > 2:
                selected_lines.append(p_lines[0])
                selected_lines.append(p_lines[-1])
            else:
                selected_lines.extend(p_lines)
                
    selected_lines.extend(range(max(0, n - 35), n))
    ordered = sorted(list(set(selected_lines)))
    
    if len(ordered) > max_selected_lines:
        half = max_selected_lines // 2
        ordered = ordered[:half] + ordered[-half:]
        
    numbered = [f"[L{index + 1:05d}] {lines[index]}" for index in ordered]
    return "smart", "\n".join(numbered), len(ordered)


def select_relevant_lines(
    text: str,
    reported_error: str,
    context_radius: int = 2,
    max_selected_lines: int = 1800,
) -> tuple[str, int]:
    mode, numbered_text, count = select_relevant_lines_smart(
        text, reported_error, context_radius, max_selected_lines
    )
    if mode == "smart":
        return numbered_text, count
    return select_relevant_lines_keyword(text, reported_error, context_radius, max_selected_lines)



def number_all_lines(text: str) -> str:
    return "\n".join(
        f"[L{index:05d}] {line}"
        for index, line in enumerate(text.splitlines(), start=1)
    )


def prepare_file(
    name: str,
    data: bytes,
    reported_error: str,
    redact: bool,
) -> ParsedFile:
    raw = decode_bytes(data)
    processed = redact_sensitive(raw) if redact else raw
    selected, selected_count = select_relevant_lines(processed, reported_error)
    return ParsedFile(
        name=name,
        raw_text=processed,
        numbered_text=number_all_lines(processed),
        selected_text=selected,
        total_lines=len(processed.splitlines()),
        selected_lines=selected_count,
    )


def combine_for_prompt(
    parsed_files: Iterable[ParsedFile],
    max_chars: int,
) -> str:
    sections: list[str] = []
    used = 0

    for parsed in parsed_files:
        header = (
            f"\n===== FILE: {parsed.name} "
            f"(total_lines={parsed.total_lines}, selected_lines={parsed.selected_lines}) =====\n"
        )
        body = parsed.selected_text
        section = header + body + "\n"

        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(section) > remaining:
            section = section[:remaining] + "\n[TRUNCATED_BY_SERVER]\n"

        sections.append(section)
        used += len(section)

    return "".join(sections)
