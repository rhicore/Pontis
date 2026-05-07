"""DB Column Relation Generator - 数据库列关系LLM打分生成器

职责：
- 读取 *.db 下的所有 overlap 实体（labels=["overlap"]）
- 使用LLM进行语义验证和置信度打分
- 在 _entity/ 下创建 [表名].[列名]->[表名].[列名] 实体（labels=["rel"]）

独立执行：
    python -m extractor.db_column_rel ./my_data
"""
import json
import logging
from typing import Optional
from storage import Store
from extractor.modules.utils.loader import Config

logger = logging.getLogger(__name__)

DB_EXTENSIONS = ["*.db", "*.sqlite", "*.sqlite3", "*.duckdb"]


def generate(store: Store, config=None) -> None:
    """为所有 overlap 实体生成 LLM 关系评分"""
    logger.info("=== Generating column relations (LLM scoring) ===")

    llm = config.get_llm() if config else None
    if not llm:
        logger.warning("LLM not configured, skipping relation generation")
        return

    # 查找所有 overlap 实体（通过标签或后缀，兼容新旧数据）
    created_count = 0
    for ext in DB_EXTENSIONS:
        for ref in store.find_nodes(f"{ext}::*:overlap"):
            try:
                if _generate_for_overlap(ref, store, llm):
                    created_count += 1
            except Exception as e:
                logger.warning(f"Failed to generate relation for {ref}: {e}")
        # 兼容旧命名（带 .overlap 后缀）
        for ref in store.find_nodes(f"{ext}::*.overlap"):
            try:
                if _generate_for_overlap(ref, store, llm):
                    created_count += 1
            except Exception as e:
                logger.warning(f"Failed to generate relation for {ref}: {e}")

    if created_count > 0:
        logger.info(f"  Total relations created: {created_count}")


def _generate_for_overlap(ref: str, store: Store, llm) -> bool:
    """为单个 overlap 实体生成 rel 关系"""
    entity_name = ref
    overlap_meta = store.get_meta(ref)
    if not overlap_meta:
        return False

    # 查找父 db 文件实体
    db_parents = store.find_connected(ref, "*.db")
    db_ref = db_parents[0] if db_parents else ""

    # rel 实体与 overlap 同名（类型通过 label 区分）
    rel_entity_name = entity_name
    if store.node_exists(rel_entity_name):
        return False

    stats = overlap_meta.get("stats", {})
    # 从 entity name 解析定位信息
    # 格式: from_table.from_col->to_table.to_col
    if "->" not in entity_name:
        return False
    left, right = entity_name.split("->", 1)
    # left = from_table.from_col, right = to_table.to_col
    left_parts = left.split(".", 1)
    right_parts = right.split(".", 1)
    from_table = left_parts[0] if len(left_parts) >= 1 else ""
    from_column = left_parts[1] if len(left_parts) >= 2 else ""
    to_table = right_parts[0] if len(right_parts) >= 1 else ""
    to_column = right_parts[1] if len(right_parts) >= 2 else ""

    # 预筛选
    jaccard = stats.get("jaccard", 0)
    coverage = max(stats.get("coverage_A_in_B", 0), stats.get("coverage_B_in_A", 0))

    if jaccard < 0.01 and coverage < 0.05:
        logger.debug(f"Skipping {entity_name}: too low overlap")
        return False

    match_type = "WEAK_MATCH"  # sketch overlap 不再存储 match_type
    from_type = "TEXT"  # 从 entity name 不可推断，用默认值
    to_type = "TEXT"

    heuristic_score = _calculate_heuristic_score(
        stats, from_type, to_type, match_type
    )

    llm_result = _llm_score(from_table, from_column, to_table, to_column,
                            from_type, to_type, match_type, stats, heuristic_score, llm)
    if not llm_result:
        return False

    confidence = llm_result.get("confidence", 0.0)

    if confidence < 0.5:
        logger.debug(f"Filtered {entity_name}: confidence {confidence} < 0.5")
        return False

    # 创建 rel 实体（类型通过 label 区分）
    can_join = llm_result.get("can_join", confidence >= 0.5)
    reason = llm_result.get("reason", "")
    rel_meta = {
        "detail": f"来源：overlap 检测（Jaccard={stats.get('jaccard', 0):.4f}）。"
                  f"启发式分数={heuristic_score}，LLM置信度={confidence:.2f}。"
                  f"{'可JOIN' if can_join else '不建议JOIN'}。{reason}",
        "created_at": __import__('datetime').datetime.now().isoformat(),
    }

    store.create_node(rel_entity_name, meta=rel_meta, labels=["rel"])

    # 添加边: source table → rel, target table → rel
    from_table_ref = f"{db_ref}--{from_table}" if db_ref else from_table
    to_table_ref = f"{db_ref}--{to_table}" if db_ref else to_table
    store.add_edges([
        {"a": from_table_ref, "b": rel_entity_name},
        {"a": to_table_ref, "b": rel_entity_name},
    ])

    logger.info(f"  Relation: {rel_entity_name} (confidence={confidence:.2f})")
    return True


def _calculate_heuristic_score(stats, from_type, to_type, match_type) -> float:
    """计算启发式分数（扣分制）"""
    score = 1.0

    type_risk = _get_type_risk(from_type) + _get_type_risk(to_type)
    score -= type_risk * 0.5

    jaccard = stats.get("jaccard", 0)
    coverage = max(stats.get("coverage_A_in_B", 0), stats.get("coverage_B_in_A", 0))

    if coverage < 0.1:
        score -= 0.6
    elif coverage < 0.3:
        score -= 0.3

    if jaccard < 0.1:
        score -= 0.3

    if match_type == "STRONG_MATCH":
        score += 0.2

    return round(max(0.0, min(1.0, score)), 2)


def _get_type_risk(data_type: str) -> float:
    """获取数据类型风险系数"""
    dtype_upper = (data_type or "TEXT").upper()

    if dtype_upper in ["INT", "INTEGER", "SERIAL", "BIGINT", "SMALLINT"]:
        return 0.4
    if dtype_upper in ["BOOL", "BOOLEAN", "ENUM"]:
        return 0.8
    if "UUID" in dtype_upper or "GUID" in dtype_upper:
        return 0.0
    if dtype_upper in ["REAL", "FLOAT", "DOUBLE"]:
        return 0.3
    return 0.2


def _llm_score(from_table, from_column, to_table, to_column,
               from_type, to_type, match_type, stats, heuristic_score, llm) -> Optional[dict]:
    """使用LLM进行关系评分"""

    prompt = f"""You are a strict database auditor evaluating potential join relationships.

Evaluate whether these two columns should form a join relationship:

Column A:
- Table: {from_table}
- Column: {from_column}
- Type: {from_type}

Column B:
- Table: {to_table}
- Column: {to_column}
- Type: {to_type}

Overlap Statistics:
- Jaccard Similarity: {stats.get('jaccard', 0):.4f}
- Overlapping Values: {stats.get('card_overlap', 0)}
- Coverage A→B: {stats.get('coverage_A_in_B', 0):.2%}
- Coverage B→A: {stats.get('coverage_B_in_A', 0):.2%}
- Match Type: {match_type}

Heuristic Score (0-1): {heuristic_score}

Score Guidelines (0.0 - 1.0):
- 0.9-1.0: Perfect match - semantic fit + UUID or very high coverage
- 0.7-0.8: Good match - semantic fit + reasonable coverage
- 0.5-0.6: Possible match - semantic fit but weak evidence
- 0.3-0.4: Unlikely - semantic mismatch or coincidental overlap
- 0.0-0.2: Reject - clear semantic mismatch or meaningless overlap

Respond in JSON format:
{{
    "can_join": true/false,
    "confidence": 0.0-1.0,
    "reason": "Brief explanation of the scoring decision"
}}"""

    try:
        response = llm.complete(prompt, max_tokens=300)

        json_start = response.find('{')
        json_end = response.rfind('}') + 1

        if json_start >= 0 and json_end > json_start:
            json_str = response[json_start:json_end]
            result = json.loads(json_str)

            confidence = float(result.get("confidence", heuristic_score))
            confidence = max(0.0, min(1.0, confidence))

            return {
                "can_join": result.get("can_join", confidence >= 0.5),
                "confidence": round(confidence, 2),
                "reason": result.get("reason", "No reason provided")
            }

    except Exception as e:
        logger.debug(f"LLM parsing failed: {e}, using heuristic score")

    return {
        "can_join": heuristic_score >= 0.5,
        "confidence": heuristic_score,
        "reason": f"Fallback to heuristic score (LLM failed)"
    }
