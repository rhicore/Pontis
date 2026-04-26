# BIRD Benchmark 错误分类 — formula_1

总计 72 条错误记录
- 类别1（探索不足，可改进）: 30 (41.7%)
- 类别2（查询歧义，难改进）: 42 (58.3%)

## Q849 [simple] 类别2-查询歧义
- 问题: Where can the introduction of the races held on Circuit de Barcelona-Catalunya be found?
- 标准 SQL: SELECT DISTINCT T1.url FROM circuits AS T1 INNER JOIN races AS T2 ON T2.circuitID = T1.circuitId WHERE T1.name = 'Circuit de Barcelona-Catalunya'
- 预测 SQL: SELECT races.url
FROM races
JOIN circuits ON races.circuitId = circuits.circuitId
WHERE circuits.name = 'Circuit de Barcelona-Catalunya'
- 工具调用: 15 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'introduction of the races'可以理解为所有比赛的介绍（返回多条url），也可以理解为赛道的介绍（返回一条url），模型选择了前者，而标准答案选择了后者。

---

## Q851 [simple] 类别1-探索不足
- 问题: Please list the positions of the circuits built by the constructor Renault.
- 标准 SQL: SELECT DISTINCT T1.position FROM constructorStandings AS T1 INNER JOIN constructors AS T2 ON T2.constructorId = T1.constructorId WHERE T2.name = 'Rena
- 预测 SQL: SELECT DISTINCT c.location
FROM circuits c
JOIN races r ON c.circuitId = r.circuitId
JOIN results res ON r.raceId = res.raceId
JOIN constructors con O
- 工具调用: 25 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求的是'circuits built by the constructor'（车队建造的赛道位置），但模型错误地理解为'车队比赛过的赛道位置'，使用了results表而非constructorStandings表，说明模型未正确理解constructorStandings表中position列的含义以及circuits表与constructorStandings的关联关系。

---

## Q855 [simple] 类别2-查询歧义
- 问题: Where can I find the information about the races held on Sepang International Circuit?
- 标准 SQL: SELECT DISTINCT T1.url FROM circuits AS T1 INNER JOIN races AS T2 ON T2.circuitID = T1.circuitId WHERE T1.name = 'Sepang International Circuit'
- 预测 SQL: SELECT races.url FROM races INNER JOIN circuits ON races.circuitId = circuits.circuitId WHERE circuits.name = 'Sepang International Circuit'
- 工具调用: 11 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'information about the races' 可以理解为所有比赛的信息（返回多条url），也可以理解为去重后的赛道信息（返回一条url），两种理解在语义上都通顺。

---

## Q860 [simple] 类别2-查询歧义
- 问题: For the driver who had the Q2 time as 0:01:40 in the qualifying race No. 355, what is his nationality?
- 标准 SQL: SELECT DISTINCT T2.nationality FROM qualifying AS T1 INNER JOIN drivers AS T2 ON T2.driverId = T1.driverId WHERE T1.raceId = 355 AND T1.q2 LIKE '1:40%
- 预测 SQL: SELECT d.nationality
FROM qualifying q
JOIN drivers d ON q.driverId = d.driverId
WHERE q.raceId = 355 AND q.q2 = '1:40.014'
Agent response (last 500 c
- 工具调用: 48 次 | Guardrail block: 1 次
- 判定理由: 问题本身有歧义：'Q2 time as 0:01:40' 可以理解为精确等于1分40秒（1:40.000），也可以理解为以1:40开头的所有时间（如1:40.014、1:40.060等）。标准SQL用LIKE '1:40%'匹配多个值，预测SQL用精确匹配，两种理解在语义上都通顺。

---

## Q861 [simple] 类别1-探索不足
- 问题: What is his number of the driver who finished 0:01:54 in the Q3 of qualifying race No.903?
- 标准 SQL: SELECT T2.number FROM qualifying AS T1 INNER JOIN drivers AS T2 ON T2.driverId = T1.driverId WHERE T1.raceId = 903 AND T1.q3 LIKE '1:54%'
- 预测 SQL: SELECT number FROM qualifying WHERE raceId = 903 AND q3 LIKE '1:54%'
Agent response (last 500 chars): Based on my exploration, I have a clear understa
- 工具调用: 18 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求返回车手的号码（driver's number），但模型直接从qualifying表取number列（临时车号），而正确答案应从drivers表取number列（车手固定号码），模型未正确理解number列的歧义说明。

---

## Q865 [moderate] 类别1-探索不足
- 问题: For all the drivers who finished the game in race No. 592, who is the oldest?
- 标准 SQL: SELECT T1.forename, T1.surname FROM drivers AS T1 INNER JOIN results AS T2 ON T2.driverId = T1.driverId WHERE T2.raceId = 592 AND T2.time IS NOT NULL 
- 预测 SQL: SELECT d.forename, d.surname, d.dob
FROM results r
JOIN drivers d ON r.driverId = d.driverId
WHERE r.raceId = 592 AND r.time IS NOT NULL
ORDER BY d.do
- 工具调用: 21 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：预测SQL多选了d.dob列，而标准SQL只要求forename和surname，这是对问题'who is the oldest'的列选择理解错误，属于模型未充分理解列含义。

---

## Q866 [moderate] 类别2-查询歧义
- 问题: Who was the player that got the lap time of 0:01:27 in the race No. 161? Show his introduction website.
- 标准 SQL: SELECT DISTINCT T2.forename, T2.surname, T2.url FROM lapTimes AS T1 INNER JOIN drivers AS T2 ON T2.driverId = T1.driverId WHERE T1.raceId = 161 AND T1
- 预测 SQL: SELECT DISTINCT drivers.url
FROM lapTimes
JOIN drivers ON lapTimes.driverId = drivers.driverId
WHERE lapTimes.raceId = 161
  AND lapTimes.time LIKE '1
- 工具调用: 27 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：问题要求显示车手的介绍网站（introduction website），但未明确指定是 drivers.url 还是其他来源，模型选择了仅输出 url，而标准 SQL 还输出了 forename 和 surname，两者对'展示介绍网站'的理解不同。

---

## Q871 [simple] 类别1-探索不足
- 问题: For the driver who had the Q2 time as 0:01:15 in race No. 347, where is he from?
- 标准 SQL: SELECT DISTINCT T2.nationality FROM qualifying AS T1 INNER JOIN drivers AS T2 ON T2.driverId = T1.driverId WHERE T1.raceId = 347 AND T1.q2 LIKE '1:15%
- 预测 SQL: SELECT d.nationality
FROM qualifying q
JOIN drivers d ON q.driverId = d.driverId
WHERE q.raceId = 347 AND q.q2 = '0:01:15'
Agent response (last 500 ch
- 工具调用: 56 次 | Guardrail block: 1 次
- 判定理由: 模型没理解schema：q2列的时间格式为'm:ss.mmm'（如'1:15.018'），而非'0:01:15'，模型错误地使用了精确字符串匹配导致查不到结果。

---

## Q879 [moderate] 类别1-探索不足
- 问题: For the driver who set the fastest lap speed, what is his nationality?
- 标准 SQL: SELECT T1.nationality FROM drivers AS T1 INNER JOIN results AS T2 ON T2.driverId = T1.driverId ORDER BY T2.fastestLapSpeed DESC LIMIT 1
- 预测 SQL: SELECT d.nationality
FROM results r
JOIN drivers d ON r.driverId = d.driverId
WHERE r.fastestLapSpeed IS NOT NULL
ORDER BY CAST(r.fastestLapSpeed AS R
- 工具调用: 15 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：fastestLapSpeed列存储为TEXT类型，但模型使用了CAST转换，而标准SQL直接按TEXT排序也能得到正确结果，说明模型对数据格式理解有误。

---

## Q881 [moderate] 类别1-探索不足
- 问题: For the drivers who took part in the race in 1983/7/16, what's their race completion rate?
- 标准 SQL: SELECT CAST(COUNT(CASE WHEN T2.time IS NOT NULL THEN T2.driverId END) AS REAL) * 100 / COUNT(T2.driverId) FROM races AS T1 INNER JOIN results AS T2 ON
- 预测 SQL: SELECT d.driverId, d.forename, d.surname,
       CAST(SUM(CASE WHEN r2.time IS NOT NULL THEN 1 ELSE 0 END) AS REAL) / COUNT(*) AS completion_rate
FROM
- 工具调用: 22 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema，错误地使用了自连接results表来计算完成率，导致按车手分组返回多行，而标准SQL只需对同一场比赛的results表做条件聚合即可。

---

## Q888 [simple] 类别2-查询歧义
- 问题: In which country was the first European Grand Prix hosted? Name the circuit and location.
- 标准 SQL: SELECT T1.country, T1.location FROM circuits AS T1 INNER JOIN races AS T2 ON T2.circuitID = T1.circuitId WHERE T2.name = 'European Grand Prix' ORDER B
- 预测 SQL: SELECT c.country, c.name AS circuit, c.location
FROM races r
JOIN circuits c ON r.circuitId = c.circuitId
WHERE r.name = 'European Grand Prix'
ORDER B
- 工具调用: 20 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：标准SQL要求输出circuit和location，预测SQL额外输出了circuit name，但问题未明确要求输出circuit name，且两种输出在语义上都合理。

---

## Q889 [simple] 类别1-探索不足
- 问题: When was the last f1 season whereby Brands Hatch hosted the British Grand Prix?
- 标准 SQL: SELECT T2.date FROM circuits AS T1 INNER JOIN races AS T2 ON T2.circuitID = T1.circuitId WHERE T1.name = 'Brands Hatch' AND T2.name = 'British Grand P
- 预测 SQL: SELECT MAX(r.year)
FROM races r
JOIN circuits c ON r.circuitId = c.circuitId
WHERE r.name = 'British Grand Prix'
AND c.name = 'Brands Hatch'
- 工具调用: 16 次 | Guardrail block: 1 次
- 判定理由: 模型没理解schema：问题问的是'last season'（最后赛季），应返回日期（date）而非年份（year），且golden SQL用ORDER BY year DESC LIMIT 1，模型用了MAX(year)导致语义偏差。

---

## Q891 [simple] 类别2-查询歧义
- 问题: Name all drivers in the 2010 Singapore Grand Prix order by their position stands.
- 标准 SQL: SELECT T3.forename, T3.surname FROM races AS T1 INNER JOIN driverStandings AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.driverId = T2
- 预测 SQL: SELECT d.forename || ' ' || d.surname
FROM results r
JOIN drivers d ON r.driverId = d.driverId
JOIN races ra ON r.raceId = ra.raceId
WHERE ra.year = 2
- 工具调用: 31 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'position stands'可理解为比赛中的完赛名次（results.positionOrder）或赛季积分榜排名（driverStandings.position），两者都是合理的理解方式，模型选择了前者而标准答案选择了后者。

---

## Q892 [moderate] 类别2-查询歧义
- 问题: State the driver with the most points scored. Find his full name with that points.
- 标准 SQL: SELECT T3.forename, T3.surname, T2.points FROM races AS T1 INNER JOIN driverStandings AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.dr
- 预测 SQL: SELECT d.forename, d.surname, SUM(r.points) AS points
FROM results r
JOIN drivers d ON r.driverId = d.driverId
GROUP BY d.driverId
ORDER BY points DES
- 工具调用: 19 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'most points scored'可以理解为单场最高积分（从results表取单场积分）或赛季累计最高积分（从driverStandings表取累计积分），两种理解在语义上都通顺，模型选择了前者而标准SQL选择了后者。

---

## Q893 [simple] 类别2-查询歧义
- 问题: Name the top 3 drivers and the points they scored in the 2017 Chinese Grand Prix.
- 标准 SQL: SELECT T3.forename, T3.surname, T2.points FROM races AS T1 INNER JOIN driverStandings AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.dr
- 预测 SQL: SELECT d.forename, d.surname, r.points
FROM results r
JOIN drivers d ON r.driverId = d.driverId
JOIN races ra ON r.raceId = ra.raceId
WHERE ra.year = 
- 工具调用: 19 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：问题中的'points they scored'可以指单场比赛积分（results.points）或赛季累计积分（driverStandings.points），两种理解在语义上都通顺，且数据库专家在完整schema下也可能选择不同表。

---

## Q896 [challenging] 类别2-查询歧义
- 问题: Calculate the percentage whereby Hamilton was not at the 1st track of the the f1 circuit since 2010.
- 标准 SQL: SELECT CAST(COUNT(CASE WHEN T2.position <> 1 THEN T2.position END) AS REAL) * 100 / COUNT(T2.driverStandingsId) FROM races AS T1 INNER JOIN driverStan
- 预测 SQL: SELECT CAST(COUNT(CASE WHEN r.position > 1 THEN 1 END) AS REAL) / COUNT(*) AS result
FROM results r
JOIN drivers d ON r.driverId = d.driverId
JOIN rac
- 工具调用: 23 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'not at the 1st track' 可以理解为正赛名次不是第1名（results.position），也可以理解为赛季积分榜排名不是第1（driverStandings.position），两种理解在语义上都通顺，属于对问题中'track'含义的不同解读。

---

## Q897 [moderate] 类别1-探索不足
- 问题: Name the driver with the most winning. Mention his nationality and what is his maximum point scores.
- 标准 SQL: SELECT T1.forename, T1.surname, T1.nationality, MAX(T2.points) FROM drivers AS T1 INNER JOIN driverStandings AS T2 ON T2.driverId = T1.driverId WHERE 
- 预测 SQL: SELECT d.forename || ' ' || d.surname AS name, d.nationality, MAX(r.points) AS max_points
FROM drivers d
JOIN results r ON d.driverId = r.driverId
GRO
- 工具调用: 30 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求用driverStandings表（含wins列）统计获胜次数，但模型错误地使用了results表通过position=1来推断获胜，且未使用wins列，说明未充分理解driverStandings.wins的语义。

---

## Q902 [simple] 类别1-探索不足
- 问题: Which race was Alex Yoong in when he was in track number less than 20?
- 标准 SQL: SELECT T1.name FROM races AS T1 INNER JOIN driverStandings AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.driverId = T2.driverId WHERE 
- 预测 SQL: SELECT r.name
FROM results res
JOIN races r ON res.raceId = r.raceId
JOIN drivers d ON res.driverId = d.driverId
WHERE d.forename = 'Alex' AND d.surna
- 工具调用: 26 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题中的'track number'实际指driverStandings表中的赛季排名(position)，但模型错误地使用了results表中的比赛完赛名次(position)，导致结果不匹配。

---

## Q903 [moderate] 类别2-查询歧义
- 问题: How many times did Michael Schumacher won from races hosted in Sepang International Circuit?
- 标准 SQL: SELECT SUM(T2.wins) FROM drivers AS T1 INNER JOIN driverStandings AS T2 ON T2.driverId = T1.driverId INNER JOIN races AS T3 ON T3.raceId = T2.raceId I
- 预测 SQL: SELECT COUNT(DISTINCT r.raceId)
FROM results r
JOIN races ra ON r.raceId = ra.raceId
WHERE ra.circuitId = 2
  AND r.driverId = 30
  AND r.points = (
 
- 工具调用: 35 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'won from races' 可以理解为在比赛中获得最高积分（即获胜），但标准SQL使用driverStandings.wins（赛季累计胜场数），而预测SQL使用results.points判断单场获胜，两者对'win'的定义不同。

---

## Q905 [simple] 类别2-查询歧义
- 问题: What is Eddie Irvine's average points scored in year 2000?
- 标准 SQL: SELECT AVG(T2.points) FROM drivers AS T1 INNER JOIN driverStandings AS T2 ON T2.driverId = T1.driverId INNER JOIN races AS T3 ON T3.raceId = T2.raceId
- 预测 SQL: SELECT AVG(rs.points)
FROM results rs
JOIN races r ON rs.raceId = r.raceId
JOIN drivers d ON rs.driverId = d.driverId
WHERE d.surname = 'Irvine' AND d
- 工具调用: 29 次 | Guardrail block: 1 次
- 判定理由: 问题本身有歧义：'average points' 可以指单场比赛的平均积分（results.points）或赛季累计积分的平均值（driverStandings.points），两种理解在语义上都通顺，属于对'points'定义的不同理解。

---

## Q908 [simple] 类别1-探索不足
- 问题: What is the most laps f1 races had? Name the race, year and circuit location where the races with most laps was hosted.
- 标准 SQL: SELECT T3.lap, T2.name, T2.year, T1.location FROM circuits AS T1 INNER JOIN races AS T2 ON T1.circuitId = T2.circuitId INNER JOIN lapTimes AS T3 ON T3
- 预测 SQL: SELECT ra.name AS race, ra.year, c.location || ', ' || c.country AS circuit_location
FROM results r
JOIN races ra ON r.raceId = ra.raceId
JOIN circuit
- 工具调用: 24 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题问的是'most laps'，但golden SQL从lapTimes表取每圈数据（lap列），而模型从results表的laps列（完成圈数）取数据，误解了列含义。

---

## Q921 [simple] 类别2-查询歧义
- 问题: Please give more information about the Formula_1 races that used the Silverstone Circuit.
- 标准 SQL: SELECT DISTINCT T1.url FROM circuits AS T1 INNER JOIN races AS T2 ON T2.circuitID = T1.circuitId WHERE T1.name = 'Silverstone Circuit'
- 预测 SQL: SELECT races.url FROM races JOIN circuits ON races.circuitId = circuits.circuitId WHERE circuits.name = 'Silverstone Circuit'
Agent response (last 500
- 工具调用: 17 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'more information refers to url' 未明确是取所有比赛的url还是去重后的url，模型预测SQL返回51行（所有比赛），而标准SQL用DISTINCT返回1行（唯一url），两者对'url信息'的理解角度不同。

---

## Q922 [simple] 类别2-查询歧义
- 问题: What time did the the 2010's Formula_1 race took place on the Abu Dhabi Circuit?
- 标准 SQL: SELECT T2.date, T2.time FROM circuits AS T1 INNER JOIN races AS T2 ON T2.circuitID = T1.circuitId WHERE T2.year = 2010 AND T2.name = 'Abu Dhabi Grand 
- 预测 SQL: SELECT time FROM races WHERE year = 2010 AND circuitId = (SELECT circuitId FROM circuits WHERE name = 'Yas Marina Circuit')
- 工具调用: 12 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：问题中'Abu Dhabi Circuit'既可以指赛道名称'Yas Marina Circuit'，也可以指比赛名称'Abu Dhabi Grand Prix'，模型选择了通过赛道名查询，而标准SQL通过比赛名查询，两者都是合理的理解方式。

---

## Q927 [simple] 类别2-查询歧义
- 问题: Which driver created the fastest lap speed in a Formula_1 race? Please give both his forename and surname.
- 标准 SQL: SELECT T1.forename, T1.surname FROM drivers AS T1 INNER JOIN results AS T2 ON T2.driverId = T1.driverId WHERE T2.fastestLapTime IS NOT NULL ORDER BY T
- 预测 SQL: SELECT d.forename, d.surname
FROM results r
JOIN drivers d ON r.driverId = d.driverId
WHERE r.fastestLapSpeed IS NOT NULL
ORDER BY CAST(r.fastestLapSp
- 工具调用: 14 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：标准SQL按fastestLapSpeed降序取最高速度，预测SQL也按fastestLapSpeed降序但使用了CAST转换，两者对'fastest lap speed'的理解一致，但标准SQL额外要求fastestLapTime非空，而预测SQL只要求fastestLapSpeed非空，导致可能选出不同行。数据库专家可能认为只需速度非空即可，因此属于理解角度不同。

---

## Q928 [moderate] 类别1-探索不足
- 问题: Which driver ranked the first in the Canadian Grand Prix in 2007? Please give his reference name.
- 标准 SQL: SELECT T3.forename, T3.surname, T3.driverRef FROM races AS T1 INNER JOIN results AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.driverI
- 预测 SQL: SELECT d.driverRef
FROM results r
JOIN drivers d ON r.driverId = d.driverId
JOIN races ra ON r.raceId = ra.raceId
WHERE ra.name = 'Canadian Grand Prix
- 工具调用: 22 次 | Guardrail block: 1 次
- 判定理由: 模型没理解schema：问题要求'ranked the first'，应使用results.rank列（完赛名次），但模型误用了results.positionOrder列（排序用序号），导致查询条件错误。

---

## Q929 [simple] 类别1-探索不足
- 问题: Please list the Formula_1 races that Lewis Hamilton participated.
- 标准 SQL: SELECT T1.name FROM races AS T1 INNER JOIN results AS T2 ON T2.raceId = T1.raceId INNER JOIN drivers AS T3 ON T3.driverId = T2.driverId WHERE T3.foren
- 预测 SQL: SELECT r.name, r.year
FROM races r
JOIN results res ON r.raceId = res.raceId
WHERE res.driverId = 1
Agent response (last 500 chars): 现在我已经充分理解了表结构和关系，
- 工具调用: 21 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：预测SQL返回了Lewis Hamilton所有参赛记录（202行），但问题要求列出他参加过的比赛（应去重，仅26场），模型未正确使用DISTINCT或理解'participated'需要去重。

---

## Q930 [simple] 类别1-探索不足
- 问题: In which Formula_1 race did Lewis Hamilton rank the highest?
- 标准 SQL: SELECT name FROM races WHERE raceId IN ( SELECT raceId FROM results WHERE rank = 1 AND driverId = ( SELECT driverId FROM drivers WHERE forename = 'Lew
- 预测 SQL: SELECT races.name
FROM results
JOIN drivers ON results.driverId = drivers.driverId
JOIN races ON results.raceId = races.raceId
WHERE drivers.forename 
- 工具调用: 26 次 | Guardrail block: 1 次
- 判定理由: 模型没理解schema：模型误以为rank列中最小值（min(rank)）对应最高排名，但实际rank=1才是最快圈速排名（Fastest Lap），rank=0表示未排名，导致只返回一条记录而非全部rank=1的16场比赛。

---

## Q931 [moderate] 类别2-查询歧义
- 问题: What was the fastest lap speed among all drivers in the 2009 Spanish Grand Prix?
- 标准 SQL: SELECT T2.fastestLapSpeed FROM races AS T1 INNER JOIN results AS T2 ON T2.raceId = T1.raceId WHERE T1.name = 'Spanish Grand Prix' AND T1.year = 2009 A
- 预测 SQL: SELECT MAX(CAST(results.fastestLapSpeed AS REAL))
FROM races
JOIN results ON races.raceId = results.raceId
WHERE races.year = 2009 AND races.name = 'S
- 工具调用: 17 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：标准SQL通过ORDER BY和LIMIT 1取具体的fastestLapSpeed值，而预测SQL用MAX聚合函数，两者在语义上都合理，只是对'fastest lap speed'的理解角度不同。

---

## Q937 [simple] 类别2-查询歧义
- 问题: What's the finish time for the driver who ranked second in 2008's AustChineseralian Grand Prix?
- 标准 SQL: SELECT T1.time FROM results AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId WHERE T1.rank = 2 AND T2.name = 'Chinese Grand Prix' AND T2.year = 2
- 预测 SQL: SELECT r.time
FROM results r
JOIN races ra ON r.raceId = ra.raceId
JOIN drivers d ON r.driverId = d.driverId
WHERE ra.year = 2008 AND ra.name = 'Chine
- 工具调用: 21 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'finish time'在F1语境中既可指完赛名次对应的'position'，也可指比赛用时差距'time'，标准SQL用rank=2而预测SQL用position=2，两者对'ranked second'的理解不同。

---

## Q943 [moderate] 类别2-查询歧义
- 问题: What is the rate of drivers completing all the laps in the 2008 Australian Grand Prix?
- 标准 SQL: SELECT CAST(SUM(IIF(T1.time IS NOT NULL, 1, 0)) AS REAL) * 100 / COUNT(T1.resultId) FROM results AS T1 INNER JOIN races AS T2 ON T1.raceId = T2.raceId
- 预测 SQL: SELECT CAST(COUNT(CASE WHEN time IS NOT NULL THEN 1 END) AS REAL) / COUNT(*) AS rate
FROM results
WHERE raceId = 18
- 工具调用: 11 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：标准SQL将rate乘以100输出百分比，而预测SQL输出小数，两者对'rate'的理解不同，且evidence未明确指定输出格式。

---

## Q949 [simple] 类别2-查询歧义
- 问题: Which constructor has the highest point?
- 标准 SQL: SELECT T2.name FROM constructorStandings AS T1 INNER JOIN constructors AS T2 on T1.constructorId = T2.constructorId ORDER BY T1.points DESC LIMIT 1
- 预测 SQL: SELECT c.name
FROM constructorResults cr
JOIN constructors c ON cr.constructorId = c.constructorId
GROUP BY c.constructorId
ORDER BY SUM(cr.points) DE
- 工具调用: 28 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'highest point'可以指单场最高积分（constructorResults.points）或赛季累计最高积分（constructorStandings.points），两种理解在语义上都通顺，数据库专家也可能选择不同表。

---

## Q950 [simple] 类别2-查询歧义
- 问题: Please list the constructor names with 0 points at race 291.
- 标准 SQL: SELECT T2.name FROM constructorStandings AS T1 INNER JOIN constructors AS T2 on T1.constructorId = T2.constructorId WHERE T1.points = 0 AND T1.raceId 
- 预测 SQL: SELECT c.name
FROM constructorResults cr
JOIN constructors c ON cr.constructorId = c.constructorId
WHERE cr.raceId = 291 AND cr.points = 0
- 工具调用: 19 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'0 points at race 291'既可以理解为单场比赛得分为0（从constructorResults取单场积分），也可以理解为赛季累计积分为0（从constructorStandings取赛季累计积分），两种理解在语义上都通顺。

---

## Q951 [simple] 类别1-探索不足
- 问题: How many Japanese constructors have 0 points in 2 races?
- 标准 SQL: SELECT COUNT(T1.raceId) FROM constructorStandings AS T1 INNER JOIN constructors AS T2 on T1.constructorId = T2.constructorId WHERE T1.points = 0 AND T
- 预测 SQL: SELECT COUNT(*) 
FROM (
    SELECT c.constructorId
    FROM constructors c
    JOIN constructorResults cr ON c.constructorId = cr.constructorId
    WH
- 工具调用: 24 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题中的'0 points'应指constructorStandings（赛季累计积分）中的points=0，但模型错误地使用了constructorResults（单场积分）并计算SUM(cr.points)=0，导致语义错误。

---

## Q952 [simple] 类别2-查询歧义
- 问题: Which constructors have been ranked 1?
- 标准 SQL: SELECT DISTINCT T2.name FROM results AS T1 INNER JOIN constructors AS T2 on T1.constructorId = T2.constructorId WHERE T1.rank = 1
- 预测 SQL: SELECT DISTINCT c.name
FROM constructorStandings cs
JOIN constructors c ON cs.constructorId = c.constructorId
WHERE cs.position = 1
- 工具调用: 14 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'ranked 1'可以指车队在赛季积分榜中排名第一（constructorStandings.position=1），也可以指车手在比赛中获得第一名（results.rank=1），两种理解在语义上都通顺。

---

## Q955 [challenging] 类别1-探索不足
- 问题: What is the average time in seconds of champion for each year, before year 1975?
- 标准 SQL: WITH time_in_seconds AS ( SELECT T2.year, T2.raceId, T1.positionOrder, CASE WHEN T1.positionOrder = 1 THEN (CAST(SUBSTR(T1.time, 1, 1) AS REAL) * 3600
- 预测 SQL: SELECT r.year, AVG(res.milliseconds / 1000.0) AS avg_time_seconds
FROM results res
JOIN races r ON res.raceId = r.raceId
WHERE r.year < 1975 AND res.p
- 工具调用: 32 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求计算冠军完赛时间（time列，文本格式如'1:37:04.470'），但模型错误地使用了milliseconds列（比赛用时毫秒数，约75%为NULL），导致数据源错误。

---

## Q956 [simple] 类别1-探索不足
- 问题: Which drivers born after 1975 have been ranked 2? Please give their forenames and surnames.
- 标准 SQL: SELECT T2.forename, T2.surname FROM results AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId WHERE STRFTIME('%Y', T2.dob) > '1975' AND T1.r
- 预测 SQL: SELECT DISTINCT d.forename, d.surname
FROM drivers d
JOIN results r ON d.driverId = r.driverId
WHERE r.position = 2
AND CAST(strftime('%Y', d.dob) AS 
- 工具调用: 22 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：将'ranked 2'误解为results.position=2（完赛名次），而实际应使用results.rank=2（最快圈速排名），这是对列含义的错误理解。

---

## Q957 [simple] 类别2-查询歧义
- 问题: How many Italian drivers haven't finished the race?
- 标准 SQL: SELECT COUNT(T1.driverId) FROM results AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId WHERE T2.nationality = 'Italian' AND T1.time IS NUL
- 预测 SQL: SELECT COUNT(DISTINCT d.driverId)
FROM drivers d
JOIN results r ON d.driverId = r.driverId
WHERE d.nationality = 'Italian'
  AND r.time IS NULL
Agent 
- 工具调用: 15 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'how many Italian drivers haven't finished the race' 可以理解为统计有多少个不同的意大利车手（COUNT DISTINCT driverId），也可以理解为统计所有未完成比赛的记录数（COUNT driverId），两种理解在语义上都通顺。

---

## Q958 [moderate] 类别2-查询歧义
- 问题: Which driver has the fastest lap time? Please give their forenames and surnames.
- 标准 SQL: SELECT T2.forename, T2.surname, T1.fastestLapTime FROM results AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId WHERE T1.fastestLapTime IS 
- 预测 SQL: SELECT d.forename, d.surname
FROM lapTimes l
JOIN drivers d ON l.driverId = d.driverId
WHERE l.milliseconds = (SELECT MIN(milliseconds) FROM lapTimes)
- 工具调用: 16 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'fastest lap time'既可以理解为单圈最快时间（lapTimes.milliseconds），也可以理解为比赛中的最快单圈记录（results.fastestLapTime），两种理解在语义上都通顺。

---

## Q959 [simple] 类别2-查询歧义
- 问题: What is the fastest lap number of the champion in 2009?
- 标准 SQL: SELECT T1.fastestLap FROM results AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId WHERE T2.year = 2009 AND T1.time LIKE '_:%:__.___'
- 预测 SQL: SELECT lt.lap
FROM lapTimes lt
JOIN races r ON lt.raceId = r.raceId
JOIN (
    SELECT ds.driverId
    FROM driverStandings ds
    JOIN races r2 ON ds.
- 工具调用: 35 次 | Guardrail block: 2 次
- 判定理由: 问题本身有歧义：'fastest lap number of the champion' 可以理解为冠军车手在全年所有比赛中的最快单圈圈数（从 lapTimes 表取），也可以理解为冠军车手在每场比赛中获得最快圈速的圈数（从 results.fastestLap 取），两种理解在语义上都通顺。

---

## Q962 [challenging] 类别1-探索不足
- 问题: From 2000 to 2005, what percentage of drivers who were born before 1985 and the lap numbers were over 50?
- 标准 SQL: SELECT CAST(SUM(IIF(STRFTIME('%Y', T3.dob) < '1985' AND T1.laps > 50, 1, 0)) AS REAL) * 100 / COUNT(*) FROM results AS T1 INNER JOIN races AS T2 on T1
- 预测 SQL: SELECT CAST(COUNT(DISTINCT CASE WHEN CAST(SUBSTR(d.dob,1,4) AS INTEGER) < 1985 AND lt.lap > 50 THEN d.driverId END) AS REAL) * 100 / COUNT(DISTINCT d.
- 工具调用: 24 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题中的'lap numbers'指的是results表中的'laps'列（比赛完成圈数），而非lapTimes表中的圈序号，模型错误地使用了lapTimes表。

---

## Q963 [moderate] 类别1-探索不足
- 问题: How many French drivers who obtain the laptime less than 02:00.00?
- 标准 SQL: SELECT COUNT(T1.driverId) FROM drivers AS T1 INNER JOIN lapTimes AS T2 on T1.driverId = T2.driverId WHERE T1.nationality = 'French' AND (CAST(SUBSTR(T
- 预测 SQL: SELECT COUNT(DISTINCT d.driverId) 
FROM drivers d 
JOIN lapTimes lt ON d.driverId = lt.driverId 
WHERE d.nationality = 'French' 
  AND lt.milliseconds
- 工具调用: 15 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题中的'laptime less than 02:00.00'应解析为文本格式的圈时（如1:59.123）转换为秒数小于120，但模型错误地使用了毫秒列（milliseconds < 120000），导致过滤条件不准确。

---

## Q966 [simple] 类别2-查询歧义
- 问题: How many driver participated in race ID number 18?
- 标准 SQL: SELECT COUNT(driverId) FROM driverStandings WHERE raceId = 18
- 预测 SQL: SELECT COUNT(DISTINCT driverId) FROM results WHERE raceId = 18
- 工具调用: 9 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'participated'可以理解为'参加了比赛'（从results表统计）或'在积分榜中有记录'（从driverStandings表统计），两种理解在语义上都通顺，属于对问题的不同解读。

---

## Q967 [simple] 类别1-探索不足
- 问题: State code numbers of top 3 yougest drivers. How many Netherlandic drivers among them?
- 标准 SQL: SELECT COUNT(*) FROM ( SELECT T1.nationality FROM drivers AS T1 ORDER BY JULIANDAY(T1.dob) DESC LIMIT 3) AS T3 WHERE T3.nationality = 'Dutch'
- 预测 SQL: SELECT code FROM drivers ORDER BY dob DESC LIMIT 3
Agent response (last 500 chars): Now I have a complete picture. The top 3 youngest drivers are: Lan
- 工具调用: 13 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema，问题要求统计前3年轻车手中荷兰籍的数量，但模型只返回了车手代码，完全忽略了COUNT和国籍过滤条件。

---

## Q970 [moderate] 类别2-查询歧义
- 问题: List out top 3 German drivers who were born from 1980-1990 and have the earliest lap time.
- 标准 SQL: SELECT T2.driverId FROM pitStops AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId WHERE T2.nationality = 'German' AND STRFTIME('%Y', T2.dob
- 预测 SQL: SELECT d.forename, d.surname, MIN(l.milliseconds)
FROM drivers d
JOIN lapTimes l ON d.driverId = l.driverId
WHERE d.nationality = 'German'
  AND CAST(
- 工具调用: 21 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'earliest lap time'可理解为单圈最快时间（lapTimes.milliseconds）或进站耗时最短（pitStops.time），两种理解在语义上都通顺，属于自然语言歧义导致的错误。

---

## Q973 [moderate] 类别2-查询歧义
- 问题: List out top 10 Spanish drivers who were born before 1982 and have the latest lap time.
- 标准 SQL: SELECT T2.driverId FROM pitStops AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId WHERE T2.nationality = 'Spanish' AND STRFTIME('%Y', T2.do
- 预测 SQL: SELECT d.forename, d.surname
FROM drivers d
JOIN lapTimes l ON d.driverId = l.driverId
WHERE d.nationality = 'Spanish'
  AND CAST(strftime('%Y', d.dob
- 工具调用: 20 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'latest lap time' 可以理解为单圈用时（lapTimes.milliseconds）或进站时间（pitStops.time），两种理解在语义上都通顺，属于对问题中'latest lap time'的不同解释。

---

## Q974 [simple] 类别2-查询歧义
- 问题: State the racing year which has the fastest lap time?
- 标准 SQL: SELECT T2.year FROM results AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId WHERE T1.fastestLapTime IS NOT NULL
- 预测 SQL: SELECT r.year
FROM races r
JOIN results res ON r.raceId = res.raceId
WHERE res.fastestLapTime IS NOT NULL
ORDER BY 
  CAST(substr(res.fastestLapTime, 
- 工具调用: 17 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'has the fastest lap time'可以理解为找出最快圈速对应的年份（即单条最快圈速的年份），也可以理解为找出所有存在最快圈速记录的年份。标准SQL返回所有非空最快圈速的年份（14行），预测SQL返回最快圈速值最小的年份（1行），两者对问题的理解不同。

---

## Q975 [simple] 类别1-探索不足
- 问题: Which year has the lowest speed of lap time?
- 标准 SQL: SELECT T2.year FROM lapTimes AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId ORDER BY T1.time DESC LIMIT 1
- 预测 SQL: SELECT r.year 
FROM lapTimes lt 
JOIN races r ON lt.raceId = r.raceId 
ORDER BY lt.milliseconds DESC 
LIMIT 1
- 工具调用: 15 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题中的'lowest speed of lap time'应使用文本列time（单圈用时）排序，但模型错误地使用了毫秒列milliseconds，导致结果不同。

---

## Q976 [simple] 类别2-查询歧义
- 问题: List the driver's ID of the top five driver, by descending order, the fastest time during the first lap of the race.
- 标准 SQL: SELECT driverId FROM lapTimes WHERE lap = 1 ORDER BY time LIMIT 5
- 预测 SQL: SELECT driverId
FROM lapTimes
WHERE lap = 1
GROUP BY driverId
ORDER BY MIN(milliseconds) DESC
LIMIT 5
- 工具调用: 16 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'the fastest time during the first lap of the race' 可以理解为每位车手在第一圈的最快时间（需GROUP BY driverId），也可以理解为所有车手第一圈记录中时间最小的5条（无需GROUP BY）。两种理解在语义上都通顺，属于自然语言歧义。

---

## Q979 [simple] 类别2-查询歧义
- 问题: What race number has the most finishers?
- 标准 SQL: SELECT raceId FROM results GROUP BY raceId ORDER BY COUNT(time IS NOT NULL) DESC LIMIT 1
- 预测 SQL: SELECT raceId
FROM results
WHERE time IS NOT NULL
GROUP BY raceId
ORDER BY COUNT(*) DESC
LIMIT 1
- 工具调用: 19 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：标准SQL用COUNT(time IS NOT NULL)统计完赛车手数，预测SQL用WHERE time IS NOT NULL过滤后COUNT(*)，两者对'finisher'的定义一致但统计方式不同，导致结果可能不同（如某场比赛所有车手time都不为NULL时，COUNT(*)与COUNT(time IS NOT NULL)等价，但若存在NULL则COUNT(*)会少算）。实际上两种写法在语义上都合理，属于对'most finishers'统计方式的理解差异。

---

## Q983 [simple] 类别1-探索不足
- 问题: Which of the Italian constructor got the highest point to date? Give its introduction website?
- 标准 SQL: SELECT T1.url FROM constructors AS T1 INNER JOIN constructorStandings AS T2 on T1.constructorId = T2.constructorId WHERE T1.nationality = 'Italian' OR
- 预测 SQL: SELECT c.name, c.url
FROM constructors c
JOIN constructorResults cr ON c.constructorId = cr.constructorId
WHERE c.nationality = 'Italian'
GROUP BY c.c
- 工具调用: 25 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求'to date'（至今累计最高分），应使用constructorStandings（赛季累计积分）而非constructorResults（单场积分），模型未正确理解points列的单场vs累计语义。

---

## Q984 [simple] 类别2-查询歧义
- 问题: What is the website of the constructor who tallied the most total wins.
- 标准 SQL: SELECT T1.url FROM constructors AS T1 INNER JOIN constructorStandings AS T2 on T1.constructorId = T2.constructorId ORDER BY T2.wins DESC LIMIT 1
- 预测 SQL: SELECT c.url
FROM constructors c
JOIN (
    SELECT per_season.constructorId, SUM(per_season.season_wins) AS total_wins
    FROM (
        SELECT cs.co
- 工具调用: 24 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'total wins'可以理解为所有赛季的累计获胜次数（预测SQL按年份取MAX再SUM），也可以理解为单赛季最高获胜次数（标准SQL直接取wins列最大值），两种理解在语义上都通顺。

---

## Q985 [simple] 类别1-探索不足
- 问题: Among the drivers who participated in the French Grand Prix, who has the slowest time in the 3rd lap.
- 标准 SQL: SELECT T1.driverId FROM lapTimes AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId WHERE T2.name = 'French Grand Prix' AND T1.lap = 3 ORDER BY T1.
- 预测 SQL: SELECT d.forename, d.surname
FROM drivers d
JOIN lapTimes lt ON d.driverId = lt.driverId
JOIN races r ON lt.raceId = r.raceId
WHERE r.name = 'French G
- 工具调用: 23 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：Golden SQL使用lapTimes.time（文本格式）排序，而预测SQL使用了lapTimes.milliseconds（毫秒数值），两者排序结果不同，说明模型未正确理解列的含义。

---

## Q986 [simple] 类别2-查询歧义
- 问题: In which race did the fastest 1st lap time was recorded? Please indicate the time in milliseconds.
- 标准 SQL: SELECT T1.milliseconds FROM lapTimes AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId WHERE T1.lap = 1 ORDER BY T1.time LIMIT 1
- 预测 SQL: SELECT r.name, lt.milliseconds
FROM lapTimes lt
JOIN races r ON lt.raceId = r.raceId
WHERE lt.lap = 1
ORDER BY lt.milliseconds ASC
LIMIT 1
- 工具调用: 17 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：标准SQL按time（文本格式）排序，预测SQL按milliseconds（数值格式）排序，两者都是合理的排序方式，但问题未明确指定按哪个字段排序。

---

## Q987 [simple] 类别1-探索不足
- 问题: What is the average fastest lap time of the top 10 drivers in the 2006 United States Grand Prix?
- 标准 SQL: SELECT AVG(T1.fastestLapTime) FROM results AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId WHERE T1.rank < 11 AND T2.year = 2006 AND T2.name = '
- 预测 SQL: SELECT AVG(CAST(SUBSTR(r.fastestLapTime, 1, INSTR(r.fastestLapTime, ':') - 1) AS REAL) * 60 + CAST(SUBSTR(r.fastestLapTime, INSTR(r.fastestLapTime, ':
- 工具调用: 23 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题中的'top 10 drivers'应使用results.rank列（最快圈速排名<11），但模型错误地使用了positionOrder列（完赛名次），这是对列含义的误解。

---

## Q988 [challenging] 类别2-查询歧义
- 问题: List down top 3 German drivers who has the shortest average pit stop duration and were born between 1980-1985.
- 标准 SQL: SELECT T2.forename, T2.surname FROM pitStops AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId WHERE T2.nationality = 'German' AND STRFTIME(
- 预测 SQL: SELECT d.forename || ' ' || d.surname AS full_name
FROM drivers d
JOIN pitStops p ON d.driverId = p.driverId
WHERE d.nationality = 'German'
  AND CAST
- 工具调用: 28 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：标准SQL用AVG(T1.duration)直接计算平均进站耗时，预测SQL用SUM(CAST(p.duration AS REAL))/COUNT(p.duration)手动计算平均值，两者在数学上等价，但预测SQL将duration从TEXT转为REAL再计算，而标准SQL直接对TEXT类型使用AVG，SQLite的AVG对TEXT会隐式转换为数值，结果一致。实际差异在于预测SQL返回full_name拼接字段，标准SQL返回分开的forename和surname，但问题要求'List down top 3 German drivers'，两种输出格式均可接受，属于对输出格式的不同理解。

---

## Q989 [moderate] 类别2-查询歧义
- 问题: Who is the champion of the Canadian Grand Prix in 2008? Indicate his finish time.
- 标准 SQL: SELECT T1.time FROM results AS T1 INNER JOIN races AS T2 ON T1.raceId = T2.raceId WHERE T2.name = 'Canadian Grand Prix' AND T2.year = 2008 AND T1.time
- 预测 SQL: SELECT d.forename || ' ' || d.surname AS champion, r.time AS finish_time
FROM races ra
JOIN results r ON ra.raceId = r.raceId
JOIN drivers d ON r.driv
- 工具调用: 19 次 | Guardrail block: 1 次
- 判定理由: 问题本身有歧义：标准SQL只要求返回冠军的finish_time，而预测SQL额外返回了车手姓名，两者对'Indicate his finish time'的理解不同，但都合理。

---

## Q992 [moderate] 类别2-查询歧义
- 问题: Find the full name, Wiki Pedia page link, and date of birth of German drivers born between 1971 and 1985. List it in descending order of date of birth.
- 标准 SQL: SELECT forename, surname, url, dob FROM drivers WHERE nationality = 'German' AND STRFTIME('%Y', dob) BETWEEN '1971' AND '1985' ORDER BY dob DESC
- 预测 SQL: SELECT forename || ' ' || surname, url, dob FROM drivers WHERE nationality = 'German' AND CAST(strftime('%Y', dob) AS INTEGER) BETWEEN 1971 AND 1985 O
- 工具调用: 13 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：标准SQL要求分别输出forename和surname，而预测SQL拼接为全名，两者都是对'full name'的合理理解，且查询结果行数相同。

---

## Q993 [simple] 类别2-查询歧义
- 问题: In which location does the Hungaroring circuit located? Also, find the country and coordinates of this circuit?
- 标准 SQL: SELECT country, lat, lng FROM circuits WHERE name = 'Hungaroring'
- 预测 SQL: SELECT location, country, lat, lng FROM circuits WHERE name = 'Hungaroring'
- 工具调用: 12 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：问题明确问'In which location does the Hungaroring circuit located?'，location可以指城市（location列）也可以指赛道名称，标准SQL只返回country、lat、lng，而预测SQL额外返回了location列，两者对'location'的理解不同，但都合理。

---

## Q995 [moderate] 类别2-查询歧义
- 问题: What is the average score of Lewis Hamilton among all the Turkish Grand Prix?
- 标准 SQL: SELECT AVG(T2.points) FROM drivers AS T1 INNER JOIN driverStandings AS T2 ON T1.driverId = T2.driverId INNER JOIN races AS T3 ON T3.raceId = T2.raceId
- 预测 SQL: SELECT AVG(res.points) FROM results res JOIN races ra ON res.raceId = ra.raceId JOIN drivers d ON res.driverId = d.driverId WHERE d.forename = 'Lewis'
- 工具调用: 23 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'average score' 可以指单场比赛的平均积分（results.points）或赛季累计平均积分（driverStandings.points），两种理解在语义上都通顺，数据库专家也可能选择不同的表。

---

## Q996 [simple] 类别2-查询歧义
- 问题: What is the annual average number of races held during the first 10 years of the 21st century?
- 标准 SQL: SELECT CAST(SUM(CASE WHEN year BETWEEN 2000 AND 2010 THEN 1 ELSE 0 END) AS REAL) / 10 FROM races WHERE date BETWEEN '2000-01-01' AND '2010-12-31'
- 预测 SQL: SELECT CAST(COUNT(*) AS REAL) / COUNT(DISTINCT year) FROM races WHERE date BETWEEN '2000-01-01' AND '2010-12-31'
Agent response (last 500 chars): 现在我对
- 工具调用: 9 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'first 10 years of the 21st century' 可以理解为2000-2009（10年）或2000-2010（11年），且'annual average'的计算方式（除以年数还是除以不同年份数）存在不同理解。模型预测的SQL除以COUNT(DISTINCT year)得到的是11年的平均值，而标准SQL除以10得到的是10年的平均值，两者在语义上都通顺。

---

## Q998 [simple] 类别2-查询歧义
- 问题: In terms of number of points acquired, how many victories did the driver who ranked 91st acquired?
- 标准 SQL: SELECT SUM(CASE WHEN points = 91 THEN wins ELSE 0 END) FROM driverStandings
- 预测 SQL: SELECT COUNT(*) 
FROM results 
WHERE driverId = (
    SELECT driverId 
    FROM results 
    GROUP BY driverId 
    ORDER BY SUM(points) DESC 
    LIM
- 工具调用: 50 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'91st refers to points' 可理解为按积分排名第91位（标准SQL用driverStandings的points列直接取wins），也可理解为按总积分排序取第91名车手（预测SQL用results聚合）。两种理解在语义上都通顺，属于对'排名'定义的不同角度。

---

## Q1000 [simple] 类别2-查询歧义
- 问题: Which racetrack hosted the most recent race? Indicate the full location.
- 标准 SQL: SELECT T1.location FROM circuits AS T1 INNER JOIN races AS T2 ON T1.circuitId = T2.circuitId ORDER BY T2.date DESC LIMIT 1
- 预测 SQL: SELECT c.name, c.location || ', ' || c.country AS full_location
FROM races r
JOIN circuits c ON r.circuitId = c.circuitId
ORDER BY r.date DESC
LIMIT 1
- 工具调用: 19 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：evidence 要求 'full location refers to location+country'，但标准 SQL 只返回 location，而预测 SQL 返回了 name 和拼接的 location+country，两者对 'full location' 的理解不同，且模型按 evidence 拼接是合理的。

---

## Q1002 [moderate] 类别2-查询歧义
- 问题: As of the present, what is the full name of the youngest racer? Indicate her nationality and the name of the race to which he/she first joined.
- 标准 SQL: SELECT T1.forename, T1.surname, T1.nationality, T3.name FROM drivers AS T1 INNER JOIN driverStandings AS T2 on T1.driverId = T2.driverId INNER JOIN ra
- 预测 SQL: SELECT d.forename || ' ' || d.surname, d.nationality, r.name
FROM drivers d
JOIN results res ON d.driverId = res.driverId
JOIN races r ON res.raceId =
- 工具调用: 20 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'first joined'可以理解为首次参加任何F1比赛（results表）或首次进入车手积分榜（driverStandings表），两种理解在语义上都通顺，属于对问题的不同解读。

---

## Q1004 [simple] 类别2-查询歧义
- 问题: How many wins was achieved by the oldest racer? Indicate his/her full name.
- 标准 SQL: SELECT SUM(T1.wins),T2.forename, T2.surname FROM driverStandings AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId ORDER BY T2.dob ASC LIMIT
- 预测 SQL: SELECT d.forename, d.surname, COUNT(r.position) AS wins
FROM drivers d
LEFT JOIN results r ON d.driverId = r.driverId AND r.position = 1
WHERE d.dob =
- 工具调用: 19 次 | Guardrail block: 0 次
- 判定理由: 问题本身有歧义：'wins'可以指比赛获胜次数（从results表统计position=1），也可以指赛季积分榜冠军次数（从driverStandings表统计wins列），两种理解在语义上都通顺。

---

## Q1005 [simple] 类别1-探索不足
- 问题: What was the longest time a driver had ever spent at a pit stop?
- 标准 SQL: SELECT duration FROM pitStops ORDER BY duration DESC LIMIT 1
- 预测 SQL: SELECT MAX(milliseconds) FROM pitStops
Agent response (last 500 chars): 现在已经充分理解了 pitStops 表的结构。让我写出最终 SQL。

关键发现：
- `pitStops` 表有 `duration`（TEXT，格式不
- 工具调用: 16 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：evidence明确要求返回duration列，但模型误以为duration是文本格式无法正确排序，转而使用milliseconds列，导致输出列不匹配。

---

## Q1006 [challenging] 类别1-探索不足
- 问题: Among all the lap records set on various circuits, what is the time for the fastest one?
- 标准 SQL: SELECT time FROM lapTimes ORDER BY (CASE WHEN INSTR(time, ':') <> INSTR(SUBSTR(time, INSTR(time, ':') + 1), ':') + INSTR(time, ':') THEN CAST(SUBSTR(t
- 预测 SQL: SELECT time FROM lapTimes ORDER BY milliseconds ASC LIMIT 1
- 工具调用: 15 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：lapTimes.milliseconds列是单圈用时（毫秒），与time列直接对应，但golden SQL要求对time文本进行复杂解析排序，模型未意识到time列格式需要转换才能正确排序，而直接使用milliseconds列虽然逻辑等价但被判定为错误。

---

## Q1009 [simple] 类别1-探索不足
- 问题: Please list the time each driver spent at the pit stop during the 2011 Australian Grand Prix.
- 标准 SQL: SELECT T1.duration FROM pitStops AS T1 INNER JOIN races AS T2 on T1.raceId = T2.raceId WHERE T2.year = 2011 AND T2.name = 'Australian Grand Prix'
- 预测 SQL: SELECT d.forename, d.surname, SUM(ps.duration)
FROM pitStops ps
JOIN drivers d ON d.driverId = ps.driverId
WHERE ps.raceId = 841
GROUP BY d.driverId
A
- 工具调用: 20 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求列出每位车手在进站中的耗时（duration），但预测SQL使用了SUM聚合，将多次进站的耗时相加，而标准SQL直接列出每条进站记录。模型误解了'each driver spent at the pit stop'的含义，认为需要汇总而非逐条列出。

---

## Q1010 [simple] 类别1-探索不足
- 问题: What is the lap record set by Lewis Hamilton in a Formula_1 race?
- 标准 SQL: SELECT T1.time FROM lapTimes AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId WHERE T2.forename = 'Lewis' AND T2.surname = 'Hamilton'
- 预测 SQL: SELECT MIN(time)
FROM lapTimes
WHERE driverId = 1
Agent response (last 500 chars): 基于对数据库结构的充分理解，Lewis Hamilton 的 driverId = 1，最快圈速记录存储在 `lapTimes` 表中
- 工具调用: 25 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求返回所有圈速记录（lap record），但模型错误地使用了MIN聚合函数只返回最快的一条，说明模型未正确理解'lap record'的含义是全部圈速数据而非最快单圈。

---

## Q1011 [challenging] 类别1-探索不足
- 问题: Which top 20 driver created the shortest lap time ever record in a Formula_1 race? Please give them full names.
- 标准 SQL: WITH lap_times_in_seconds AS (SELECT driverId, (CASE WHEN SUBSTR(time, 1, INSTR(time, ':') - 1) <> '' THEN CAST(SUBSTR(time, 1, INSTR(time, ':') - 1) 
- 预测 SQL: SELECT d.forename, d.surname
FROM lapTimes lt
JOIN drivers d ON lt.driverId = d.driverId
GROUP BY d.driverId
ORDER BY MIN(lt.milliseconds) ASC
LIMIT 2
- 工具调用: 20 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求基于'time'列（文本格式）计算最短圈速，但模型错误地使用了'milliseconds'列（毫秒格式），虽然两者数值相关但golden SQL明确要求解析文本时间，属于对列含义理解错误。

---

## Q1012 [simple] 类别2-查询歧义
- 问题: What was the position of the circuits during Lewis Hamilton's fastest lap in a Formula_1 race?
- 标准 SQL: SELECT T1.position FROM lapTimes AS T1 INNER JOIN drivers AS T2 on T1.driverId = T2.driverId WHERE T2.forename = 'Lewis' AND T2.surname = 'Hamilton' O
- 预测 SQL: SELECT lt.position
FROM lapTimes lt
JOIN drivers d ON lt.driverId = d.driverId
WHERE d.driverRef = 'hamilton'
ORDER BY lt.milliseconds ASC
LIMIT 1
Age
- 工具调用: 36 次 | Guardrail block: 1 次
- 判定理由: 问题本身有歧义：问题中的'fastest lap'在evidence中定义为'MIN(time)'，但模型使用了'milliseconds'列来排序，而lapTimes表中同时存在'time'（文本格式）和'milliseconds'（毫秒）两列，两者都表示单圈用时，数据库专家也可能选择'milliseconds'列来计算最快圈速。

---

## Q1013 [simple] 类别1-探索不足
- 问题: What is the lap record for the Austrian Grand Prix Circuit?
- 标准 SQL: WITH fastest_lap_times AS ( SELECT T1.raceId, T1.fastestLapTime FROM results AS T1 WHERE T1.FastestLapTime IS NOT NULL) SELECT MIN(fastest_lap_times.f
- 预测 SQL: SELECT MIN(lt.milliseconds) AS lap_record
FROM lapTimes lt
JOIN races r ON lt.raceId = r.raceId
WHERE r.circuitId IN (23, 70)
Agent response (last 500
- 工具调用: 34 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题要求的是Austrian Grand Prix的圈速记录，但模型错误地从lapTimes表取单圈用时，而正确答案应从results表的fastestLapTime列获取，说明模型未正确理解fastestLapTime列的含义和所在表。

---

## Q1014 [challenging] 类别1-探索不足
- 问题: Please list the lap records for the circuits in Italy.
- 标准 SQL: WITH fastest_lap_times AS (SELECT T1.raceId, T1.FastestLapTime, (CAST(SUBSTR(T1.FastestLapTime, 1, INSTR(T1.FastestLapTime, ':') - 1) AS REAL) * 60) +
- 预测 SQL: SELECT c.name AS circuit_name,
       MIN(lt.milliseconds) AS fastest_lap_ms
FROM circuits c
JOIN races r ON c.circuitId = r.circuitId
JOIN lapTimes l
- 工具调用: 27 次 | Guardrail block: 0 次
- 判定理由: 模型没理解schema：问题中的'lap record'指的是results表中的FastestLapTime（比赛最快圈速），但模型错误地使用了lapTimes表（每圈计时数据），导致结果错误。

---
