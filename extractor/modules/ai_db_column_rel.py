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
from storage.workspace import Workspace
from extractor.modules.utils.loader import Config

logger = logging.getLogger(__name__)



def generate(workspace: Workspace, config=None) -> None:
    """为所有 overlap 实体生成 LLM 关系评分"""
    logger.info("=== Generating column relations (LLM scoring) ===")

    llm = config.get_llm() if config else None
    if not llm:
        logger.warning("LLM not configured, skipping relation generation")
        return

    # 查找所有 overlap 实体（通过标签）
    created_count = 0
    overlap_rows = workspace.cypher("MATCH (o:overlap) RETURN o")
    for overlap_row in overlap_rows:
        ref = overlap_row["o"]["name"]
        try:
            if _generate_for_overlap(ref, workspace, llm):
                created_count += 1
        except Exception as e:
            logger.warning(f"Failed to generate relation for {ref}: {e}")

    if created_count > 0:
        logger.info(f"  Total relations created: {created_count}")


def _generate_for_overlap(ref: str, workspace: Workspace, llm) -> bool:
    """为单个 overlap 实体生成 rel 关系"""
    entity_name = ref
    overlap_meta_rows = workspace.cypher("MATCH (n {name: $name}) RETURN n", params={"name": ref})
    overlap_meta = overlap_meta_rows[0].get("n") if overlap_meta_rows else None
    if not overlap_meta:
        return False

    # 查找父 db 文件实体
    db_rows = workspace.cypher(f'MATCH (o {{name: "{ref}"}})--(d) WHERE d.name ENDS WITH ".db" RETURN d')
    db_ref = db_rows[0]["d"]["name"] if db_rows else ""

    # rel 实体与 overlap 同名（类型通过 label 区分）
    relname = entity_name
    if workspace.cypher(f'MATCH (n {{name: "{relname}"}}) RETURN n'):
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
        "match_type": match_type,
        "jaccard": stats.get('jaccard', 0),
        "heuristic_score": heuristic_score,
        "confidence": confidence,
        "can_join": can_join,
        "detail": reason,
        "created_at": __import__('datetime').datetime.now().isoformat(),
    }

    workspace.cypher(f'CREATE (r:rel {{name: "{relname}"}})')
    workspace.cypher('MATCH (n {name: $name}) SET n += $props', params={"name": relname, "props": rel_meta})

    # 添加边: source table → rel, target table → rel
    from_table_ref = from_table
    to_table_ref = to_table
    workspace.cypher(f'MATCH (a {{name: "{from_table_ref}"}}),(r {{name: "{relname}"}}) CREATE (a)--(r)')
    workspace.cypher(f'MATCH (a {{name: "{to_table_ref}"}}),(r {{name: "{relname}"}}) CREATE (a)--(r)')

    logger.info(f"  Relation: {relname} (confidence={confidence:.2f})")
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
        response = llm.complete(prompt)

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
