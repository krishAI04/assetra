import json
import re
import urllib.error
import urllib.request

from django.conf import settings


def get_document_text(document):
    """
    Try to read text from the uploaded PDF.
    If text extraction is not available, the analyzer still works using document metadata.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        with document.file.open("rb") as pdf_file:
            reader = PdfReader(pdf_file)
            page_text = []
            for page in reader.pages[:8]:
                page_text.append(page.extract_text() or "")
            return "\n".join(page_text).strip()
    except Exception:
        return ""


def build_document_context(document):
    """Create simple text about the document for mock mode and live AI mode."""
    client_name = document.client.full_name if document.client else "Not linked"
    asset_title = document.asset.title if document.asset else "Not linked"
    asset_category = document.asset.get_category_display() if document.asset else "Not linked"
    asset_value = document.asset.estimated_value if document.asset else "Not linked"
    extracted_text = get_document_text(document)

    return {
        "title": document.title,
        "client_name": client_name,
        "asset_title": asset_title,
        "asset_category": asset_category,
        "asset_value": str(asset_value),
        "uploaded_at": document.created_at.strftime("%d %b %Y"),
        "text": extracted_text[:12000],
    }


def make_mock_analysis(document):
    """
    Return a realistic demo response without calling any paid or external AI service.
    This protects the public portfolio demo from unlimited API usage.
    """
    context = build_document_context(document)
    text = context["text"]
    dates = re.findall(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b", text)

    parties = []
    if context["client_name"] != "Not linked":
        parties.append(context["client_name"])
    if context["asset_title"] != "Not linked":
        parties.append(context["asset_title"])

    return {
        "summary": (
            f"This document appears to relate to {context['client_name']} and "
            f"{context['asset_title']}. It should be reviewed as supporting material "
            "before any estate distribution or approval decision is finalized."
        ),
        "important_parties": parties or ["Client or asset details not clearly detected"],
        "important_dates": dates[:5] or [context["uploaded_at"]],
        "asset_details": {
            "asset": context["asset_title"],
            "category": context["asset_category"],
            "estimated_value": context["asset_value"],
        },
        "risk_points": [
            "Confirm that the uploaded document is complete and readable.",
            "Verify ownership details against the client and asset records.",
            "Check whether signatures, dates, and supporting proof are present.",
        ],
        "suggested_next_action": (
            "Ask a lawyer or senior lawyer to verify the document before approval."
        ),
        "raw_response": {"source": "mock_demo"},
    }


def make_gemini_prompt(context):
    """Keep the live Gemini request focused on one clear legal-document workflow."""
    return f"""
You are helping a law firm review an estate-management document.
Return ONLY valid JSON with these keys:
summary, important_parties, important_dates, asset_details, risk_points, suggested_next_action.

Document title: {context["title"]}
Linked client: {context["client_name"]}
Linked asset: {context["asset_title"]}
Asset category: {context["asset_category"]}
Asset value: {context["asset_value"]}
Uploaded at: {context["uploaded_at"]}

Document text:
{context["text"] or "No readable text was extracted. Use the metadata above."}
"""


def clean_gemini_json(response_text):
    """Gemini can wrap JSON in markdown, so remove that before parsing."""
    cleaned = response_text.strip()
    cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(cleaned)


def make_live_gemini_analysis(document):
    """Call Gemini through its free/prototype API when live mode is enabled."""
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    model = getattr(settings, "GEMINI_MODEL", "gemini-1.5-flash")
    context = build_document_context(document)
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:"
        f"generateContent?key={api_key}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": make_gemini_prompt(context)}
                ]
            }
        ],
        "generationConfig": {"temperature": 0.2},
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        mock = make_mock_analysis(document)
        mock["raw_response"] = {"source": "mock_fallback", "error": str(error)}
        return "mock", mock

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    try:
        result = clean_gemini_json(text)
    except (KeyError, IndexError, json.JSONDecodeError):
        result = make_mock_analysis(document)
        result["raw_response"] = {"source": "mock_fallback", "gemini_response": data}
        return "mock", result

    result["raw_response"] = data
    return "live", result


def analyze_document(document, use_live_ai=False):
    """
    Main function used by the view.
    It returns both the mode used and the analysis data.
    """
    if use_live_ai and getattr(settings, "GEMINI_API_KEY", ""):
        return make_live_gemini_analysis(document)
    return "mock", make_mock_analysis(document)
