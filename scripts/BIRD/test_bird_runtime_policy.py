"""BIRD runtime policy regressions."""

import ast
from pathlib import Path

from scripts.BIRD import bird_runner
from agent.prompt._ontology import get_database_ontology_prompt, get_ontology_prompt
from agent.prompt._tool import get_tool_prompt
from extractor.semantic_embedding import EMBEDDING_TEXT_FIELDS, _embedding_text
from tool.utils.entity_search import BM25_TEXT_FIELDS


def test_bird_agent_has_tool_control_but_no_text_sql_guards():
    spec = bird_runner._build_bird_agent_spec("demo")
    guard_names = {type(guard).__name__ for guard in spec.guardrails}

    assert guard_names == {"RoundLimit", "ToolUseCheck", "ExplorationCheck"}


def test_bird_runner_has_no_hard_guard_dependency():
    source = Path(bird_runner.__file__).read_text(encoding="utf-8")

    assert "hard_guard" not in source
    assert "bird_sql_output_guard" not in source


def test_benchmark_defaults_to_required_parallelism():
    path = Path(__file__).with_name("run_bird_benchmark.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    workers_default = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not any(isinstance(arg, ast.Constant) and arg.value == "--workers" for arg in node.args):
            continue
        for keyword in node.keywords:
            if keyword.arg == "default" and isinstance(keyword.value, ast.Constant):
                workers_default = keyword.value.value

    assert workers_default == 24


def test_bird_prompts_only_describe_the_compact_database_graph():
    spec = bird_runner._build_bird_agent_spec("demo")
    ontology = get_database_ontology_prompt()
    tools = get_tool_prompt(spec)

    assert "db\n├── table / view" in ontology
    assert "文件系统" not in ontology
    assert "CSV" not in ontology
    assert "table_group" not in ontology
    assert "schema_landscape" not in tools
    assert "CSV" not in tools


def test_large_database_ontology_extends_the_same_database_core():
    ontology = get_ontology_prompt()

    assert "唯一根节点" in ontology
    assert "table_group" in ontology
    assert "文件系统" not in ontology


def test_hints_are_meta_only_and_do_not_enter_retrieval_text():
    assert "hints" not in EMBEDDING_TEXT_FIELDS
    assert "hints" not in BM25_TEXT_FIELDS
    text = _embedding_text({"name": "schools", "detail": "school rows", "hints": ["secret hint"]})

    assert "school rows" in text
    assert "secret hint" not in text
