from __future__ import annotations

import json
from typing import Literal

from schemas import AnalysisResult

Provider = Literal["openai", "gemini", "ollama"]

SYSTEM_PROMPT = """
You are PhoneBot FA Assistant, a senior failure-analysis engineer for automated
mobile-phone test stations.

Verify whether the station's REPORTED ERROR is supported by the uploaded logs,
reconstruct the test flow, identify the actual failure stage, and separate
direct evidence from inference.

STRICT EVIDENCE RULES:
1. Never claim a mechanical action occurred merely because a vision popup was detected.
2. Never claim a component is counterfeit merely because iOS says "not recognized".
3. A log can contain PASS and FAIL at different retries. Explain the sequence.
4. Every important factual conclusion must cite exact file names and line ranges.
5. Do not invent missing events, coordinates, robot force, confidence, pairing state,
   or hardware condition.
6. Verdicts: correct, partially_correct, likely_incorrect, insufficient_evidence.
7. Confidence means confidence in the verdict, not in a guessed cause.
8. Write clear Vietnamese for technicians; preserve important English log messages.
9. Rank root-cause candidates conservatively as high/medium/low.
10. Explicitly name missing logs/data.
11. Analyze the chain by layers when evidence exists: SW orchestration -> device/USB
    service -> vision/OCR -> robot/Z-axis -> final error mapping.
12. A Z-axis touch counter proves only that a touch command was recorded; it does not
    prove the intended button was physically pressed or accepted by iOS.
13. Previous database cases are engineering references only. They are not evidence for
    the current case. Never copy a previous conclusion when the current logs differ.
14. AVOID VAGUENESS: Do not use ambiguous phrases like "có thể do", "không rõ". If the logs show a direct error (e.g. from the DETERMINISTIC PRE-SCAN), state it immediately as a proven fact.
15. STRUCTURED CONCLUSION: The `executive_summary` MUST begin with a clear, one-sentence statement of the exact failure cause and the layer it occurred on.
""".strip()


def build_user_content(
    *,
    reported_error: str,
    prepared_logs: str,
    deterministic_summary: str,
    similar_cases_context: str = "",
) -> str:
    history_section = ""
    if similar_cases_context.strip():
        history_section = f"""

SIMILAR CASES FROM LOCAL DATABASE:
{similar_cases_context}
"""

    return f"""
REPORTED ERROR:
{reported_error}

DETERMINISTIC PRE-SCAN:
{deterministic_summary}

SELECTED LOG EVIDENCE:
{prepared_logs}
{history_section}

Analyze whether the error is correct, partially correct, likely incorrect, or
not verifiable. Reconstruct the chronology and identify the exact failure layer:
SW, device/USB service, vision/OCR, robot/Z-axis, or error mapping.
When database cases are supplied, compare them with the current evidence and state
only conclusions supported by the current logs.
""".strip()


def analyze_with_openai(
    *, api_key: str, base_url: str | None, model: str, user_content: str
) -> AnalysisResult:
    from openai import OpenAI

    kwargs: dict[str, str] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url.rstrip("/")
    client = OpenAI(**kwargs)

    response = client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        text_format=AnalysisResult,
    )
    if response.output_parsed is None:
        raise RuntimeError("OpenAI did not return a structured analysis.")
    return response.output_parsed


def analyze_with_gemini(*, api_key: str, model: str, user_content: str) -> AnalysisResult:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=AnalysisResult,
            temperature=0.1,
        ),
    )

    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, AnalysisResult):
        return parsed
    if parsed is not None:
        return AnalysisResult.model_validate(parsed)

    text = getattr(response, "text", None)
    if not text:
        raise RuntimeError("Gemini did not return structured JSON.")
    try:
        return AnalysisResult.model_validate_json(text)
    except Exception:
        return AnalysisResult.model_validate(json.loads(text))


def analyze_with_ollama(
    *, api_key: str | None, base_url: str, model: str, user_content: str
) -> AnalysisResult:
    from openai import OpenAI
    import openai
    import json

    client = OpenAI(
        api_key=api_key or "ollama",
        base_url=base_url.rstrip("/")
    )
    system_prompt = SYSTEM_PROMPT + "\n\nCRITICAL: You MUST return a JSON object that strictly adheres to the AnalysisResult schema."
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("Ollama did not return any content.")
        
        try:
            return AnalysisResult.model_validate_json(text)
        except Exception:
            clean_text = text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()
            return AnalysisResult.model_validate_json(clean_text)
    except openai.APIConnectionError as exc:
        raise RuntimeError(
            f"Không thể kết nối tới server Ollama tại '{base_url}'. "
            f"Hãy đảm bảo dịch vụ Ollama đang chạy (chạy lệnh `ollama serve` hoặc mở app Ollama) "
            f"và mô hình '{model}' đã được tải về thành công bằng lệnh `ollama run {model}`."
        ) from exc
    except Exception as exc:
        if "not found" in str(exc).lower() or "404" in str(exc).lower():
            raise RuntimeError(
                f"Mô hình '{model}' chưa được tải về máy của bạn. "
                f"Vui lòng chạy lệnh: `ollama pull {model}` trong Terminal của bạn. "
                f"Nếu mạng chậm hoặc cấu hình máy yếu, bạn có thể tải mô hình nhẹ hơn như: `ollama pull qwen2.5-coder:1.5b` (986MB) hoặc `ollama pull llama3.2:1b` (1.3GB)."
            ) from exc
        if "connection" in str(exc).lower():
            raise RuntimeError(
                f"Lỗi kết nối mạng hoặc server Ollama tại '{base_url}' không phản hồi. "
                f"Vui lòng kiểm tra lại dịch vụ Ollama."
            ) from exc
        raise exc


def analyze_with_ai(
    *,
    provider: Provider,
    api_key: str,
    base_url: str | None,
    reported_error: str,
    prepared_logs: str,
    deterministic_summary: str,
    model: str,
    similar_cases_context: str = "",
) -> AnalysisResult:
    user_content = build_user_content(
        reported_error=reported_error,
        prepared_logs=prepared_logs,
        deterministic_summary=deterministic_summary,
        similar_cases_context=similar_cases_context,
    )

    if provider == "openai":
        return analyze_with_openai(
            api_key=api_key,
            base_url=base_url,
            model=model,
            user_content=user_content,
        )
    if provider == "gemini":
        return analyze_with_gemini(
            api_key=api_key,
            model=model,
            user_content=user_content,
        )
    if provider == "ollama":
        return analyze_with_ollama(
            api_key=api_key,
            base_url=base_url or "http://localhost:11434/v1",
            model=model,
            user_content=user_content,
        )
    raise ValueError(f"Unsupported provider: {provider}")


RUNTIME_SYSTEM_PROMPT = """
You are PhoneBot Runtime Analyst, a senior performance and failure-analysis engineer
for automated mobile-phone test stations.

The server has already calculated transaction durations, stage shares, detailed retry/timeout
occurrences, initiators, target modules, repeated Z-axis actions, and long timestamp gaps.
Your job is only to explain the most plausible reason for slow execution and recommend checks.

STRICT RULES:
1. The slow policy is fixed: one process is slow only when total runtime is greater
   than 13 minutes, or the supplied custom threshold. PASS/FAIL does not change this.
2. Never change server-calculated durations, classification, timestamps, counts, retry initiator,
   target module or explicit/inferred labels.
3. A long gap after an event means the system spent time after that event; it does not
   prove the previous module physically caused the delay.
4. A Vision detection does not prove robot touch succeeded. A Z-axis command does not
   prove iOS accepted the touch.
5. Separate direct evidence from inference. Do not invent coordinates, force, thread
   states, hidden retries, hardware condition or missing events.
6. Rank causes conservatively as high, medium or low.
7. Write clear Vietnamese for technicians and preserve important English log text.
8. If logs are insufficient, explicitly say which SW, Vision, robot or device-service
   logs are missing.
9. Explain retry/timeout in the chain: what operation was active -> who initiated it ->
   which module/action was repeated or waited on -> runtime impact.
10. An inferred repeated action or suspected timeout is not a confirmed retry/timeout.
11. Return RuntimeAIInsight only.
""".strip()


def analyze_runtime_with_ollama(
    *, api_key: str | None, base_url: str, model: str, user_content: str
) -> "RuntimeAIInsight":
    from openai import OpenAI
    import openai
    from schemas import RuntimeAIInsight
    import json

    client = OpenAI(
        api_key=api_key or "ollama",
        base_url=base_url.rstrip("/")
    )
    system_prompt = RUNTIME_SYSTEM_PROMPT + "\n\nCRITICAL: You MUST return a JSON object that strictly adheres to the RuntimeAIInsight schema."
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        
        text = response.choices[0].message.content
        if not text:
            raise RuntimeError("Ollama did not return any content.")
        
        try:
            return RuntimeAIInsight.model_validate_json(text)
        except Exception:
            clean_text = text.strip()
            if clean_text.startswith("```json"):
                clean_text = clean_text[7:]
            if clean_text.endswith("```"):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()
            return RuntimeAIInsight.model_validate_json(clean_text)
    except openai.APIConnectionError as exc:
        raise RuntimeError(
            f"Không thể kết nối tới server Ollama tại '{base_url}'. "
            f"Hãy đảm bảo dịch vụ Ollama đang chạy (chạy lệnh `ollama serve` hoặc mở app Ollama) "
            f"và mô hình '{model}' đã được tải về thành công bằng lệnh `ollama run {model}`."
        ) from exc
    except Exception as exc:
        if "not found" in str(exc).lower() or "404" in str(exc).lower():
            raise RuntimeError(
                f"Mô hình '{model}' chưa được tải về máy của bạn. "
                f"Vui lòng chạy lệnh: `ollama pull {model}` trong Terminal của bạn. "
                f"Nếu mạng chậm hoặc cấu hình máy yếu, bạn có thể tải mô hình nhẹ hơn như: `ollama pull qwen2.5-coder:1.5b` (986MB) hoặc `ollama pull llama3.2:1b` (1.3GB)."
            ) from exc
        if "connection" in str(exc).lower():
            raise RuntimeError(
                f"Lỗi kết nối mạng hoặc server Ollama tại '{base_url}' không phản hồi. "
                f"Vui lòng kiểm tra lại dịch vụ Ollama."
            ) from exc
        raise exc


def analyze_runtime_with_ai(
    *,
    provider: Provider,
    api_key: str,
    base_url: str | None,
    model: str,
    runtime_analysis: dict,
    prepared_logs: str,
) -> "RuntimeAIInsight":
    from schemas import RuntimeAIInsight

    compact = {
        "classification": runtime_analysis.get("classification"),
        "threshold_minutes": runtime_analysis.get("threshold_minutes"),
        "total_duration_seconds": runtime_analysis.get("total_duration_seconds"),
        "total_duration_text": runtime_analysis.get("total_duration_text"),
        "over_threshold_seconds": runtime_analysis.get("over_threshold_seconds"),
        "stage_breakdown": runtime_analysis.get("stage_breakdown", [])[:8],
        "longest_gaps": runtime_analysis.get("longest_gaps", [])[:6],
        "processes": runtime_analysis.get("processes", [])[:8],
        "retry_timeout_groups": runtime_analysis.get("retry_timeout_groups", [])[:12],
        "retry_timeout_events": runtime_analysis.get("retry_timeout_events", [])[:30],
        "retry_by_module": runtime_analysis.get("retry_by_module", {}),
        "timeout_by_module": runtime_analysis.get("timeout_by_module", {}),
        "deterministic_root_causes": runtime_analysis.get("root_cause_candidates", [])[:4],
        "missing_logs_or_data": runtime_analysis.get("missing_logs_or_data", []),
    }
    user_content = f"""
SERVER-CALCULATED RUNTIME METRICS:
{json.dumps(compact, ensure_ascii=False, indent=2)}

SELECTED LOG EVIDENCE:
{prepared_logs}

Explain why this process was slow or, if it was within 13 minutes, identify the largest
time contributors without incorrectly calling it slow. Prioritize the longest gaps,
retry/timeout evidence, and the SW -> USB/device service -> Vision/OCR -> robot/Z-axis
chain. State clearly whether SW initiated the retry and whether the repeated target was a
service call, Vision detection, USB connection, functional test, or physical Z-axis action.
The calculated classification is authoritative.
""".strip()

    if provider == "openai":
        from openai import OpenAI

        kwargs: dict[str, str] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
        client = OpenAI(**kwargs)
        response = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": RUNTIME_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            text_format=RuntimeAIInsight,
        )
        if response.output_parsed is None:
            raise RuntimeError("OpenAI did not return structured runtime insight.")
        return response.output_parsed

    if provider == "gemini":
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=RUNTIME_SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=RuntimeAIInsight,
                temperature=0.1,
            ),
        )
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, RuntimeAIInsight):
            return parsed
        if parsed is not None:
            return RuntimeAIInsight.model_validate(parsed)
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Gemini did not return structured runtime insight.")
        try:
            return RuntimeAIInsight.model_validate_json(text)
        except Exception:
            return RuntimeAIInsight.model_validate(json.loads(text))

    if provider == "ollama":
        return analyze_runtime_with_ollama(
            api_key=api_key,
            base_url=base_url or "http://localhost:11434/v1",
            model=model,
            user_content=user_content,
        )

    raise ValueError(f"Unsupported provider: {provider}")


def generate_embedding(
    text: str,
    *,
    provider: Provider,
    api_key: str | None,
    base_url: str | None = None,
    model: str | None = None,
) -> list[float] | None:
    if not text or not text.strip():
        return None
    try:
        if provider == "openai":
            from openai import OpenAI
            client = OpenAI(api_key=api_key)
            emb_model = model or "text-embedding-3-small"
            resp = client.embeddings.create(input=[text], model=emb_model)
            return resp.data[0].embedding

        elif provider == "gemini":
            from google import genai
            client = genai.Client(api_key=api_key)
            emb_model = model or "text-embedding-004"
            resp = client.models.embed_content(model=emb_model, contents=text)
            if hasattr(resp, "embedding") and hasattr(resp.embedding, "values"):
                return resp.embedding.values
            elif isinstance(resp, dict) and "embedding" in resp:
                return resp["embedding"]["values"]
            return None

        elif provider == "ollama":
            from openai import OpenAI
            client = OpenAI(
                api_key=api_key or "ollama",
                base_url=(base_url or "http://localhost:11434/v1").rstrip("/")
            )
            emb_model = model or "nomic-embed-text"
            try:
                resp = client.embeddings.create(input=[text], model=emb_model)
                return resp.data[0].embedding
            except Exception as e:
                # Fallback to general qwen2.5-coder:7b or similar if embedding failed
                # but print warning
                print(f"Ollama embedding failed for model {emb_model}: {e}")
                return None
    except Exception as e:
        print(f"Failed to generate embedding with {provider}: {e}")
        return None


def chat_copilot(
    *,
    provider: Provider,
    api_key: str | None,
    base_url: str | None = None,
    model: str | None = None,
    system_prompt: str,
    messages: list[dict[str, str]],
) -> str:
    combined_messages = [{"role": "system", "content": system_prompt}]
    for msg in messages:
        combined_messages.append({"role": msg["role"], "content": msg["content"]})

    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            messages=combined_messages,
            temperature=0.4,
        )
        return resp.choices[0].message.content or ""

    elif provider == "gemini":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        # Convert standard openai roles to gemini compatible format or use raw list
        # For simplicity, we can format user and model messages in the client call,
        # or build custom contents list. Let's build contents.
        contents = []
        # If there's a system instruction, Gemini supports it via config
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.4,
        )
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])]
            ))
        resp = client.models.generate_content(
            model=model or "gemini-2.5-flash",
            contents=contents,
            config=config,
        )
        return resp.text or ""

    elif provider == "ollama":
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key or "ollama",
            base_url=(base_url or "http://localhost:11434/v1").rstrip("/")
        )
        resp = client.chat.completions.create(
            model=model or "qwen2.5-coder:7b",
            messages=combined_messages,
            temperature=0.4,
        )
        return resp.choices[0].message.content or ""

    raise ValueError(f"Unsupported provider: {provider}")

