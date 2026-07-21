from __future__ import annotations

import re
from collections import Counter
from log_engine import ParsedFile


RULES = {
    "usb_connected": re.compile(
        r"Plug_usb_cable|Connected to v2\.0 device|Apple USB Multiplexor", re.I
    ),
    "trust_success": re.compile(
        r"Detect already trusted device|iPhone\.isTrust\s*=\s*1|"
        r"devInfo\.isTrust\s*1|Handshake with device .* succeeded|check pair\s+0",
        re.I,
    ),
    "trust_failure": re.compile(
        r"Could not trust|iPhone\.isTrust\s*=\s*0|\bF001\b", re.I
    ),
    "trust_popup": re.compile(
        r"popup_trust_PC|Trust This Computer", re.I
    ),
    "camera_warning": re.compile(
        r"Camera Not Recognized|Important Camera Message|"
        r"popup_important_camera_message_ok",
        re.I,
    ),
    "not_genuine_wording": re.compile(
        r"not genuine Apple part", re.I
    ),
    "installed_incorrectly_wording": re.compile(
        r"installed incorrectly", re.I
    ),
    "read_success": re.compile(
        r"Read information Phone SUCCESS|\[readInfo\] SUCCESS", re.I
    ),
    "read_failure": re.compile(
        r"readInformationFor_iOS FAILED|Could not read device", re.I
    ),
    "activation_failure": re.compile(
        r"Failed to retrieve activation info|Failed to establish session|"
        r"Could not start service MCInstall",
        re.I,
    ),
    "robot_touch": re.compile(
        r"countTimeTouchByZ_Axis\s*=\s*(\d+).*"
        r"timeTouchByZ_Axis\s*=\s*(\d+)",
        re.I,
    ),
    "transaction_end": re.compile(r"END TRANSACTION", re.I),
}


TIMESTAMP_RE = re.compile(
    r"\[(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)\]|"
    r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"\d{2}-\w{3}-\d{4}\.(\d{2}:\d{2}:\d{2}\s+[AP]M)",
    re.I
)

def extract_time_local(line: str) -> str:
    match = TIMESTAMP_RE.search(line)
    if match:
        return match.group(1) or match.group(2) or ""
    return ""

def pre_scan(parsed_files: list[ParsedFile]) -> str:
    counts: Counter[str] = Counter()
    touch_count = None
    touch_ms = None
    critical_events = []

    critical_patterns = {
        "trust_failure": "Lỗi Trust/Handshake",
        "camera_warning": "Cảnh báo Camera",
        "not_genuine_wording": "Linh kiện không chính hãng (not genuine)",
        "installed_incorrectly_wording": "Lắp đặt không đúng kỹ thuật",
        "read_failure": "Lỗi đọc thông tin thiết bị (Read Info Failed)",
        "activation_failure": "Lỗi kích hoạt / Activation FAILED",
    }

    for parsed in parsed_files:
        for idx, line in enumerate(parsed.raw_text.splitlines(), start=1):
            for name, pattern in RULES.items():
                match = pattern.search(line)
                if match:
                    counts[name] += 1
                    if name == "robot_touch":
                        touch_count = int(match.group(1))
                        touch_ms = int(match.group(2))
                    if name in critical_patterns:
                        timestamp = extract_time_local(line)
                        ts_str = f" [{timestamp}]" if timestamp else ""
                        critical_events.append(
                            f"- {critical_patterns[name]} trong file `{parsed.name}` dòng {idx}:{ts_str} \"{line.strip()[:140]}\""
                        )

    rows = [
        f"- {name}: {count}"
        for name, count in sorted(counts.items())
    ]
    if touch_count is not None:
        rows.append(
            f"- robot_touch_summary: count={touch_count}, total_ms={touch_ms}"
        )
        
    res = []
    if rows:
        res.append("Thống kê dấu hiệu phát hiện:")
        res.extend(rows)
        
    if critical_events:
        res.append("\nCÁC SỰ KIỆN LỖI/CẢNH BÁO TRỰC TIẾP PHÁT HIỆN ĐƯỢC:")
        res.extend(critical_events[:25]) # Cap at 25 items
        
    if not res:
        return "- Không tìm thấy dấu hiệu lỗi trực tiếp từ pre-scan."
    return "\n".join(res)

