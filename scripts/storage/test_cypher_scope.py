"""Regression checks for logical-project Cypher scoping."""

from storage.cypher_scope import scope_user_cypher


def _scope(query: str) -> str:
    scoped, params = scope_user_cypher(query, {}, "alpha")
    assert params == {"__pontis_project": "alpha"}
    return scoped


def test_bound_variables_are_not_redecorated_in_merge() -> None:
    scoped = _scope(
        "MATCH (created {id: $created_id}), (endpoint:col {_ref: $endpoint_ref}) "
        "MERGE (endpoint)-[r:RELATED_TO]->(created) RETURN count(r) AS created"
    )
    assert "MATCH (created {project: $__pontis_project, id: $created_id})" in scoped
    assert "(endpoint:col {project: $__pontis_project, _ref: $endpoint_ref})" in scoped
    assert "MERGE (endpoint)-[r:RELATED_TO]->(created)" in scoped


def test_add_edge_bound_variables_are_not_redecorated() -> None:
    scoped = _scope(
        "MATCH (a:col {_ref: $a_ref}), (b:col {_ref: $b_ref}) "
        "MERGE (a)-[r:RELATED_TO]->(b) RETURN count(r) AS created"
    )
    assert "MATCH (a:col {project: $__pontis_project, _ref: $a_ref})" in scoped
    assert "(b:col {project: $__pontis_project, _ref: $b_ref})" in scoped
    assert "MERGE (a)-[r:RELATED_TO]->(b)" in scoped


def test_new_merge_variables_are_scoped() -> None:
    scoped = _scope("MERGE (a:thing {name: $name}) RETURN a")
    assert scoped == "MERGE (a:thing {project: $__pontis_project, name: $name}) RETURN a"


def test_union_starts_a_new_variable_scope() -> None:
    scoped = _scope("MATCH (n:table) RETURN n UNION MATCH (n:view) RETURN n")
    assert scoped.count("project: $__pontis_project") == 2


def test_schema_ddl_is_not_node_scoped() -> None:
    query = (
        "CREATE VECTOR INDEX `embedding_col` IF NOT EXISTS "
        "FOR (n:`col`) ON (n.detail_embedding) "
        "OPTIONS {indexConfig: {`vector.dimensions`: 1024}}"
    )
    scoped, params = scope_user_cypher(query, {}, "alpha")
    assert scoped == query
    assert params == {}


def main() -> None:
    test_bound_variables_are_not_redecorated_in_merge()
    test_add_edge_bound_variables_are_not_redecorated()
    test_new_merge_variables_are_scoped()
    test_union_starts_a_new_variable_scope()
    test_schema_ddl_is_not_node_scoped()
    print("cypher scope tests passed")


if __name__ == "__main__":
    main()
