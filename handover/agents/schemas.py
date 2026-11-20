"""JSON schemas for structured outputs. Kept union-free (empty string / -1 / 0
mean "not stated") so they compile cleanly as constrained-decoding grammars."""

from __future__ import annotations

ACTIONS = ["renew", "non_renew", "terminate", "amend", "convert_to_permanent", "confirm_probation", "review",
           "no_action"]
DOC_KINDS = ["employment_agreement", "amendment", "offer_letter", "consultancy_agreement", "separation_document",
             "policy", "board_minutes", "nda", "lease", "invoice", "other"]


def _s(desc: str) -> dict:
    return {"type": "string", "description": desc}


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(props)}


ANALYSIS_SCHEMA = _obj({
    "document_kind": {"type": "string", "enum": DOC_KINDS},
    "is_employment_related": {"type": "boolean",
                              "description": "True if this document creates, changes or ends the engagement of a "
                                             "specific worker (employee or contractor)."},
    "language": _s("ISO 639-1 code of the document's main language"),
    "language_name": _s("English name of that language"),
    "is_scanned": {"type": "boolean", "description": "True if pages are images (scan or photo) with no text layer."},
    "title": _s("Document title; if not in English add the English translation in parentheses"),
    "summary": _s("2-3 sentence English summary: who, role, term, renewal, notice, money, anything unusual"),
    "employee_name": _s("Worker's full name, or empty string"),
    "employee_email": _s("Worker's email address if stated, else empty string"),
    "job_title": _s("Job title in English, or empty string"),
    "department": _s("Department or team, or empty string"),
    "employer_entity": _s("Contracting company entity, or empty string"),
    "country": _s("ISO 3166-1 alpha-2 country of work, or empty string"),
    "work_location": _s("City/office, or empty string"),
    "governing_law": _s("Governing law / jurisdiction, or empty string"),
    "contract_type": {"type": "string", "enum": ["fixed_term", "indefinite", "at_will", "contractor", "not_applicable"]},
    "start_date": _s("YYYY-MM-DD or empty string"),
    "end_date": _s("End of the current term as stated in THIS document (YYYY-MM-DD), or empty string"),
    "auto_renewal": {"type": "boolean"},
    "renewal_terms": _s("How renewal/extension works, or empty string"),
    "notice_period_days": {"type": "integer",
                           "description": "Days of notice the employer must give to end or not renew; -1 if none "
                                          "or not stated"},
    "notice_terms": _s("Notice rules for both sides incl. formalities (written form etc.), or empty string"),
    "notice_deadline": _s("Last calendar day (YYYY-MM-DD) for the employer to give notice of non-renewal or "
                          "termination at term end, computed from this document; empty if not applicable"),
    "probation_end_date": _s("YYYY-MM-DD or empty string"),
    "compensation_amount": {"type": "number", "description": "Base pay amount; 0 if not stated"},
    "compensation_currency": _s("ISO 4217 code, or empty string"),
    "compensation_period": {"type": "string", "enum": ["year", "month", "hour", "other", "unknown"]},
    "compensation_text": _s("Pay as a short human-readable string, or empty string"),
    "non_compete": _s("Post-employment non-compete (scope, duration, compensation), or empty string if none"),
    "termination_terms": _s("Termination rights and severance, or empty string"),
    "amends_document": _s("If this document amends or supplements another, identify that document; else empty"),
    "key_dates": {"type": "array", "items": _obj({"date": _s("YYYY-MM-DD"), "label": _s("What happens")})},
    "key_clauses": {"type": "array",
                    "description": "The 2-5 clauses that matter most for renewal/termination decisions",
                    "items": _obj({"heading": _s("Clause number/heading as in the document"),
                                   "original_text": _s("Short verbatim excerpt in the original language"),
                                   "english_text": _s("English translation (same text if already English)"),
                                   "page": {"type": "integer", "description": "1-based page, 0 if unknown"}})},
    "risk_flags": {"type": "array",
                   "items": _obj({"severity": {"type": "string", "enum": ["high", "medium", "low"]},
                                  "title": _s("Short headline"),
                                  "detail": _s("Why it matters, citing the clause or law")})},
    "recommended_action": {"type": "string", "enum": ACTIONS},
    "recommendation_rationale": _s("One or two sentences"),
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "signed_status": {"type": "string", "enum": ["fully_signed", "partially_signed", "unsigned", "unclear"]},
})

DRAFT_TYPES = ["renewal_addendum", "non_renewal_notice", "termination_letter", "contract_amendment",
               "conversion_addendum", "probation_confirmation", "other"]

DRAFT_SCHEMA = _obj({
    "doc_type": {"type": "string", "enum": DRAFT_TYPES},
    "title": _s("Document title in the drafting language"),
    "language": _s("ISO 639-1 code of the language the document is drafted in"),
    "body_markdown": _s("The complete document in Markdown: heading, parties, recitals/references to the "
                        "original agreement, operative clauses, and signature blocks. Use [brackets] for any "
                        "fact you do not have."),
    "english_summary": _s("3-5 sentences in English for counsel: what this document does and why"),
    "compliance_notes": {"type": "array", "items": _s("A formality, legal requirement or risk counsel must "
                                                      "know before sending (cite the law)")},
    "delivery_method": {"type": "string", "enum": ["email", "email_and_wet_ink", "wet_ink_only", "e_signature"]},
    "recipients_to": {"type": "array", "items": _s("Email address")},
    "recipients_cc": {"type": "array", "items": _s("Email address")},
    "cover_email_subject": _s("Subject line"),
    "cover_email_body": _s("Short, warm, professional cover email in the worker's language"),
    "signature_required_from": {"type": "array", "items": _s("Who must sign")},
    "sign_by_date": _s("YYYY-MM-DD by which signatures/delivery must be complete, or empty string"),
})
