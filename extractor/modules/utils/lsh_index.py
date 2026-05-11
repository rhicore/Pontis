"""LSH Index — 共享索引基础设施

为列/JSON 值构建 hash bucket + KLL quantiles + Frequent strings 索引。
支持 O(1) 等值查询和 O(1) 范围预判，避免全表扫描。

索引文件格式 (.idx):
  Header (32 bytes):
    magic: 0x50544C49 | version: 1 | flags
    num_buckets | kll_size | freq_size
    distinct_count | total_rows
  KLL data (if flags & 0x01)
  Freq data (if flags & 0x02)
  Bucket offsets: (num_buckets+1) × uint32
  Hash data: sorted uint32 truncated hashes per bucket
"""
import hashlib
import os
import struct
from bisect import bisect_left
from typing import List, Optional

# Binary format constants
MAGIC = 0x50544C49  # 'PTLI'
VERSION = 1
HEADER_SIZE = 32
HEADER_FMT = '<IIIIIIII'  # magic, version, flags, num_buckets, kll_size, freq_size, distinct, total

FLAG_HAS_KLL = 0x01
FLAG_HAS_FREQ = 0x02
FLAG_IS_NUMERIC = 0x04

# Bucket count by cardinality
SMALL_THRESHOLD = 1000
LARGE_THRESHOLD = 1000000


def _hash_value(val) -> int:
    """将值 hash 为 uint64。"""
    if val is None:
        return hashlib.blake2b(b'\x00', digest_size=8).digest()[0] << 0
    if isinstance(val, bool):
        val = int(val)
    if isinstance(val, int):
        data = struct.pack('<q', val)
    elif isinstance(val, float):
        data = struct.pack('<d', val)
    else:
        data = str(val).encode('utf-8')
    digest = hashlib.blake2b(data, digest_size=8).digest()
    return struct.unpack('<Q', digest)[0]


def _bucket_id(hash_val: int, num_buckets: int) -> int:
    return hash_val % num_buckets


def _truncated_hash(hash_val: int) -> int:
    """取高 32 位作为截断 hash（低 bit 已用于选 bucket）。"""
    return (hash_val >> 32) & 0xFFFFFFFF


def ref_to_index_name(ref: str) -> str:
    """将 ref 转换为安全的索引文件名。
    db/event.db::event.id.INT.col → db_event.db_event.id.INT.col.idx
    """
    name = ref.replace(os.sep, '_').replace('/', '_').replace('::', '_')
    return name + '.idx'


def index_path(workspace, col_ref: str) -> str:
    """返回索引文件的完整路径。"""
    rows = workspace.cypher("MATCH (d:dir {path: '.'}) RETURN d.src AS src")
    if not rows:
        raise FileNotFoundError("project root source is not available")
    src = rows[0].get("src")
    if not src or not src.has("path"):
        raise FileNotFoundError("project root source path is not available")
    return os.path.join(src.get("path"), ".pontis", "_index", ref_to_index_name(col_ref))


def choose_bucket_count(cardinality: Optional[int] = None) -> int:
    """根据 cardinality 选择 bucket 数量。"""
    if cardinality is None:
        return 1024
    if cardinality < SMALL_THRESHOLD:
        return 64
    if cardinality > LARGE_THRESHOLD:
        return 4096
    return 1024


class LSHIndexWriter:
    """流式构建 LSH 索引。

    用法:
        writer = LSHIndexWriter(num_buckets=1024, is_numeric=True)
        for val in stream_values():
            writer.add_value(val)
        writer.write('path.idx')
    """

    def __init__(self, num_buckets: int = 1024, is_numeric: bool = False, top_k: int = 50):
        self.num_buckets = num_buckets
        self.is_numeric = is_numeric
        self.top_k = top_k

        self._buckets: List[List[int]] = [[] for _ in range(num_buckets)]
        self._distinct_count = 0
        self._total_rows = 0
        self._seen_hashes = set()  # 去重计数

        self._kll = None
        self._freq = None

        if is_numeric:
            try:
                from datasketches import kll_floats_sketch
                self._kll = kll_floats_sketch()
            except ImportError:
                pass

        try:
            from datasketches import frequent_strings_sketch
            self._freq = frequent_strings_sketch(top_k)
        except ImportError:
            pass

    def add_value(self, val):
        """添加一个值到索引。"""
        self._total_rows += 1

        if val is None:
            return

        # Hash bucket
        h = _hash_value(val)
        truncated = _truncated_hash(h)

        if h not in self._seen_hashes:
            self._seen_hashes.add(h)
            self._distinct_count += 1
            bid = _bucket_id(h, self.num_buckets)
            self._buckets[bid].append(truncated)

        # KLL quantiles (numeric)
        if self._kll is not None:
            try:
                self._kll.update(float(val))
            except (ValueError, TypeError):
                pass

        # Frequent strings
        if self._freq is not None:
            try:
                self._freq.update(str(val))
            except Exception:
                pass

    def to_bytes(self) -> bytes:
        """将索引序列化为 bytes。"""
        for b in self._buckets:
            b.sort()

        kll_data = b''
        freq_data = b''
        flags = 0

        if self._kll is not None:
            kll_data = self._kll.serialize()
            flags |= FLAG_HAS_KLL

        if self._freq is not None:
            freq_data = self._freq.serialize()
            flags |= FLAG_HAS_FREQ

        if self.is_numeric:
            flags |= FLAG_IS_NUMERIC

        offsets_size = (self.num_buckets + 1) * 4
        offset = HEADER_SIZE + len(kll_data) + len(freq_data) + offsets_size
        offsets = []
        for b in self._buckets:
            offsets.append(offset)
            offset += len(b) * 4
        offsets.append(offset)

        buf = struct.pack(HEADER_FMT,
                          MAGIC, VERSION, flags, self.num_buckets,
                          len(kll_data), len(freq_data),
                          self._distinct_count, self._total_rows)
        if kll_data:
            buf += kll_data
        if freq_data:
            buf += freq_data
        buf += struct.pack(f'<{len(offsets)}I', *offsets)
        for b in self._buckets:
            for h in b:
                buf += struct.pack('<I', h)
        return buf

    def write(self, filepath: str):
        """将索引写入二进制文件。"""
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'wb') as f:
            f.write(self.to_bytes())


class LSHIndexReader:
    """读取并查询 LSH 索引。"""

    def __init__(self):
        self.num_buckets = 0
        self.flags = 0
        self.distinct_count = 0
        self.total_rows = 0
        self._buckets: List[List[int]] = []
        self._kll = None
        self._freq = None

    @property
    def has_kll(self) -> bool:
        return self._kll is not None

    @property
    def has_freq(self) -> bool:
        return self._freq is not None

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional['LSHIndexReader']:
        """从 bytes 加载索引。"""
        reader = cls()
        try:
            pos = 0

            # Header
            if len(data) < HEADER_SIZE:
                return None
            (magic, version, flags, num_buckets,
             kll_size, freq_size,
             distinct_count, total_rows) = struct.unpack_from(HEADER_FMT, data, pos)
            pos += HEADER_SIZE

            if magic != MAGIC or version != VERSION:
                return None

            reader.num_buckets = num_buckets
            reader.flags = flags
            reader.distinct_count = distinct_count
            reader.total_rows = total_rows

            # KLL sketch
            if flags & FLAG_HAS_KLL and kll_size > 0:
                try:
                    from datasketches import kll_floats_sketch
                    reader._kll = kll_floats_sketch.deserialize(data[pos:pos + kll_size])
                except ImportError:
                    pass
                pos += kll_size

            # Freq sketch
            if flags & FLAG_HAS_FREQ and freq_size > 0:
                try:
                    from datasketches import frequent_strings_sketch
                    reader._freq = frequent_strings_sketch.deserialize(data[pos:pos + freq_size])
                except ImportError:
                    pass
                pos += freq_size

            # Bucket offsets
            offsets_size = (num_buckets + 1) * 4
            offsets = list(struct.unpack_from(f'<{num_buckets + 1}I', data, pos))
            pos += offsets_size

            # Hash data
            reader._buckets = []
            for i in range(num_buckets):
                start = offsets[i]
                end = offsets[i + 1]
                count = (end - start) // 4
                if count > 0:
                    bucket = list(struct.unpack_from(f'<{count}I', data, start))
                else:
                    bucket = []
                reader._buckets.append(bucket)

            return reader
        except Exception:
            return None

    @classmethod
    def load(cls, filepath: str) -> Optional['LSHIndexReader']:
        """从文件加载索引。"""
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, 'rb') as f:
                return cls.from_bytes(f.read())
        except Exception:
            return None

    def query_eq(self, val) -> bool:
        """等值查询：值是否存在（~10^-8 误判率）。"""
        h = _hash_value(val)
        truncated = _truncated_hash(h)
        bid = _bucket_id(h, self.num_buckets)

        bucket = self._buckets[bid] if bid < len(self._buckets) else []
        idx = bisect_left(bucket, truncated)
        return idx < len(bucket) and bucket[idx] == truncated

    def query_range_gt(self, threshold: float) -> int:
        """预估 > threshold 的行数（使用 KLL）。"""
        if self._kll is None:
            return -1  # 未知
        if self.total_rows == 0:
            return 0
        rank = self._kll.get_rank(float(threshold))
        return int((1.0 - rank) * self.total_rows)

    def query_range_lt(self, threshold: float) -> int:
        """预估 < threshold 的行数（使用 KLL）。"""
        if self._kll is None:
            return -1
        if self.total_rows == 0:
            return 0
        rank = self._kll.get_rank(float(threshold))
        return int(rank * self.total_rows)

    def query_range_gte(self, threshold: float) -> int:
        """预估 >= threshold 的行数。"""
        lt = self.query_range_lt(threshold)
        if lt < 0:
            return -1
        return self.total_rows - lt

    def query_range_lte(self, threshold: float) -> int:
        """预估 <= threshold 的行数。"""
        gt = self.query_range_gt(threshold)
        if gt < 0:
            return -1
        return self.total_rows - gt
