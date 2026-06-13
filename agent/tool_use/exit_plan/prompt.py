def get_description() -> str:
    return """Exit plan mode and request approval for a plan.

Use this tool only after you have enough information to propose a concrete next-step
plan. The plan can be a SQL candidate, an implementation plan, an analysis plan, or any
other task-specific plan requested by the active workflow.

The tool submits the plan to the user or supervising agent for approval. If rejected,
revise according to the feedback.
"""
