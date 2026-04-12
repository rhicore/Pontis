"""DB Column Relation Generator - 数据库列关系LLM打分生成器

职责：
- 读取 *.db 下的所有 *.overlap 实体（硬性规则检测结果）
- 使用LLM进行语义验证和置信度打分
- 在 _entity/ 下创建 [表名].[列名]__to__[表名].[列名].rel 实体

独立执行：
    python -m extractor.db_column_rel ./my_data
"""
import os
import json
import logging
from typing import Optional
from storage import Store
from extractor.utils import get_llm

logger = logging.getLogger(__name__)


def generate(store: Store, config=None) -> None:
    """为所有.overlap实体生成LLM关系评分"""
    logger.info("=== Generating column relations (LLM scoring) ===")

    llm = get_llm(config)
    if not llm:
        logger.warning("LLM not configured, skipping relation generation")
        return

    # 查找所有.overlap实体
    overlap_refs = store.find_nodes("*.db::*.overlap")

    created_count = 0
    for ref in overlap_refs:
        try:
            if _generate_for_overlap(ref, store, llm):
                created_count += 1
        except Exception as e:
            logger.warning(f"Failed to generate relation for {ref}: {e}")

    if created_count > 0:
        logger.info(f"  Total relations created: {created_count}")


def _generate_for_overlap(ref: str, store: Store, llm) -> bool:
    """为单个.overlap实体生成.rel关系"""
    path, entity_name = ref.split("::", 1)
    overlap_meta = store.get_meta(ref)
    if not overlap_meta:
        return False

    # 检查是否已存在.rel实体
    rel_entity_name = entity_name.replace(".overlap", ".rel")
    if store.node_exists(f"{path}::{rel_entity_name}"):
        return False

    stats = overlap_meta.get("stats", {})
    match_type = overlap_meta.get("match_type", "WEAK_MATCH")
    from_type = overlap_meta.get("from_type", "TEXT")
    to_type = overlap_meta.get("to_type", "TEXT")

    # 预筛选
    jaccard = stats.get("jaccard", 0)
    coverage = max(stats.get("coverage_A_in_B", 0), stats.get("coverage_B_in_A", 0))

    if jaccard < 0.01 and coverage < 0.05:
        logger.debug(f"Skipping {entity_name}: too low overlap")
        return False

    heuristic_score = _calculate_heuristic_score(
        stats, from_type, to_type, match_type
    )

    llm_result = _llm_score(overlap_meta, stats, heuristic_score, llm)
    if not llm_result:
        return False

    confidence = llm_result.get("confidence", 0.0)

    if confidence < 0.5:
        logger.debug(f"Filtered {entity_name}: confidence {confidence} < 0.5")
        return False

    # 创建.rel实体
    rel_meta = {
        "relation_type": "column_relation",
        "from_table": overlap_meta.get("from_table"),
        "from_column": overlap_meta.get("from_column"),
        "from_type": from_type,
        "to_table": overlap_meta.get("to_table"),
        "to_column": overlap_meta.get("to_column"),
        "to_type": to_type,
        "confidence": confidence,
        "can_join": llm_result.get("can_join", confidence >= 0.5),
        "reason": llm_result.get("reason", ""),
        "heuristic_score": heuristic_score,
        "overlap_stats": stats,
        "created_at": __import__('datetime').datetime.now().isoformat(),
    }

    store.create_node(f"{path}::{rel_entity_name}", meta=rel_meta)

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


def _llm_score(overlap_meta, stats, heuristic_score, llm) -> Optional[dict]:
    """使用LLM进行关系评分"""
    from_table = overlap_meta.get("from_table", "")
    from_column = overlap_meta.get("from_column", "")
    to_table = overlap_meta.get("to_table", "")
    to_column = overlap_meta.get("to_column", "")
    from_type = overlap_meta.get("from_type", "TEXT")
    to_type = overlap_meta.get("to_type", "TEXT")
    match_type = overlap_meta.get("match_type", "WEAK_MATCH")

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
