"""
AI summarization for DevPulse (Gemini).

File name is kept as bedrock_ai.py and the function is still called
summarize_with_bedrock so that mcp_server.py and tools/__init__.py need no changes.

Why answers were cut off mid-sentence:
  Current Gemini models think by default, and thinking tokens count against
  maxOutputTokens. With a small limit the model spends it all thinking and the
  visible answer stops after a few words. Fixes applied here:
    1. Thinking is minimised for these short voice summaries. Gemini 3.x models use
       thinkingLevel, older 2.5 models use thinkingBudget. The right one is tried
       first, then the other, then none.
    2. maxOutputTokens is large enough to leave room even if thinking cannot be reduced.
    3. finishReason is checked, and a truncated answer is never shown to the user.
    4. API errors go to the server log only, never into the response text.
"""

import logging
import os

import httpx

from config import settings

logger = logging.getLogger("devpulse.ai")

DEFAULT_MODEL = "gemini-3.6-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = (
    "You are DevPulse, a developer assistant that speaks through Alexa+. "
    "Summarize developer workflow data (CI status, test results, pull requests, "
    "repository activity) into a short spoken response.\n"
    "Rules:\n"
    "- Maximum 80 words. Alexa reads it aloud.\n"
    "- Lead with the most important finding.\n"
    "- Plain English, complete sentences only.\n"
    "- No markdown, no bullet symbols, no asterisks, no headings.\n"
    "- If everything is healthy, say so briefly.\n"
    "- Only state facts present in the data. Never invent details."
)


def _api_key() -> str:
    return (
        getattr(settings, "gemini_api_key", "")
        or os.environ.get("GEMINI_API_KEY", "")
        or os.environ.get("GOOGLE_API_KEY", "")
    ).strip()


def _model() -> str:
    return (
        getattr(settings, "gemini_model", "")
        or os.environ.get("GEMINI_MODEL", "")
        or DEFAULT_MODEL
    ).strip()


def _thinking_options(model: str) -> list:
    """Thinking configs to try in order. None means send no thinkingConfig."""
    if "gemini-3" in model:
        return [{"thinkingLevel": "minimal"}, {"thinkingBudget": 0}, None]
    return [{"thinkingBudget": 0}, {"thinkingLevel": "minimal"}, None]


def _payload(prompt: str, thinking) -> dict:
    generation_config: dict = {
        "temperature": 0.3,
        # Generous on purpose: thinking tokens share this budget on some models.
        "maxOutputTokens": 2048,
    }
    if thinking is not None:
        generation_config["thinkingConfig"] = thinking
    return {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }


def _extract_text(data: dict) -> tuple[str, str]:
    """Return (text, finish_reason). Joins every text part, skips thought parts."""
    candidates = data.get("candidates") or []
    if not candidates:
        return "", "NO_CANDIDATES"
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
    return text, cand.get("finishReason", "")


async def summarize_with_bedrock(raw_data: str) -> str:
    """
    Summarize raw GitHub data into a short spoken answer.
    On any failure, returns a clean plain-data fallback. Errors are logged, not shown.
    """
    key = _api_key()
    if not key:
        logger.warning("No Gemini API key set (GEMINI_API_KEY). Using plain-data fallback.")
        return _fallback_summary(raw_data)

    model = _model()
    url = GEMINI_URL.format(model=model)
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    options = _thinking_options(model)

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = None
            for i, thinking in enumerate(options):
                resp = await client.post(url, headers=headers, json=_payload(raw_data, thinking))
                is_last = i == len(options) - 1
                if resp.status_code == 400 and "thinking" in resp.text.lower() and not is_last:
                    logger.info("Model rejected thinking config %s, trying next option.", thinking)
                    continue
                break

            if resp.status_code != 200:
                logger.error("Gemini API error %s (model=%s): %s", resp.status_code, model, resp.text[:300])
                return _fallback_summary(raw_data)

            text, finish = _extract_text(resp.json())

        if not text:
            logger.error("Gemini returned no text (model=%s, finishReason=%s)", model, finish)
            return _fallback_summary(raw_data)

        if finish == "MAX_TOKENS":
            logger.error("Gemini response truncated (MAX_TOKENS, model=%s). Using fallback.", model)
            return _fallback_summary(raw_data)

        return text

    except Exception:
        logger.exception("Gemini summarization failed")
        return _fallback_summary(raw_data)


def _fallback_summary(raw_data: str) -> str:
    """
    Used when the AI is unavailable. The incoming string is
    '<instruction line>\\n\\n<actual data>', so drop the instruction line and
    return the first few lines of real data.
    """
    data_part = raw_data.split("\n\n", 1)[1] if "\n\n" in raw_data else raw_data
    lines = [ln.strip() for ln in data_part.strip().split("\n") if ln.strip()]
    return " ".join(lines[:4])[:500]