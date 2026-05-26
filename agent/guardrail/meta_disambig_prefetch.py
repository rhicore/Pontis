"""Meta-triggered context fork.

When the main agent reads metadata for an entity with adjacent disambig/hint
nodes or local hints, start a non-blocking forked agent. The fork inherits the
main agent context and tools, investigates the relevant context, and reports
back as an appended message.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
from typing import Dict, List, Tuple

from agent.fork import is_fork_context, run_forked_agent
from agent.guardrail_api import Guardrail, GuardrailContext, PostToolAction
from tool.utils.resolve import (
    canonical_ref,
    resolve_entity_selector,
    selector_match_pattern,
    selector_params,
)

logger = logging.getLogger(__name__)


class MetaDisambigPrefetch(Guardrail):
    """Non-blocking fork for meta-adjacent disambiguation and hint nodes."""

    def __init__(self, *, max_pending: int = 4, max_rounds: int = 5):
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_pending,
            thread_name_prefix="pontis-disambig-fork",
        )
        self._max_rounds = max_rounds
        self._pending: Dict[Tuple[str, Tuple[str, ...]], concurrent.futures.Future] = {}
        self._completed_keys: set[Tuple[str, Tuple[str, ...]]] = set()

    def post_tool(self, ctx: GuardrailContext,
                  call_index: int, name: str, args: dict,
                  result: str):
        if name != "meta":
            return None
        if ctx.agent is None or is_fork_context(ctx.messages):
            return None

        ref = args.get("ref") or args.get("path")
        if not ref:
            return None

        entity_ref, context_refs, local_hints = self._adjacent_context(ctx.workspace, ref)
        if not entity_ref or (not context_refs and not local_hints):
            return None

        key = (entity_ref, tuple(sorted(context_refs + local_hints)))
        if key in self._pending or key in self._completed_keys:
            return None

        directive = self._build_directive(entity_ref, context_refs, local_hints)
        label = f"meta-context:{entity_ref}"
        self._pending[key] = self._executor.submit(
            run_forked_agent,
            ctx.agent,
            directive,
            max_rounds=self._max_rounds,
            label=label,
        )
        logger.info(
            "Started meta context fork for %s (%d refs, %d local hints)",
            entity_ref,
            len(context_refs),
            len(local_hints),
        )
        return PostToolAction(trace_messages=[
            (
                "Started meta context fork "
                f"entity={entity_ref} context_refs={len(context_refs)} "
                f"local_hints={len(local_hints)}"
            )
        ])

    def drain_ready(self, ctx: GuardrailContext) -> List[str]:
        ready: List[str] = []
        for key, future in list(self._pending.items()):
            if not future.done():
                continue
            self._pending.pop(key, None)
            self._completed_keys.add(key)
            try:
                fork_result = future.result()
            except Exception as exc:  # noqa: BLE001 - fork must not break main agent
                logger.warning("Meta disambig fork failed: %s", exc)
                continue
            content = (fork_result.result or "").strip()
            if not content:
                continue
            ready.append(
                "<meta-context-sidechain>\n"
                + content
                + "\n</meta-context-sidechain>\n\n"
                "Use this fork report only if it is relevant to the current SQL decision. "
                "If it contradicts direct database evidence, verify with tools before relying on it."
            )
        return ready

    def _adjacent_context(self, workspace, ref: str) -> tuple[str, List[str], List[str]]:
        if workspace is None:
            return "", [], []
        selector, err = resolve_entity_selector(workspace, ref)
        if err or not selector:
            return "", [], []

        project = selector.get("project")
        match = selector_match_pattern(selector, var="n")
        rows = workspace.cypher(
            f"""
            MATCH {match}
            OPTIONAL MATCH (n)--(d)
            WHERE 'disambig' IN coalesce(d.labels, []) OR 'hint' IN coalesce(d.labels, [])
            RETURN n, d
            """,
            params=selector_params(selector),
            project=project,
        )
        context_refs = []
        local_hints: List[str] = []
        for row in rows:
            node_n = row.get("n") or {}
            if not local_hints:
                local_hints = self._normalize_hints(node_n.get("hints"))
            node = row.get("d") or {}
            labels = set(node.get("labels", []))
            if not ({"disambig", "hint"} & labels):
                continue
            dref = canonical_ref(node, node.get("name", ""))
            if dref and dref not in context_refs:
                context_refs.append(dref)

        entity_ref = canonical_ref(selector, ref)
        return entity_ref, context_refs, local_hints

    @staticmethod
    def _normalize_hints(value) -> List[str]:
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
                    return MetaDisambigPrefetch._normalize_hints(parsed)
            return [line.strip() for line in text.splitlines() if line.strip()]
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        return [text] if text else []

    def _build_directive(self, entity_ref: str,
                         context_refs: List[str],
                         local_hints: List[str]) -> str:
        refs = "\n".join(f"- {ref}" for ref in context_refs) or "- none"
        hints = "\n".join(f"- {hint}" for hint in local_hints) or "- none"
        return f"""\
The main agent just read metadata for this entity:
{entity_ref}

That entity has adjacent context entities:
{refs}

That entity also has local hints:
{hints}

Investigate whether these disambiguations or hints affect the current user task
and the main agent's SQL decisions. Use the inherited full conversation context.
Use tools directly if you need to read hint/disambiguation metadata, neighboring
schema, or small database samples.

Do not solve the full user task. Do not produce final SQL. Report only the
context findings and checks the main agent should consider before final SQL.
"""
