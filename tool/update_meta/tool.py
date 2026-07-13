"""Update meta tool — 更新实体元数据。"""

import json

from tool.utils.resolve import resolve_entity_selector, selector_match_pattern, selector_params

_ALLOWED_FIELDS = {"brief", "detail", "hints", "disambig_note", "review_status"}
_REVIEW_STATUSES = {"pending_review", "accepted", "needs_split", "rejected"}


def _normalize_hints(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if isinstance(parsed, list):
                return _normalize_hints(parsed)
        lines = [line.strip() for line in text.splitlines()]
        return [line for line in lines if line]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _split_project_ref(ref: str) -> tuple[str | None, str]:
    if "::" not in ref:
        return None, ref
    project, local_ref = ref.split("::", 1)
    return project, local_ref


def update_meta_command(workspace, ref: str, fields: dict) -> str:
    """通过 Cypher SET 更新实体元数据。

    ref 支持两种模式：
      - 精确名称 → 直接匹配
      - ref 模式 → 必须匹配唯一实体
    """
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except json.JSONDecodeError:
            return "错误: fields 必须是对象；收到的是无法解析为 JSON 对象的字符串"
    if not isinstance(fields, dict):
        return (
            "错误: fields 必须是对象，只允许包含 "
            "brief/detail/hints/disambig_note/review_status"
        )

    invalid = set(fields.keys()) - _ALLOWED_FIELDS
    if invalid:
        return f"错误: 不允许修改 {', '.join(sorted(invalid))}。只允许修改: {', '.join(sorted(_ALLOWED_FIELDS))}"

    if "review_status" in fields and fields["review_status"] not in _REVIEW_STATUSES:
        return (
            "错误: review_status 必须是以下值之一: "
            + ", ".join(sorted(_REVIEW_STATUSES))
        )

    safe_fields = {k: v for k, v in fields.items() if not k.startswith("_")}
    if not safe_fields:
        return "错误: 没有有效的字段可更新"

    selector, err = resolve_entity_selector(workspace, ref)
    if err:
        return f"Error: {err}"

    project, local_ref = _split_project_ref(ref)
    if selector.get("project"):
        project = selector["project"]
    match = selector_match_pattern(selector, "n")
    if "hints" in safe_fields:
        safe_fields["hints"] = _normalize_hints(safe_fields.get("hints"))

    rows = workspace.cypher(
        f"MATCH {match} SET n += $props RETURN n",
        params=selector_params(selector, {"props": safe_fields}),
        project=project,
    )
    if not rows:
        return f"Error: failed to update entity: {ref}"

    has_wildcards = any(c in local_ref for c in "*?[]")
    target_ref = selector.get("ref") or selector.get("path") or local_ref
    display_ref = ref if "::" in ref else (target_ref if has_wildcards else local_ref)
    written = [f"OK {display_ref}:"]
    for k, v in safe_fields.items():
        written.append(f"{k}: {v}")
    return "\n".join(written)
