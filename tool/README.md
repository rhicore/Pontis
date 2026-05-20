这是对于工具层实现的人工指示,agent实现必须永远按照这个来,ai禁止修改这个文件!


find 的输出格式必须严格继承输入 ref 的路径结构。
  它不是“给每个节点生成一个自认为完整的 canonical ref”，而是“把这次 ref 匹配到的路径原样表达出来”。
  例子：
  find({"ref": "*:db"})
  输出应该是：
  xx:db
  yy:db
  因为输入只有一段 *:db，所以输出也只有一段 name:db。
  再比如：
  find({"ref": "xx:db/*:table"})
  输出应该是：
  xx:db/table1:table
  xx:db/table2:table
  因为输入是两段路径 db -> table，所以输出也是两段路径。
  如果是：
  find({"ref": "xx:db/table1:table/*:col"})
  输出才应该是：
  xx:db/table1:table/col1:col
  xx:db/table1:table/col2:col
  也就是说：find 的输出不是节点 ID，不是全局绝对路径，也不是工具自己猜出来的完整路径，而是输入路径模式的实例化结果。


meta 展示邻接节点时，永远只显示邻接节点自己的名称，不显示完整路径。
    比如：
    meta("xx:db/table1:table")
    Related 里应该是：
    col:
    col1
    col2
    col3
    然后 agent 如果要访问某个邻接节点，应该自然地用：
    xx:db/table1:table/col1
    也就是：
    主节点 ref / 邻接节点名称
    这和图路径遍历是一致的。meta 不应该在 Related 里再塞一堆完整 ref，因为那会破坏统一模型，也会让 agent 复制乱。
