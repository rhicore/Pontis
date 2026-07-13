"""Relation-preserving comparison of one prediction and one or more gold results."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import permutations
from math import isfinite, perm
import re
from typing import Any, Iterable, Sequence


_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T].*)?$")
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


@dataclass(frozen=True)
class ResultTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def width(self) -> int:
        return len(self.columns)


@dataclass(frozen=True)
class GoldenResult:
    """One acceptable gold result and its optional answer-column contract."""

    table: ResultTable
    required_columns: tuple[int, ...] | None = None
    ordered: bool | None = None
    name: str | None = None


@dataclass(frozen=True)
class ComparisonPolicy:
    """Benchmark-specific choices applied by the shared comparator."""

    ordered: bool = False
    allow_extra_predicted_columns: bool = False
    allow_column_reorder: bool = True
    max_reordered_columns: int = 8
    max_column_mappings: int = 200_000
    trim_strings: bool = True
    parse_numeric_strings: bool = False
    float_round_digits: int = 9
    numeric_abs_tolerance: Decimal | None = None
    null_tokens: tuple[str, ...] = ("__NULL__",)


@dataclass(frozen=True)
class ResultComparison:
    business_correct: bool
    match_type: str
    matched_gold_index: int | None = None
    matched_gold_name: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_correct": self.business_correct,
            "match_type": self.match_type,
            "matched_gold_index": self.matched_gold_index,
            "matched_gold_name": self.matched_gold_name,
            "details": self.details,
        }


def compare_result_sets(
    predicted: ResultTable | str,
    goldens: Sequence[GoldenResult],
    *,
    policy: ComparisonPolicy | None = None,
) -> ResultComparison:
    """Match a prediction against any complete golden-result alternative."""

    policy = policy or ComparisonPolicy()
    if isinstance(predicted, str):
        return ResultComparison(False, "execution_error", details={"error": predicted})
    if not goldens:
        return ResultComparison(False, "missing_gold")

    invalid = _table_issue(predicted)
    if invalid:
        return ResultComparison(False, "invalid_prediction", details={"error": invalid})

    failures: list[dict[str, Any]] = []
    for index, golden in enumerate(goldens):
        comparison = _compare_one(predicted, golden, policy)
        if comparison.business_correct:
            return ResultComparison(
                True,
                comparison.match_type,
                matched_gold_index=index,
                matched_gold_name=golden.name,
                details=comparison.details,
            )
        failures.append(
            {
                "gold_index": index,
                "gold_name": golden.name,
                "match_type": comparison.match_type,
                **comparison.details,
            }
        )

    match_types = {failure["match_type"] for failure in failures}
    match_type = failures[0]["match_type"] if len(match_types) == 1 else "no_gold_alternative_match"
    return ResultComparison(
        False,
        match_type,
        details={"gold_alternatives": len(goldens), "failures": failures},
    )


def _compare_one(
    predicted: ResultTable,
    golden: GoldenResult,
    policy: ComparisonPolicy,
) -> ResultComparison:
    issue = _table_issue(golden.table)
    if issue:
        return ResultComparison(False, "invalid_gold", details={"error": issue})

    try:
        required = _required_columns(golden)
    except ValueError as exc:
        return ResultComparison(False, "invalid_gold_contract", details={"error": str(exc)})

    gold = _project_table(golden.table, required)
    if predicted.width < gold.width:
        return _width_mismatch(predicted, gold)
    if not policy.allow_extra_predicted_columns and predicted.width != gold.width:
        return _width_mismatch(predicted, gold)

    ordered = policy.ordered if golden.ordered is None else golden.ordered
    mappings, limited = _candidate_mappings(predicted, gold, policy)
    if not mappings:
        return ResultComparison(
            False,
            "column_mapping_unavailable" if limited else "column_count_mismatch",
            details={"predicted_columns": predicted.width, "gold_columns": gold.width},
        )

    gold_raw = tuple(tuple(row) for row in gold.rows)
    gold_normalized = _normalize_rows(gold.rows, policy)
    row_count_mismatch = len(predicted.rows) != len(gold.rows)

    for mapping in mappings:
        predicted_raw = _project_rows(predicted.rows, mapping)
        if _rows_match(predicted_raw, gold_raw, ordered=ordered, tolerance=None):
            return ResultComparison(
                True,
                _mapping_match_type(mapping, predicted, gold, normalized=False),
                details=_match_details(predicted, gold, mapping, ordered),
            )

        predicted_normalized = _normalize_rows(predicted_raw, policy)
        if _rows_match(
            predicted_normalized,
            gold_normalized,
            ordered=ordered,
            tolerance=policy.numeric_abs_tolerance,
        ):
            return ResultComparison(
                True,
                _mapping_match_type(mapping, predicted, gold, normalized=True),
                details=_match_details(predicted, gold, mapping, ordered),
            )

    if row_count_mismatch:
        match_type = "row_count_mismatch"
    elif limited:
        match_type = "column_mapping_search_limit"
    else:
        match_type = "ordered_row_mismatch" if ordered else "row_bag_mismatch"
    return ResultComparison(
        False,
        match_type,
        details={
            "predicted_rows": len(predicted.rows),
            "gold_rows": len(gold.rows),
            "ordered": ordered,
            "column_mapping_search_limited": limited,
        },
    )


def _table_issue(table: ResultTable) -> str | None:
    width = table.width
    for index, row in enumerate(table.rows):
        if len(row) != width:
            return f"row {index} has {len(row)} values; expected {width}"
    return None


def _required_columns(golden: GoldenResult) -> tuple[int, ...]:
    required = golden.required_columns
    if required is None or len(required) == 0:
        return tuple(range(golden.table.width))
    if len(set(required)) != len(required):
        raise ValueError("required_columns contains duplicates")
    invalid = [index for index in required if index < 0 or index >= golden.table.width]
    if invalid:
        raise ValueError(f"required column indexes out of range: {invalid}")
    return required


def _project_table(table: ResultTable, indexes: tuple[int, ...]) -> ResultTable:
    return ResultTable(
        tuple(table.columns[index] for index in indexes),
        _project_rows(table.rows, indexes),
    )


def _project_rows(
    rows: Iterable[Sequence[Any]],
    indexes: tuple[int, ...],
) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(row[index] for index in indexes) for row in rows)


def _width_mismatch(predicted: ResultTable, gold: ResultTable) -> ResultComparison:
    return ResultComparison(
        False,
        "column_count_mismatch",
        details={"predicted_columns": predicted.width, "gold_columns": gold.width},
    )


def _candidate_mappings(
    predicted: ResultTable,
    gold: ResultTable,
    policy: ComparisonPolicy,
) -> tuple[list[tuple[int, ...]], bool]:
    width = gold.width
    mappings: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()

    def add(mapping: tuple[int, ...] | None) -> None:
        if mapping is not None and mapping not in seen:
            seen.add(mapping)
            mappings.append(mapping)

    if predicted.width == width:
        add(tuple(range(width)))
    add(_header_mapping(predicted.columns, gold.columns))

    if not policy.allow_column_reorder or width > policy.max_reordered_columns:
        return mappings, policy.allow_column_reorder and width > policy.max_reordered_columns

    try:
        candidate_count = perm(predicted.width, width)
    except ValueError:
        return mappings, False
    if candidate_count > policy.max_column_mappings:
        return mappings, True

    for mapping in permutations(range(predicted.width), width):
        add(mapping)
    return mappings, False


def _header_mapping(
    predicted_columns: tuple[str, ...],
    gold_columns: tuple[str, ...],
) -> tuple[int, ...] | None:
    if len(set(predicted_columns)) != len(predicted_columns):
        return None
    if any(predicted_columns.count(column) != 1 for column in gold_columns):
        return None
    return tuple(predicted_columns.index(column) for column in gold_columns)


def _mapping_match_type(
    mapping: tuple[int, ...],
    predicted: ResultTable,
    gold: ResultTable,
    *,
    normalized: bool,
) -> str:
    if predicted.width > gold.width:
        return "projected_columns"
    if mapping != tuple(range(gold.width)):
        return "column_reorder"
    return "value_equivalent" if normalized else "exact"


def _match_details(
    predicted: ResultTable,
    gold: ResultTable,
    mapping: tuple[int, ...],
    ordered: bool,
) -> dict[str, Any]:
    return {
        "rows": len(gold.rows),
        "gold_columns": gold.width,
        "predicted_columns": predicted.width,
        "predicted_column_mapping": list(mapping),
        "ordered": ordered,
    }


def _normalize_rows(
    rows: Iterable[Sequence[Any]],
    policy: ComparisonPolicy,
) -> tuple[tuple[tuple[str, Any], ...], ...]:
    return tuple(tuple(_normalize_value(value, policy) for value in row) for row in rows)


def _normalize_value(value: Any, policy: ComparisonPolicy) -> tuple[str, Any]:
    if value is None or (isinstance(value, str) and value in policy.null_tokens):
        return ("null", None)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int):
        return ("number", Decimal(value))
    if isinstance(value, float):
        if not isfinite(value):
            return ("number", str(value))
        return ("number", Decimal(str(round(value, policy.float_round_digits))))
    if isinstance(value, Decimal):
        return ("number", value)
    if isinstance(value, (date,)):
        return ("date", value.isoformat())
    if isinstance(value, str):
        text = value.strip() if policy.trim_strings else value
        parsed_date = _parse_date(text)
        if parsed_date is not None:
            return ("date", parsed_date.isoformat())
        if policy.parse_numeric_strings and _NUMBER_RE.fullmatch(text):
            try:
                return ("number", Decimal(text))
            except InvalidOperation:
                pass
        return ("string", text)
    return (type(value).__name__, _freeze(value))


def _parse_date(text: str) -> date | None:
    if not _DATE_RE.match(text):
        return None
    head = text.split(" ", 1)[0].split("T", 1)[0].replace("/", "-")
    parts = head.split("-")
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (IndexError, ValueError):
        return None


def _freeze(value: Any) -> Any:
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    return value


def _rows_match(
    predicted: Sequence[Sequence[Any]],
    golden: Sequence[Sequence[Any]],
    *,
    ordered: bool,
    tolerance: Decimal | None,
) -> bool:
    if len(predicted) != len(golden):
        return False
    if tolerance is None or tolerance == 0:
        predicted_rows = tuple(tuple(_freeze(value) for value in row) for row in predicted)
        golden_rows = tuple(tuple(_freeze(value) for value in row) for row in golden)
        if ordered:
            return predicted_rows == golden_rows
        return Counter(predicted_rows) == Counter(golden_rows)
    if ordered:
        return all(_row_values_equal(pred, gold, tolerance) for pred, gold in zip(predicted, golden))
    return _unordered_tolerance_match(predicted, golden, tolerance)


def _row_values_equal(
    predicted: Sequence[Any],
    golden: Sequence[Any],
    tolerance: Decimal,
) -> bool:
    for pred_value, gold_value in zip(predicted, golden):
        if pred_value == gold_value:
            continue
        if (
            isinstance(pred_value, tuple)
            and isinstance(gold_value, tuple)
            and pred_value[0] == gold_value[0] == "number"
        ):
            try:
                if abs(pred_value[1] - gold_value[1]) <= tolerance:
                    continue
            except (InvalidOperation, TypeError):
                pass
        return False
    return True


def _unordered_tolerance_match(
    predicted: Sequence[Sequence[Any]],
    golden: Sequence[Sequence[Any]],
    tolerance: Decimal,
) -> bool:
    pred_counter = Counter(tuple(row) for row in predicted)
    gold_counter = Counter(tuple(row) for row in golden)
    common = pred_counter & gold_counter
    pred_counter -= common
    gold_counter -= common
    predicted = tuple(pred_counter.elements())
    golden = tuple(gold_counter.elements())
    if not predicted and not golden:
        return True
    if len(predicted) != len(golden):
        return False

    exact_indexes = tuple(
        index
        for index in range(len(golden[0]) if golden else 0)
        if not all(
            isinstance(row[index], tuple) and row[index][0] == "number"
            for row in (*predicted, *golden)
        )
    )
    gold_groups: dict[tuple[Any, ...], list[Sequence[Any]]] = defaultdict(list)
    pred_groups: dict[tuple[Any, ...], list[Sequence[Any]]] = defaultdict(list)
    for row in golden:
        gold_groups[tuple(row[index] for index in exact_indexes)].append(row)
    for row in predicted:
        pred_groups[tuple(row[index] for index in exact_indexes)].append(row)
    if set(gold_groups) != set(pred_groups):
        return False

    for signature, gold_rows in gold_groups.items():
        pred_rows = pred_groups[signature]
        if len(gold_rows) != len(pred_rows):
            return False
        gold_counts = Counter(tuple(row) for row in gold_rows)
        pred_counts = Counter(tuple(row) for row in pred_rows)
        gold_unique = list(gold_counts)
        pred_unique = list(pred_counts)
        numeric_indexes = [
            index
            for index in range(len(gold_unique[0]) if gold_unique else 0)
            if index not in exact_indexes
        ]
        if not numeric_indexes:
            return False
        pivot = max(
            numeric_indexes,
            key=lambda index: len({row[index][1] for row in pred_unique}),
        )
        sorted_predicted = sorted(
            (row[pivot][1], pred_index)
            for pred_index, row in enumerate(pred_unique)
        )
        sorted_values = [value for value, _ in sorted_predicted]
        adjacency = []
        for gold_row in gold_unique:
            value = gold_row[pivot][1]
            start = bisect_left(sorted_values, value - tolerance)
            end = bisect_right(sorted_values, value + tolerance)
            adjacency.append(
                [
                    pred_index
                    for _, pred_index in sorted_predicted[start:end]
                    if _row_values_equal(pred_unique[pred_index], gold_row, tolerance)
                ]
            )
        if any(not candidates for candidates in adjacency):
            return False
        if not _has_capacity_matching(
            adjacency,
            [gold_counts[row] for row in gold_unique],
            [pred_counts[row] for row in pred_unique],
        ):
            return False
    return True


def _has_perfect_matching(adjacency: list[list[int]], predicted_count: int) -> bool:
    matched_gold = [-1] * predicted_count

    def augment(gold_index: int, visited: set[int]) -> bool:
        for predicted_index in adjacency[gold_index]:
            if predicted_index in visited:
                continue
            visited.add(predicted_index)
            previous = matched_gold[predicted_index]
            if previous == -1 or augment(previous, visited):
                matched_gold[predicted_index] = gold_index
                return True
        return False

    return all(
        augment(gold_index, set())
        for gold_index in sorted(range(len(adjacency)), key=lambda index: len(adjacency[index]))
    )


def _has_capacity_matching(
    adjacency: list[list[int]],
    gold_counts: list[int],
    predicted_counts: list[int],
) -> bool:
    """Maximum-flow matching for duplicate rows without expanding each copy."""

    gold_size = len(gold_counts)
    pred_size = len(predicted_counts)
    source = 0
    gold_start = 1
    pred_start = gold_start + gold_size
    sink = pred_start + pred_size
    graph: list[list[list[int]]] = [[] for _ in range(sink + 1)]

    def add_edge(left: int, right: int, capacity: int) -> None:
        graph[left].append([right, capacity, len(graph[right])])
        graph[right].append([left, 0, len(graph[left]) - 1])

    total = sum(gold_counts)
    for index, count in enumerate(gold_counts):
        add_edge(source, gold_start + index, count)
    for gold_index, candidates in enumerate(adjacency):
        for pred_index in candidates:
            add_edge(gold_start + gold_index, pred_start + pred_index, total)
    for index, count in enumerate(predicted_counts):
        add_edge(pred_start + index, sink, count)

    flow = 0
    while True:
        levels = [-1] * len(graph)
        levels[source] = 0
        queue = [source]
        for node in queue:
            for target, capacity, _ in graph[node]:
                if capacity > 0 and levels[target] < 0:
                    levels[target] = levels[node] + 1
                    queue.append(target)
        if levels[sink] < 0:
            break

        positions = [0] * len(graph)

        def push(node: int, amount: int) -> int:
            if node == sink:
                return amount
            while positions[node] < len(graph[node]):
                edge = graph[node][positions[node]]
                target, capacity, reverse = edge
                if capacity > 0 and levels[target] == levels[node] + 1:
                    sent = push(target, min(amount, capacity))
                    if sent:
                        edge[1] -= sent
                        graph[target][reverse][1] += sent
                        return sent
                positions[node] += 1
            return 0

        while sent := push(source, total - flow):
            flow += sent
            if flow == total:
                return True
    return flow == total
