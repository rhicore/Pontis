"""README 先读检查 — 未读 README 存在时，只允许继续读取 README。"""

from __future__ import annotations

from agent.guardrail_api import CallVerdict, Guardrail, GuardrailContext


class READMEReadCheck(Guardrail):
    """如果项目中存在 README 节点，未读完前禁止任何非 README 操作。"""

    def __init__(self):
        self.builder_name = "readme_check"

    def check(self, ctx: GuardrailContext) -> dict:
        result = {}
        active_projects = list(ctx.workspace.active_projects or [])
        readme_reads = {
            project
            for project in active_projects
            if self._has_read_readme(ctx, project)
        }
        unread_projects = {
            project
            for project in active_projects
            if self._has_readme_node(ctx, project) and project not in readme_reads
        }

        for i, (name, args) in enumerate(ctx.pending_calls):
            readme_project = self._readme_project(name, args)
            if readme_project and readme_project in unread_projects:
                readme_reads.add(readme_project)
                unread_projects.discard(readme_project)
                continue

            target_project = self._target_project(ctx, name, args)
            if target_project and (
                target_project not in active_projects
                or not self._has_readme_node(ctx, target_project)
                or target_project in readme_reads
            ):
                target_project = None

            if target_project and self._is_readme_read(name, args, target_project):
                readme_reads.add(target_project)
                unread_projects.discard(target_project)
                continue

            if not unread_projects:
                continue

            if readme_project and readme_project in active_projects:
                if readme_project in readme_reads or not self._has_readme_node(ctx, readme_project):
                    continue
                unread_projects.discard(readme_project)
                readme_reads.add(readme_project)
                continue

            pending_projects = sorted(unread_projects)
            pending = ", ".join(
                f'`meta({{"ref": "{project}::README", "property": ["detail"]}})`'
                for project in pending_projects
            )
            projects = ", ".join(f"`{project}`" for project in pending_projects)
            result[i] = CallVerdict(
                "block",
                f"仍有未读取的 README：{projects}。在读完这些 README 之前，不能执行其他操作；"
                f"当前只允许继续读取 README，例如 {pending}。"
            )

        return result

    def _target_project(
        self,
        ctx: GuardrailContext,
        tool_name: str,
        args: dict,
    ) -> str | None:
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

    def _readme_project(self, tool_name: str, args: dict) -> str | None:
        if tool_name != "meta":
            return None
        ref = str(args.get("ref", "")).strip()
        if ref == "README":
            return None
        if ref.endswith("::README"):
            return ref.split("::", 1)[0]
        return None

    def _is_readme_read(self, tool_name: str, args: dict, project: str) -> bool:
        if tool_name == "meta":
            ref = str(args.get("ref", ""))
            normalized = ref.strip()
            if normalized in {"README", f"{project}::README"}:
                return True

        return False
