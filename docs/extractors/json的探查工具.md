


# 数据类型和VFS

## sample,topK,json,yaml等序列化文件的VFS逻辑

这些类型的文件元数据中要有一个source，指向系统中存储该文件的位置，然后如果ls该文件则是直接打开该文件

对于sample这种需要缓存的，则在.sample文件夹下面生成一个_bin文件，直接读取

当ls进入到json等序列化文件时则进入到一个不同的逻辑，显示以下几个字段
`[HasSub] | [Key] |  [Info]`
- **对于容器 (DICT/LIST)**：展示内部元素的数量（如 `5 pairs` 或 `12 items`）。
- **对于数值 (STR/INT/BOOL)**：直接展示其真实取值（若字符串过长则进行截断）。
- **对于空值 (NULL)**：直接显示 `null`。
[HasSub] | [name]              | [Info]
---------|---------------------|------------------
[+]      | metadata.DICT       | 5 pairs
[+]      | users.LIST          | 120 items
[ ]      | version.STR         | "1.2.4-stable"
[ ]      | is_active.BOOL      | true
[ ]      | timeout.INT         | 3000
[ ]      | legacy_config.NULL  | null
  

如果一个Key 对应的值是一个巨大的文本块（比如 1000 字的简介），在 `ls` 的 Value 处进行强制截断（例如前 20 字符）
为了防止json key名里空格符号等影响执行，利用URL 编码 (URL Encoding)