def get_description() -> str:
    return """Exit plan mode and request approval for the SQL writing plan.

Use this tool only after you have completed the database exploration needed to write the
SQL: relevant tables, columns, joins, values, aggregation grain, output columns, and any
important ambiguity should already be checked. Do not call this tool before exploration.

The tool submits the plan to the user/evaluation agent. If approved, continue from the
approved plan and output the final SQL. If rejected, revise according to the feedback.
"""
