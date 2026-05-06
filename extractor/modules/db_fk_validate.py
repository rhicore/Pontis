"""DB FK Validate — 校验 FK 实体的实际数据一致性。

职责：
- 遍历所有 .fk 实体
- 对每个显式 FK 执行实际 JOIN 查询，验证数据一致性
- 计算 match_rate、violation_count，检测格式问题（如前导零缺失）
- 将校验结果写入 FK 实体的 meta

独立执行：
    python -m extractor.modules.db_fk_validate ./my_data
"""
import os
import logging
import sqlite3
from pathlib import Path

from storage import Store

logger = logging.getLogger(__name__)


def generate(store: Store) -> None:
    """校验所有 .fk 实体的实际数据一致性。"""
    logger.info("=== Validating FK data consistency ===")

    # 查找 FK 实体：新版通过标签，旧版通过后缀
    fk_refs = list(store.find_nodes("*:fk"))
    if not fk_refs:
        # 旧版兼容：通过 .fk 后缀
        fk_refs = list(store.find_nodes("*.fk"))
    if not fk_refs:
        logger.info("  No FK entities found")
        return

    validated = 0
    for ref in fk_refs:
        try:
            if _validate_one(ref, store):
                validated += 1
        except Exception as e:
            logger.warning(f"  Failed to validate {ref}: {e}")

    logger.info(f"  Validated {validated}/{len(fk_refs)} FK entities")


def _parse_fk_entity(entity_name: str) -> dict | None:
    """从 FK 实体名解析出 from_table, from_col, to_table, to_col。

    格式: {from_table}.{from_col}__to__{to_table}.{to_col}（新版无后缀）
    或: {from_table}.{from_col}__to__{to_table}.{to_col}.fk（旧版带后缀）
    """
    if "__to__" not in entity_name:
        return None

    body = entity_name.replace(".fk", "")
    parts = body.split("__to__", 1)
    if len(parts) != 2:
        return None

    from_part, to_part = parts

    from_segments = from_part.split(".", 1)
    to_segments = to_part.split(".", 1)

    if len(from_segments) < 2 or len(to_segments) < 2:
        return None

    return {
        "from_table": from_segments[0],
        "from_col": from_segments[1],
        "to_table": to_segments[0],
        "to_col": to_segments[1],
    }


def _get_db_path(ref: str, store: Store) -> str | None:
    """从 FK ref 获取数据库文件绝对路径。"""
    _files = store.find_nodes(f"{ref}::*:file")
    if not _files:
        return None
    db_entity = _files[0]
    db_meta = store.get_meta(db_entity)
    db_rel = db_meta.get("path", db_entity) if db_meta else db_entity
    db_path = os.path.join(store.project_path, db_rel)
    return db_path


def _validate_one(ref: str, store: Store) -> bool:
    """校验单个 FK 实体。"""
    entity_name = ref

    parsed = _parse_fk_entity(entity_name)
    if not parsed:
        return False

    abs_db_path = _get_db_path(ref, store)
    if not abs_db_path or not Path(abs_db_path).exists():
        return False

    ft, fc = parsed["from_table"], parsed["from_col"]
    tt, tc = parsed["to_table"], parsed["to_col"]

    try:
        conn = sqlite3.connect(abs_db_path)

        # 总行数
        total = conn.execute(f'SELECT COUNT(*) FROM "{ft}"').fetchone()[0]
        if total == 0:
            conn.close()
            return False

        # JOIN 匹配数
        matched = conn.execute(
            f'SELECT COUNT(*) FROM "{ft}" t '
            f'WHERE EXISTS (SELECT 1 FROM "{tt}" s WHERE s."{tc}" = t."{fc}")'
        ).fetchone()[0]

        match_rate = matched / total
        violation_count = total - matched

        # 检测格式问题：尝试 CAST AS INTEGER 或前导零修复
        format_hint = None
        if violation_count > 0:
            # 尝试: to_col 的前缀 '0' + from_col 能否匹配
            try:
                fixed = conn.execute(
                    f'SELECT COUNT(*) FROM "{ft}" t '
                    f'WHERE NOT EXISTS (SELECT 1 FROM "{tt}" s WHERE s."{tc}" = t."{fc}") '
                    f'  AND EXISTS (SELECT 1 FROM "{tt}" s WHERE s."{tc}" = \'0\' || t."{fc}")'
                ).fetchone()[0]
                if fixed == violation_count and fixed > 0:
                    format_hint = (
                        f"发现 {violation_count} 条 FK 违规，全部可通过在 {ft}.{fc} 前补 '0' 修复。"
                        f"推测 {ft}.{fc} 部分值缺少前导零（{len_f}位），而 {tt}.{tc} 为完整格式（{len_t}位）。"
                    )
                elif fixed > 0:
                    format_hint = (
                        f"发现 {violation_count} 条 FK 违规，其中 {fixed} 条可通过补 '0' 修复，"
                        f"剩余 {violation_count - fixed} 条为其他数据不一致。"
                    )
            except Exception:
                pass

            # 如果补零没修复，尝试 CAST AS INTEGER
            if not format_hint:
                try:
                    cast_fixed = conn.execute(
                        f'SELECT COUNT(*) FROM "{ft}" t '
                        f'WHERE NOT EXISTS (SELECT 1 FROM "{tt}" s WHERE s."{tc}" = t."{fc}") '
                        f'  AND EXISTS (SELECT 1 FROM "{tt}" s WHERE CAST(s."{tc}" AS INTEGER) = CAST(t."{fc}" AS INTEGER))'
                    ).fetchone()[0]
                    if cast_fixed == violation_count and cast_fixed > 0:
                        format_hint = (
                            f"发现 {violation_count} 条 FK 违规，全部可通过 CAST AS INTEGER 修复。"
                            f"推测存在数值类型/字符串格式不一致。"
                        )
                except Exception:
                    pass

        conn.close()

    except Exception as e:
        logger.warning(f"  Query failed for {ref}: {e}")
        return False

    # 更新 meta
    existing = store.get_meta(ref) or {}
    update = {
        "match_rate": round(match_rate, 4),
        "violation_count": violation_count,
        "total_count": total,
    }
    if format_hint:
        update["format_hint"] = format_hint

    # 更新 detail：附加校验信息
    old_detail = existing.get("detail", "")
    valid_status = "完全一致" if match_rate == 1.0 else f"匹配率 {match_rate*100:.1f}%（{violation_count} 条违规）"
    update["detail"] = f"{old_detail}\n数据校验：{valid_status}。"
    if format_hint:
        update["detail"] += f"\n{format_hint}"

    store.set_meta(ref, update)

    status = "OK" if match_rate == 1.0 else f"MISMATCH {match_rate*100:.1f}%"
    logger.info(f"  {entity_name}: {status} ({matched}/{total})")
    return True


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if len(sys.argv) < 2:
        print("Usage: python -m extractor.modules.db_fk_validate <project_path>")
        sys.exit(1)
    store = Store(sys.argv[1])
    generate(store)
