"""DB Column Relation Generator - 数据库列关系LLM打分生成器

职责：
- 读取 *.db 下的所有 *.overlap 文件（硬性规则检测结果）
- 使用LLM进行语义验证和置信度打分
- 在.db目录下创建 [表名].[列名]__to__[表名].[列名].rel 文件

评分策略（扣分制）：
初始分数: 1.0
├── 语义一票否决: 直接扣至 0.0 (如用户表与日志类型表)
├── 数据类型风险:
│   ├── 整数/自增ID: 扣 0.4 (易巧合重叠)
│   ├── 枚举/布尔值: 扣 0.8 (重叠无意义)
│   └── UUID/复杂编码: 不扣分 (重叠是强证据)
└── 统计显著性:
    ├── 绝对数量过少
    ├── 相对占比过低
    └── 覆盖率 < 10%
    └── 扣 0.3 - 0.6

阈值过滤: 仅保留置信度 >= 0.5 的边

独立执行：
    python -m extractor.db_column_rel ./my_data
"""
import os
import json
import logging
from typing import List, Dict, Optional
from extractor.utils import VFSStorage, NodeRef, Config, load_config, get_llm

logger = logging.getLogger(__name__)


def generate(storage: VFSStorage, config: Optional[Config] = None) -> None:
    """为所有.overlap文件生成LLM关系评分"""
    logger.info("=== Generating column relations (LLM scoring) ===")

    # 获取LLM客户端
    llm = get_llm(config)
    if not llm:
        logger.warning("LLM not configured, skipping relation generation")
        return

    # 查找所有.overlap文件
    overlap_nodes = storage.find_nodes("*.db/_entity/*.overlap")

    created_count = 0
    for node in overlap_nodes:
        try:
            if _generate_for_overlap(node, storage, llm):
                created_count += 1
        except Exception as e:
            logger.warning(f"Failed to generate relation for {node.name}: {e}")

    if created_count > 0:
        logger.info(f"  Total relations created: {created_count}")


def _generate_for_overlap(node: NodeRef, storage: VFSStorage, llm) -> bool:
    """为单个.overlap文件生成.rel关系"""
    # 读取overlap meta
    overlap_meta = storage.read_meta(node)
    if not overlap_meta:
        return False

    # 检查是否已存在.rel文件
    rel_filename = node.name.replace(".overlap", ".rel")
    db_rel_path = os.path.dirname(node.rel_path)
    rel_rel_path = os.path.join(db_rel_path, rel_filename)
    rel_node = NodeRef(rel_rel_path, node.pontis_root)

    if storage.exists(rel_node):
        return False

    # 获取统计信息
    stats = overlap_meta.get("stats", {})
    match_type = overlap_meta.get("match_type", "WEAK_MATCH")
    from_type = overlap_meta.get("from_type", "TEXT")
    to_type = overlap_meta.get("to_type", "TEXT")

    # 预筛选：基于硬性规则的快速过滤
    jaccard = stats.get("jaccard", 0)
    coverage = max(stats.get("coverage_A_in_B", 0), stats.get("coverage_B_in_A", 0))

    # 如果Jaccard极低且覆盖率极低，直接跳过
    if jaccard < 0.01 and coverage < 0.05:
        logger.debug(f"Skipping {node.name}: too low overlap")
        return False

    # 计算启发式分数（用于辅助LLM判断）
    heuristic_score = _calculate_heuristic_score(
        stats, from_type, to_type, match_type
    )

    # LLM评分
    llm_result = _llm_score(overlap_meta, stats, heuristic_score, llm)
    if not llm_result:
        return False

    confidence = llm_result.get("confidence", 0.0)

    # 阈值过滤: 仅保留置信度 >= 0.5 的边
    if confidence < 0.5:
        logger.debug(f"Filtered {node.name}: confidence {confidence} < 0.5")
        return False

    # 创建.rel文件
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

    storage.ensure_dir(rel_node.full_path)
    storage.write_meta(rel_node, rel_meta)

    logger.info(f"  Relation: {rel_filename} (confidence={confidence:.2f})")
    return True


def _calculate_heuristic_score(
    stats: Dict,
    from_type: str,
    to_type: str,
    match_type: str
) -> float:
    """
    计算启发式分数（扣分制）
    用于辅助LLM判断
    """
    score = 1.0

    # 数据类型风险扣分
    type_risk = _get_type_risk(from_type) + _get_type_risk(to_type)
    score -= type_risk * 0.5  # 平均风险系数

    # 统计显著性扣分
    jaccard = stats.get("jaccard", 0)
    coverage = max(stats.get("coverage_A_in_B", 0), stats.get("coverage_B_in_A", 0))

    if coverage < 0.1:
        score -= 0.6
    elif coverage < 0.3:
        score -= 0.3

    if jaccard < 0.1:
        score -= 0.3

    # STRONG_MATCH加分
    if match_type == "STRONG_MATCH":
        score += 0.2

    return round(max(0.0, min(1.0, score)), 2)


def _get_type_risk(data_type: str) -> float:
    """
    获取数据类型风险系数
    - 整数/自增ID: 0.4 (易巧合重叠)
    - 枚举/布尔值: 0.8 (重叠无意义)
    - UUID/复杂编码: 0.0 (重叠是强证据)
    - 其他: 0.2
    """
    dtype_upper = (data_type or "TEXT").upper()

    # 整数/自增ID
    if dtype_upper in ["INT", "INTEGER", "SERIAL", "BIGINT", "SMALLINT"]:
        return 0.4

    # 枚举/布尔值
    if dtype_upper in ["BOOL", "BOOLEAN", "ENUM"]:
        return 0.8

    # UUID (重叠是强证据)
    if "UUID" in dtype_upper or "GUID" in dtype_upper:
        return 0.0

    # 其他数值类型也有风险
    if dtype_upper in ["REAL", "FLOAT", "DOUBLE"]:
        return 0.3

    # 文本类型风险较低
    return 0.2


def _llm_score(
    overlap_meta: Dict,
    stats: Dict,
    heuristic_score: float,
    llm
) -> Optional[Dict]:
    """
    使用LLM进行关系评分
    """
    from_table = overlap_meta.get("from_table", "")
    from_column = overlap_meta.get("from_column", "")
    to_table = overlap_meta.get("to_table", "")
    to_column = overlap_meta.get("to_column", "")
    from_type = overlap_meta.get("from_type", "TEXT")
    to_type = overlap_meta.get("to_type", "TEXT")
    match_type = overlap_meta.get("match_type", "WEAK_MATCH")

    # 构建LLM提示
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

Evaluation Criteria:
1. Semantic Consistency: Do the tables semantically relate? (e.g., User table ↔ Order table makes sense)
2. Data Type Risk:
   - INT/ID columns: High risk of coincidental overlap (e.g., auto-increment IDs)
   - ENUM/BOOL: Very high risk - overlap is meaningless
   - UUID/Complex codes: Low risk - overlap is strong evidence
3. Statistical Significance:
   - Low Jaccard (< 0.1) + Low coverage (< 10%) = Weak evidence
   - High coverage (> 50%) = Strong evidence

Score Guidelines (0.0 - 1.0):
- 0.9-1.0: Perfect match - semantic fit + UUID or very high coverage
- 0.7-0.8: Good match - semantic fit + reasonable coverage
- 0.5-0.6: Possible match - semantic fit but weak evidence (e.g., ID overlap with low coverage)
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

        # 解析JSON响应
        # 提取JSON部分
        json_start = response.find('{')
        json_end = response.rfind('}') + 1

        if json_start >= 0 and json_end > json_start:
            json_str = response[json_start:json_end]
            result = json.loads(json_str)

            # 验证和规范化
            confidence = float(result.get("confidence", heuristic_score))
            confidence = max(0.0, min(1.0, confidence))

            return {
                "can_join": result.get("can_join", confidence >= 0.5),
                "confidence": round(confidence, 2),
                "reason": result.get("reason", "No reason provided")
            }

    except Exception as e:
        logger.debug(f"LLM parsing failed: {e}, using heuristic score")

    # 降级：使用启发式分数
    return {
        "can_join": heuristic_score >= 0.5,
        "confidence": heuristic_score,
        "reason": f"Fallback to heuristic score (LLM failed)"
    }


def main():
    """CLI入口"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate DB column relations (LLM scored)")
    parser.add_argument('target', help='Directory with .pontis')
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format='%(message)s')

    target_path = os.path.abspath(args.target)
    pontis_path = os.path.join(target_path, ".pontis")

    if not os.path.exists(pontis_path):
        print(f"Error: No .pontis found at {pontis_path}", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    storage = VFSStorage(pontis_path)
    generate(storage, config)
    print("Done.")


if __name__ == '__main__':
    main()
