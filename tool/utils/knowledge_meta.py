"""Normalization helpers for bird knowledge entities."""

from __future__ import annotations


def is_bird_knowledge(project: str | None, labels: list[str] | None) -> bool:
    return project == "bird" and "knowledge" in set(labels or [])


def derive_knowledge_brief(meta: dict) -> str | None:
    for key in (
        "brief",
        "mistake_summary",
        "decision_summary",
        "transfer_hint",
        "why_this_case_matters",
        "question",
    ):
        value = meta.get(key)
        if value is not None and str(value).strip() not in ("", "-", "..."):
            return str(value).strip().splitlines()[0][:120]
    detail = meta.get("detail")
    if detail is not None and str(detail).strip() not in ("", "-", "..."):
        for line in str(detail).splitlines():
            text = line.strip()
            if not text:
                continue
            if text.startswith("#"):
                continue
            if text.startswith("- question:"):
                return text.split(":", 1)[1].strip()[:120]
            if text.startswith("- question_id:") or text.startswith("- db_id:") or text.startswith("- difficulty:"):
                continue
            if text.lower().startswith("brief:"):
                text = text.split(":", 1)[1].strip()
            if text.startswith("- "):
                continue
            if text:
                return text[:120]
    return None


def derive_knowledge_detail(meta: dict, labels: list[str] | None) -> str | None:
    detail = meta.get("detail")
    if detail is not None and str(detail).strip() not in ("", "-", "..."):
        return str(detail).strip()

    label_set = set(labels or [])
    lines: list[str] = []
    if "example" in label_set:
        ordered_fields = [
            ("brief", "brief"),
            ("question", "question"),
            ("evidence", "evidence"),
            ("schema_background", "schema_background"),
            ("bird_bias", "bird_bias"),
            ("why_this_case_matters", "why_this_case_matters"),
            ("transfer_hint", "transfer_hint"),
            ("predicted_sql", "predicted_sql"),
            ("golden_sql", "golden_sql"),
            ("error_type", "error_type"),
            ("mistake_summary", "mistake_summary"),
            ("wrong_assumption", "wrong_assumption"),
            ("fix_hint", "fix_hint"),
            ("decision_summary", "decision_summary"),
            ("verification_note", "verification_note"),
            ("rejected_alternatives", "rejected_alternatives"),
        ]
    else:
        ordered_fields = [
            ("brief", "brief"),
            ("transfer_hint", "transfer_hint"),
            ("why_this_case_matters", "why_this_case_matters"),
            ("mistake_summary", "mistake_summary"),
            ("decision_summary", "decision_summary"),
            ("wrong_assumption", "wrong_assumption"),
            ("fix_hint", "fix_hint"),
            ("verification_note", "verification_note"),
        ]

    for key, title in ordered_fields:
        value = meta.get(key)
        if value is None or str(value).strip() in ("", "-", "..."):
            continue
        lines.append(f"{title}: {value}")

    if not lines and str(meta.get("brief", "")).strip():
        lines.append(f"brief: {meta['brief']}")

    if not lines:
        return None
    return "\n\n".join(lines)


def normalize_knowledge_meta(project: str | None, labels: list[str] | None, meta: dict | None) -> dict:
    data = dict(meta or {})
    if not is_bird_knowledge(project, labels):
        return data

    if str(data.get("brief", "")).strip() in ("", "-", "..."):
        brief = derive_knowledge_brief(data)
        if brief:
            data["brief"] = brief

    if str(data.get("detail", "")).strip() in ("", "-", "..."):
        detail = derive_knowledge_detail(data, labels)
        if detail:
            data["detail"] = detail

    return data
