"""Detection of instruction-like text inside untrusted documents (resumes, job descriptions).

Documents are never executed as instructions anywhere in RoleMatch: the rule-based pipeline does not interpret
them, and the optional LLM path wraps them as quoted data and validates outputs against the source text.
This module only *flags* suspicious passages so the UI can show them to the user.
"""

from __future__ import annotations

import re

_PATTERNS = [
    r"ignore (?:all |any )?(?:the )?(?:previous|prior|above|earlier) (?:instructions|prompts|rules)",
    r"disregard (?:all |any )?(?:the )?(?:previous|prior|above) (?:instructions|text)",
    r"(?:you are|act as) (?:now )?(?:an? )?(?:ai|assistant|language model|chatgpt|claude|gpt)",
    r"system prompt",
    r"(?:rank|rate|score|mark) (?:this|me|the) (?:candidate|applicant|resume|job)s? (?:as )?(?:first|highest|top|100|a perfect|an? (?:excellent|perfect))",
    r"(?:this|the) candidate (?:is|should be) (?:a )?perfect (?:match|fit)",
    r"perfect (?:match|fit) for (?:every|all|any) (?:job|role|position)s?",
    r"do not (?:mention|reveal|report) (?:any )?(?:gaps|missing)",
    r"assistant\s*:",
    r"<\s*/?\s*(?:system|instructions?|prompt)\s*>",
]
_RX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)


def find_instruction_like_text(text: str, limit: int = 10) -> list[dict]:
    out = []
    for m in _RX.finditer(text):
        out.append({"start": m.start(), "end": m.end(), "text": m.group(0)})
        if len(out) >= limit:
            break
    return out


def strip_instruction_like_sentences(text: str) -> str:
    """Remove whole sentences that contain instruction-like text, so they cannot influence extraction or embeddings."""
    flags = find_instruction_like_text(text, limit=50)
    if not flags:
        return text
    cut: list[tuple[int, int]] = []
    for f in flags:
        a = max(text.rfind(". ", 0, f["start"]) + 1, text.rfind("\n", 0, f["start"]) + 1, 0)
        ends = [i for i in (text.find(". ", f["end"]), text.find("\n", f["end"])) if i != -1]
        b = (min(ends) + 1) if ends else len(text)
        cut.append((a, b))
    out, pos = [], 0
    for a, b in sorted(cut):
        if a > pos:
            out.append(text[pos:a])
        pos = max(pos, b)
    out.append(text[pos:])
    return "".join(out)
