"""README 先读检查 — 若项目存在 README 节点，则首次有意义访问前必须先读。"""

from __future__ import annotations

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext


class READMEReadCheck(Guardrail):
    """如果项目中存在 README 节点，要求先读取再访问该项目。"""

    def __init__(self):
        self.builder_name = "readme_check"

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}
        readme_reads = {
            project
            for project in (ctx.workspace.active_projects or [])
            if self._has_read_readme(ctx, project)
        }

        for i, (name, args) in enumerate(ctx.pending_calls):
            target_project = self._target_project(ctx, name, args, readme_reads)
            if not target_project:
                continue

            if not self._has_readme_node(ctx, target_project):
                continue

            if target_project in readme_reads:
                continue

            if self._is_readme_read(name, args, target_project):
                readme_reads.add(target_project)
                continue

            result[i] = CallVerdict(
                "block",
                f"项目 `{target_project}` 存在 README 节点。首次访问该项目内容前，请先读取它，例如 "
                f'`meta({{"ref": "{target_project}::README", "property": ["detail"]}})`。'
            )

        return result

    def _target_project(
        self,
        ctx: GuardrailContext,
        tool_name: str,
        args: dict,
        readme_reads: set[str],
    ) -> str | None:
        bird_first = self._needs_bird_readme_first(ctx, readme_reads)
        if bird_first and not self._is_readme_read(tool_name, args, "bird"):
            return "bird"

        if tool_name in {"glob", "meta", "search", "delete"}:
            ref = str(args.get("ref", ""))
            if "::" in ref:
                return ref.split("::", 1)[0]
            active = ctx.workspace.active_projects
            return active[0] if active else None

        if tool_name in {"create_entity", "update_meta"}:
            ref = str(args.get("ref", ""))
            if "::" in ref:
                return ref.split("::", 1)[0]
            active = ctx.workspace.active_projects
            return active[0] if active else None

        if tool_name in {"query", "bash", "grep"}:
            active = ctx.workspace.active_projects
            return active[0] if active else None

        return None

    def _needs_bird_readme_first(self, ctx: GuardrailContext, readme_reads: set[str]) -> bool:
        active = set(ctx.workspace.active_projects or [])
        if "bird" not in active:
            return False
        if not self._has_readme_node(ctx, "bird"):
            return False
        if "bird" in readme_reads:
            return False
        return True

    def _has_readme_node(self, ctx: GuardrailContext, project: str) -> bool:
        rows = ctx.workspace.cypher(
            "MATCH (n {name: $name}) RETURN n",
            params={"name": "README"},
            project=project,
        )
        return bool(rows)

    def _has_read_readme(self, ctx: GuardrailContext, project: str) -> bool:
        for name, args, _ in ctx.tool_history:
            if self._is_readme_read(name, args, project):
                return True
        return False

    def _is_readme_read(self, tool_name: str, args: dict, project: str) -> bool:
        if tool_name == "meta":
            ref = str(args.get("ref", ""))
            normalized = ref.strip()
            if normalized in {"README", f"{project}::README"}:
                return True

        return False
