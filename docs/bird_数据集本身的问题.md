● 对，基本如此。排除 golden 本身有争议的（3 题）和 evidence 标注错误的（1 题），剩余 34 题错误可以归为两类：

  1. 跨表 JOIN（6 题） — 唯一的"数据库理解"缺口。Agent 不知道 Player→Match→Country 的隐含桥接路径。                                                                                                        
  2. Query 语义理解偏差（28 题） — Agent 对数据库结构是清楚的（表、列、关系都看到了），问题在于：
    - "player id" 指的是 id 还是 player_api_id？                                                                                                                                                           
    - "top 4" 是取前 4 个还是只取最高值？                                                                                                                                                                  
    - "percentage" 分母是子集还是全表？                                                                                                                                                                    
    - 要不要加 DISTINCT？要不要 LIMIT 1？                                                                                                                                                                  
    - 多条历史记录是全返回还是只取最新？                                                                                                                                                                   
                                                                                                                                                                                                           
  这些本质上是 BIRD 数据集本身的语义模糊性 — 同一个问题可以有合理的不同 SQL 实现，但 golden 只认一种写法。   