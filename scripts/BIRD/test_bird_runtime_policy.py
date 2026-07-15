"""BIRD runtime policy regressions."""

import ast
from pathlib import Path

from scripts.BIRD import bird_runner
from agent.prompt._ontology import get_database_ontology_prompt, get_ontology_prompt
from agent.prompt._tool import get_tool_prompt
from extractor.semantic_embedding import EMBEDDING_TEXT_FIELDS, _embedding_text
from explorer.description_audit import PROMPT as DESCRIPTION_AUDIT_PROMPT
from explorer.column_domain_review import PROMPT as COLUMN_DOMAIN_REVIEW_PROMPT
from explorer.disambiguate import PROMPT as DISAMBIGUATE_PROMPT
from explorer.schema_prepare import PROMPT as SCHEMA_PREPARE_PROMPT
from explorer.utils.description_contract import DESCRIPTION_CONTRACT
from tool.utils.entity_search import BM25_TEXT_FIELDS
from utils.embedding import load_embedding_config


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


def test_main_agent_uses_reviewed_relations_instead_of_machine_domains():
    spec = bird_runner._build_bird_agent_spec("demo")
    prompts = "\n".join([
        get_database_ontology_prompt(),
        get_tool_prompt(spec),
    ])

    assert "column_domain" not in prompts
    assert "fk" in prompts
    assert "rel" in prompts
    assert "disambig" in prompts


def test_vector_retrieval_has_a_noise_floor():
    config = load_embedding_config()

    assert 0.0 < config.min_similarity < 1.0
    assert config.min_similarity >= 0.65


def test_schema_descriptions_share_one_glossary_contract_without_querying_rows():
    assert DESCRIPTION_CONTRACT in SCHEMA_PREPARE_PROMPT
    assert DESCRIPTION_CONTRACT in DESCRIPTION_AUDIT_PROMPT
    assert "跨数据刷新仍成立" in DESCRIPTION_CONTRACT

    source = Path(__file__).parents[2].joinpath("explorer/schema_prepare.py").read_text(encoding="utf-8")
    assert 'tools=["find", "meta", "update_meta"]' in source
    assert '"query"' not in source


def test_reviewed_relation_entities_have_non_overlapping_ownership():
    assert "`rel` 保存 schema 未声明" in COLUMN_DOMAIN_REVIEW_PROMPT
    assert "一个稳定关系由 `fk` 或 `rel` 中的一种表达" in COLUMN_DOMAIN_REVIEW_PROMPT
    assert "A-B 已有 fk，而 C-A 经验证可稳定连接" in COLUMN_DOMAIN_REVIEW_PROMPT
    assert "关系实体的 metadata 是业务摘要" in COLUMN_DOMAIN_REVIEW_PROMPT
    assert "端点身份、成员清单、主外键角色和各列 cardinality" in COLUMN_DOMAIN_REVIEW_PROMPT
    assert "同一选择问题保留一个实体" in DISAMBIGUATE_PROMPT
    assert "补齐成员边，把说明整理成" in DISAMBIGUATE_PROMPT
    assert "整个任务不执行 SQL" in DISAMBIGUATE_PROMPT
    assert "Related 列表负责显示候选身份" in DISAMBIGUATE_PROMPT


def test_hints_are_meta_only_and_do_not_enter_retrieval_text():
    assert "hints" not in EMBEDDING_TEXT_FIELDS
    assert "hints" not in BM25_TEXT_FIELDS
    text = _embedding_text({"name": "schools", "detail": "school rows", "hints": ["secret hint"]})

    assert "school rows" in text
    assert "secret hint" not in text
