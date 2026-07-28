"""
Photo path: qualitative CNC-machinability pre-screen from a single image.

A 2D photo cannot prove undercuts, so this is explicitly a rough pre-check that
points the user to upload the STL for a definitive geometric answer. Uses OpenAI
vision (gpt-4o) via OPENAI_API_KEY; kept small and swappable.
"""
from __future__ import annotations

import base64
import json
import os

_SYSTEM = (
    "You are a senior CNC/CAM engineer assessing whether a physical part or 3D "
    "model shown in a single photo could be cut on a 3-axis or 4-axis CNC router, "
    "or whether it likely needs 5-axis machining because of undercuts (features a "
    "straight-down or single-rotary tool cannot reach). Reason about overhangs, "
    "re-entrant/undercut features, deep narrow pockets, internal cavities, and "
    "whether the form looks like a surface of revolution (rotary-friendly). "
    "A single photo is NOT definitive geometry."
)

_INSTRUCTION = (
    "Return ONLY strict JSON with keys: "
    '"verdict" (one of "3-axis", "4-axis", "5-axis", "uncertain"), '
    '"confidence" (0.0-1.0), '
    '"reasoning" (2-4 short sentences citing the visible features), '
    '"suspected_undercuts" (array of short strings, may be empty). '
    "Do not wrap the JSON in markdown."
)

_UNAVAILABLE = {
    "verdict": "uncertain",
    "confidence": 0.0,
    "reasoning": "Photo analysis is unavailable because OPENAI_API_KEY is not "
                 "configured on the server. Upload an STL for a definitive check.",
    "suspected_undercuts": [],
    "available": False,
}


def assess_image(image_bytes: bytes, mime_type: str = "image/png", model: str = "gpt-4o") -> dict:
    """Return a qualitative machinability assessment dict for one image."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return dict(_UNAVAILABLE)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        b64 = base64.b64encode(image_bytes).decode("ascii")
        resp = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": [
                    {"type": "text", "text": _INSTRUCTION},
                    {"type": "image_url",
                     "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ]},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
    except Exception as exc:  # network / parse / auth
        return {
            "verdict": "uncertain",
            "confidence": 0.0,
            "reasoning": f"Photo analysis failed: {type(exc).__name__}. "
                         "Upload an STL for a definitive check.",
            "suspected_undercuts": [],
            "available": False,
        }

    # normalize / clamp
    verdict = str(data.get("verdict", "uncertain")).lower()
    if verdict not in ("3-axis", "4-axis", "5-axis", "uncertain"):
        verdict = "uncertain"
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "verdict": verdict,
        "confidence": round(confidence, 2),
        "reasoning": str(data.get("reasoning", "")).strip(),
        "suspected_undercuts": [str(x) for x in (data.get("suspected_undercuts") or [])][:8],
        "available": True,
        "caveat": "A single photo cannot prove undercuts. Upload the STL model "
                  "for a definitive, geometry-based 3/4/5-axis verdict.",
    }
