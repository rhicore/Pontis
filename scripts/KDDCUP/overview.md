
Data Agent icon
KDD Cup 2026: Data Agents
KDD Cup 2026：数据代理
News  新闻
Overview  概述
Benchmark  基准
Tracks  曲目
Evaluation  评估
Timeline  时间线
Prizes  奖项
Committee  委员会
Community  社区
Submit  提交
Rules  规则
Leaderboard  排行榜
KDD Cup 2026 · Official Competition  
Data Agents
for Complex
Data Analysis
数据代理用于复杂数据分析
Build autonomous AI agents that decompose complex analytical questions, orchestrate multi-step reasoning over heterogeneous data sources, and deliver accurate answers.  

Prizes  奖项
Leaderboard + Creative  排行榜 + 创意
Competition  竞争
Mar 15 – Aug 9, 2026 (AoE)
2026 年 3 月 15 日 – 8 月 9 日 (AoE)
Submit
  提交
Docker image submission  Docker 镜像提交
Learn More
  了解更多
News
News & Updates
Latest announcements and important updates. All dates follow AoE (UTC-12).

May 18, 2026 (AoE)
Announcement
Phase-1 to Phase-2 Qualification Rules
Phase-1 A-board submissions close May 21, 2026, 19:59 Beijing (May 20, 23:59 AoE). The Top 60 teams by weighted A/B scores qualify for Phase-2; Top 1–40 may choose Leaderboard or Creative track, Top 41–60 Creative only. Open the full announcement for tie-breaking and deadlines.

Read full announcement
May 18, 2026 (AoE)
Announcement
Phase-1 B-board Evaluation Instructions
B-board final submission opens when A-board closes (May 21, 19:59 Beijing), with deadline May 23, 19:59 Beijing (May 22, 23:59 AoE), one email per team, 12-hour B-board runtime, and internal evaluation May 23–26. See the full announcement for the email template and rules.

Read full announcement
May 18, 2026 (AoE)
Announcement
Final Submission Checklist Reminder
Before each submission, verify naming, docker save (not export), linux/amd64, Drive permissions, /output layout, valid CSV, A-board 2h / B-board 12h limits, and 16 CPU / 64 GB resource planning. Full checklist in the announcement.

Read full announcement
May 10, 2026 (AoE)
Update
Phase 1 Task Difficulty Distribution (A/B Boards)
We publish the difficulty breakdown for the Phase 1 A-board (57 tasks) and B-board (324 tasks) subsets—counts and shares by Easy / Medium / Hard / Extreme—to help teams estimate evaluation load. The full tables and the canonical Rules reference are in the announcement.

Read full announcement
May 3, 2026 (AoE)
Announcement
Pre-Submission Checklist
Following first-round evaluation, the organizers ask all teams to verify naming (image & tar.gz), export images with docker save (not docker export), target linux/amd64, Google Drive downloadability, strict /output/task_<id>/prediction.csv layout, and parseable CSV outputs. Only emails from the registered team leader are accepted for submissions—see the full announcement for details.

Read full announcement
Apr 29, 2026 (AoE)
Update
Evaluation Workflow Update
We are updating the evaluation workflow: the hidden set is split into A-board and B-board subsets of approximately 60 and 320 tasks, with a temporary submission freeze (April 30–May 2, 2026, EoD AoE), first submission for new teams by April 29, 2026 (EoD AoE), daily leaderboard refreshes at 23:59 EoD AoE during the freeze, submission intake reopens May 3, 2026 (AoE). See the May 18 announcements for the current Phase-1 schedule. Open the full announcement for the April 29 policies.

Read full announcement
Apr 26, 2026 (AoE)
Announcement
Registration Closed with 703 Valid Teams
The competition has finalized 703 valid participating teams, representing 1,307 participants worldwide. We thank all teams for their strong interest and participation.

Apr 9, 2026 (AoE)
Announcement
Competition Rules Page Released
The official Competition Rules page is now available on the website, detailing dataset format, submission process, evaluation procedures, and related constraints. Visit dataagent.top/rules for complete details.

View Rules
Mar 25, 2026 (AoE)
Release
Starter Kit Released
The official KDD Cup 2026 starter kit is now available on GitHub. It includes a ReAct baseline agent, dataset loader, and CLI tooling to help participants get started quickly.

View on GitHub
Mar 23, 2026 (AoE)
Announcement
Registration Now Open
Team registration is now open. The Team Leader submits initial registration, and all members will receive a verification email within 24 hours to complete the process.

Mar 23, 2026 (AoE)
Announcement
Official Community Channels Launched
Our official WeChat channel (数据智能与分析实验室 DIAL) and Discord server (KDD Cup 2026 | DataAgents) are now live. Join to get real-time updates and connect with other participants.

Mar 19, 2026 (AoE)
Update
Phase 1 Demo Dataset README Updated
The README in the Phase 1 demo dataset has been updated to help participants better understand the dataset structure.

Mar 18, 2026 (AoE)
Release
Phase 1 Demo Dataset Released
The Phase 1 demo dataset is now available. The official download link has been added to the DataAgent-Bench section.

Mar 1, 2026 (AoE)
Announcement
Official Website Launched
The KDD Cup 2026 competition website is now live, with competition details, schedule, and benchmark information now available online.

01 / Overview  01 / 概览
Why Data Agents?  为什么数据代理？
Traditional Data+AI systems have made significant strides in optimizing specific tasks, but they still rely heavily on human experts to orchestrate the end-to-end pipeline. This manual orchestration is a major bottleneck, limiting the scalability and adaptability of data analysis.
传统数据+AI 系统在优化特定任务方面取得了显著进展，但它们仍然严重依赖人类专家来协调端到端的工作流程。这种手动协调是一个主要瓶颈，限制了数据分析的可扩展性和适应性。

We define a Data Agent as a holistic architecture designed to orchestrate Data+AI ecosystems by tackling data-related tasks through integrated knowledge comprehension, reasoning, and planning capabilities. This competition challenges you to build truly autonomous data analysis systems that go far beyond single-shot question answering.
我们将数据代理定义为一种整体架构，旨在通过集成知识理解、推理和规划能力来处理数据相关任务，从而协调数据+AI 生态系统。这项比赛挑战你构建真正自主的数据分析系统，这些系统远超单次问答的范畴。

Decompose & Plan  分解与规划
Break down high-level analytical questions into multi-step, executable plans autonomously.
自动将高级分析问题分解为多步骤、可执行的计划。

Tool Selection & Invocation
工具选择与调用
Select and invoke appropriate tools — Python scripts, SQL queries, API calls — at each reasoning step.
选择并调用合适的工具——Python 脚本、SQL 查询、API 调用——在每个推理步骤中。

Heterogeneous Data Reasoning
异构数据推理
Reason over structured tables, unstructured documents, charts, and multi-modal data sources.
超越结构化表格、非结构化文档、图表和多模态数据源。

Result Synthesis  结果合成
Synthesize intermediate results across multiple steps to arrive at a final, accurate answer.
跨多个步骤综合中间结果，以得出最终、准确的答案。

Broader Impact  更广泛的影响
Robust Data Agents have the potential to revolutionize how we interact with data. They can democratize data science by enabling non-experts to perform sophisticated analyses through natural language. For enterprises, they can automate the work of data analysts and database administrators, leading to massive efficiency gains. This competition will stimulate new research in agent architectures, planning algorithms, tool use, and self-reflection for AI systems.
强大的数据代理有潜力彻底改变我们与数据交互的方式。它们可以通过自然语言使非专家能够执行复杂的分析，从而实现数据科学的民主化。对于企业而言，它们可以自动化数据分析师和数据库管理员的工作，从而带来巨大的效率提升。这场竞争将刺激在代理架构、规划算法、工具使用和人工智能系统的自我反思方面进行新的研究。

02 / Benchmark  02 / 基准测试
DataAgent-Bench
Each task in DataAgent-Bench presents a self-contained data analysis challenge. The agent receives a heterogeneous data package and a high-level natural language question, and must autonomously orchestrate a complex reasoning process to produce the final answer.
DataAgent-Bench 中的每个任务都提出了一个自包含的数据分析挑战。代理接收到一个异构数据包和一个高级自然语言问题，必须自主组织复杂的推理过程以生成最终答案。

Input: Data Package  输入：数据包
Heterogeneous, multi-modal data sources
异构、多模态数据源

task_001/data/  任务 001/数据/
database.sqlite
Structured database tables
结构化数据库表
regional_report.pdf  区域报告。
PDF report with analysis  PDF 分析报告
product_catalog.json
Structured product data  结构化产品数据
quarterly_targets.png  季度目标.png
Chart visualization  图表可视化
business_handbook.docx
Business rules & definitions
业务规则与定义
Non-Linear Reasoning Topology
非线性推理拓扑
Multi-step data analysis pipeline with branching and loops
Unlike simple linear chains, real-world data analysis often requires branching (parallel sub-queries), loops (iterative refinement), and convergence (merging results from multiple paths). DataAgent-Bench captures this complexity with DAG-structured reasoning graphs.
与简单的线性链不同，现实世界的数据分析通常需要分支（并行子查询）、循环（迭代优化）和收敛（合并来自多个路径的结果）。DataAgent-Bench 通过 DAG 结构的推理图捕获这种复杂性。

Reasoning Topology Patterns
推理拓扑模式
Sequential Chain  顺序链
A
B
C
D
Each step depends on the previous step's output. Errors propagate downstream.
每一步都依赖于上一步的输出。错误会向下传播。

Branching & Merging  分支与合并
A
↙
B₁
B₂
↘
C (merge)  C (合并)
Parallel sub-queries across different data sources, then merge results.
跨不同数据源的并行子查询，然后合并结果。

Iterative Loop  迭代循环
A
B
C
Iterative refinement where the agent revisits and corrects intermediate results.
迭代优化，代理重新访问并纠正中间结果。

Example Task  示例任务
Natural Language Query  自然语言查询
"Our Q3 regional market analysis report identifies the region with the strongest year-over-year growth. For that region, pull the total actual sales revenue of all Electronics products from our sales database. Then, compare this figure against the quarterly sales target shown in the performance dashboard chart. Report the percentage difference."
我们的 Q3 区域市场分析报告确定了增长最快的地区。对于该地区，从我们的销售数据库中提取所有电子产品销售总收入。然后，将此数字与绩效仪表板图表中显示的季度销售目标进行比较。报告百分比差异。

Expected Reasoning Graph  预期推理图
This example demonstrates a branching pattern: after identifying the target region, the agent spawns two parallel sub-tasks (database query and chart analysis), then merges results for the final computation.
此示例演示了一种分支模式：在确定目标区域后，代理会生成两个并行子任务（数据库查询和图表分析），然后合并结果进行最终计算。

A
Document QA  文档问答
Read PDF report → identify top-growth region
阅读 PDF 报告 → 确定增长最快区域

"East Asia"  "东亚"
B₁
Text-to-SQL  文本到 SQL
Query sales WHERE region = "East Asia" AND category = "Electronics"
查询销售 WHERE 地区 = "东亚" AND 类别 = "电子产品"

$4,200,000
B₂
Image Analysis  图像分析
Read performance dashboard chart → extract Q3 target
读取性能仪表板图表 → 提取 Q3 目标

$3,800,000
merge  合并
C
Python Computation  Python 计算
Compute percentage difference: (4,200,000 - 3,800,000) / 3,800,000
计算百分比差异：(4,200,000 - 3,800,000) / 3,800,000

+10.5%
Final Answer:  最终答案：
+10.5%
Resources  资源
The starter kit works across both phases. Demo datasets are released per phase alongside difficulty information and download links.
启动套件适用于两个阶段。每个阶段都会发布演示数据集，附带难度信息和下载链接。

Starter Kit  入门套装

Phase 1 & 2  第一阶段和第二阶段
HKUSTDial / kddcup2026-data-agents-starter-kit

View on GitHub  在 GitHub 上查看
Phase 1 Demo Dataset  阶段 1 演示数据集
A preview package for the Phase 1 setting. The same package is mirrored on Google Drive and Baidu Netdisk—use whichever is faster for you.
第一阶段的设置预览包。该包同时在 Google Drive 和 Baidu Netdisk 上提供镜像——使用对你来说更快的那个。

Available  可用
Phase 1 Difficulty Levels
第一阶段的难度等级
Level  等级	Modalities  模态	Core Challenge  核心挑战	Document Scale  文档规模
Easy  简单	Structured files such as JSON/CSV + knowledge documents
JSON/CSV 等结构化文件 + 知识文档	Code generation for data analysis workflows, such as Python execution
数据分析工作流的代码生成，例如 Python 执行	Short context  简短背景
Medium  中等	Structured files such as JSON/CSV + databases + knowledge documents
JSON/CSV 等结构化文件 + 数据库 + 知识文档	Text-to-SQL and multi-source data analysis
文本到 SQL 和多源数据分析	Moderate context  中等背景
Hard  硬	Structured files such as JSON/CSV + databases + data documents + knowledge documents
JSON/CSV 等结构化文件 + 数据库 + 数据文档 + 知识文档	Reasoning over unstructured data documents
对非结构化数据文档进行推理	~10K-128K tokens  ~10,000-128,000 个代币
Extreme  极端	Same modality combination as Hard
与 Hard 相同的模态组合	Context engineering and memory under ultra-long document inputs
上下文工程和超长文档输入下的内存	>128K tokens  >128K 个代币
Download  下载
Google Drive
  谷歌云端硬盘
Baidu Netdisk
  百度网盘
Extraction code: bh3v  提取代码：bh3v
Phase 2 Demo Dataset  阶段 2 演示数据集
Phase 2 demo dataset details, difficulty classification, and download access will be announced before Phase 2 begins.
Phase 2 演示数据集详细信息、难度分类和下载访问将在 Phase 2 开始前公布。

Coming Later  稍后提供
Phase 2 Difficulty Levels
第二阶段难度等级
Phase 2 difficulty definitions and demo dataset details will be released before Phase 2 starts.
第二阶段难度定义和演示数据集详情将在第二阶段开始前发布。

Download  下载
Phase 2 Info Will Be Released Before Phase 2
  第二阶段信息将在第二阶段之前发布
03 / Tracks  03 / 赛道
Tracks  曲目
The competition has two phases. Phase 1 uses a single main track, and Phase 2 opens multiple subtracks for qualified teams with different goals and evaluation styles.
比赛分为两个阶段。第一阶段使用一条主赛道，第二阶段为符合条件的团队开放多个子赛道，这些团队有不同的目标和评估风格。

A/B-board evaluation  A/B-BOARD 评估
Phase 1  阶段 1
All registered teams compete under automated A-board and B-board evaluation, with the final Phase 1 leaderboard combining both scores.
所有注册队伍在自动化的 A 板和 B 板评估下比赛，最终的第一阶段排行榜将结合两个分数。

Qualified teams only  仅限合格队伍
Phase 2  阶段 2
Top 60 teams from Phase 1 advance. Top 1–40 may choose the Leaderboard or Creative subtrack; Top 41–60 enter the Creative subtrack only.
前 60 名队伍进入第二阶段。前 1-40 名队伍可以选择排行榜赛道或创意赛道；前 41-60 名队伍只能进入创意赛道。

Phase 2 Subtracks  第二阶段子赛道
Qualified teams can continue in Phase 2 through two different tracks, depending on their qualification status and final competition rules.

Benchmark accuracy
Leaderboard Subtrack
This subtrack keeps the core competition format, but uses a more challenging Phase 2 benchmark with new modalities such as data images and data videos. Teams are ranked automatically by answer accuracy.

System design and usability
Creative Subtrack
Teams build mature, easy-to-use, interface-friendly data agent systems with strong interaction design and transparent decision processes.

Advancement Rule
How qualification works
The Top 60 teams by weighted Phase-1 A-board and B-board scores qualify for Phase 2.

Top 1–40 may choose the Leaderboard or Creative subtrack. Top 41–60 may only enter the Creative subtrack. If teams tie on the final score, they share the same rank but each tied team still occupies a separate qualification slot.

04 / Evaluation
Scoring & Evaluation
The leaderboard track uses column-level matching with recall-based scoring and a light redundancy penalty, aligned with Rules 6.2 and 6.3. Creative-track submissions are reviewed jointly by sponsors and the organizing committee.

Leaderboard Scoring Rule
The official metric first matches columns by content signatures, then computes recall and subtracts a light penalty for extra unmatched columns.

Metric Details
Column Signature Matching
Columns are matched by content signatures: values are sorted inside each column, then compared by signature counts. Column names and row order are ignored.

Coverage (Recall)
Recall measures how many gold columns are covered by matched columns: Recall = Matched Columns / Gold Columns.

Redundancy Penalty
Final score applies a light penalty for extra unmatched columns: Score = Recall - λ · (Extra Columns / Predicted Columns), lower-bounded at 0.

Example
Pred Answer
A
B
C
The prediction contains columns A, B, C.

Gold Answer
B
C
The gold answer requires columns B, C.

Result: Recall = 2/2, Score = 1 - λ · (1/3)
Even though the prediction contains an extra column A, it still fully covers gold columns B and C.

The extra column contributes to the penalty term through Extra Columns / Predicted Columns.

Column matching uses only values in each column signature; column names and row order are ignored.

Evaluation Modes
Automated
Leaderboard Ranking
Leaderboard submissions are scored automatically using column signature matching, recall, and redundancy penalty for ranking feedback.

Committee Review
Creative Track Assessment
Creative-track systems are reviewed jointly by sponsors and the organizing committee, with emphasis on interaction design, transparency, innovation, and overall usability.

06 / Timeline
Competition Timeline
The official schedule runs from March 15 to August 9, 2026, covering registration, Phase 1 A/B-board evaluation, qualification review, Phase 2 with two subtracks, final verification, and the KDD announcement. All dates are interpreted as end-of-day AoE (UTC-12) unless otherwise noted.

Launch  启动
Mar 15 – Mar 18, 2026 (AoE)
2026 年 3 月 15 日 – 3 月 18 日（美国东部时间）
Competition Launch & Demo Dataset Release
比赛启动 & 示例数据集发布
The competition opens and the demo dataset is published for participants.
比赛开启，并向参与者发布示例数据集。

Registration  注册
Mar 22 – Apr 23, 2026 (AoE)
2026 年 3 月 22 日 – 4 月 23 日（美国东部时间）
Registration  注册
Teams register and finalize their participant roster during the official registration window.
团队在官方注册窗口期间注册并最终确定其参赛人员名单。

Phase 1  阶段 1
Current Stage  当前阶段
Apr 24 – May 21, 2026 (AoE)
2026 年 4 月 24 日至 5 月 21 日（AoE）
Phase 1 A-Board Evaluation
第一阶段 A 板评估
Registered teams compete on the A-board for staged leaderboard feedback. A-board submission closes May 21, 2026, 19:59 Beijing (May 20, 23:59 AoE). Earlier migration freeze: April 30–May 2 (EoD AoE); intake reopened May 3, 2026 (AoE).
注册队伍在 A 板上竞争，以获取分阶段排行榜反馈。A 板提交截止日期为 2026 年 5 月 21 日 19:59 北京时间（2026 年 5 月 20 日 23:59 AoE）。早期迁移冻结：4 月 30 日至 5 月 2 日（AoE 结束）；5 月 3 日，2026 年（AoE）重新开放招募。

B-Board
May 21 – May 23, 2026 (AoE)
5 月 21 日 – 5 月 23 日，2026 年（AoE）
B-Board Final Submission Window
B-Board 最终提交窗口
Opens when A-board closes. Each team may submit one final B-board solution by email by May 23, 2026, 19:59 Beijing (May 22, 23:59 AoE).
A-Board 关闭后开放。每个团队可于 2026 年 5 月 23 日 19:59 北京时间（AoE 时间 5 月 22 日 23:59）前，通过电子邮件提交一份最终 B-Board 解决方案。

B-Board
May 23 – May 26, 2026 (AoE)
5 月 23 日至 5 月 26 日，2026 年（AoE）
B-Board Evaluation & Phase 1 Results
B-Board 评估与第一阶段结果
Internal B-board evaluation (12-hour runtime per run). No submissions or changes accepted during this window. Final Phase 1 rankings combine A-board and B-board scores.
内部 B-board 评估（每次运行 12 小时运行时间）。在此期间不接受提交或更改。最终第一阶段排名结合 A-board 和 B-board 分数。

Review  评审
May 26 – May 28, 2026 (AoE)
2026 年 5 月 26 日至 5 月 28 日（AoE）
Qualification Review  资格评审
Top 60 teams qualify for Phase 2. Top 1–40 may choose the Leaderboard or Creative subtrack; Top 41–60 may enter the Creative subtrack only.
前 60 名队伍进入第 2 阶段。前 1-40 名队伍可以选择排行榜或创意子赛道；前 41-60 名队伍只能进入创意子赛道。

Phase 2  阶段 2
May 28 – Jun 30, 2026 (AoE)
5 月 28 日 – 6 月 30 日，2026 年（AoE）
Phase 2 Competition  第二阶段竞赛
Qualified teams enter Phase 2 and compete in either the leaderboard subtrack, which uses more challenging data and new modalities such as data images and data videos, or the creative subtrack for mature, user-friendly data agent systems.
合格的团队将进入第二阶段，并在排行榜子赛道或创意子赛道中竞争。排行榜子赛道使用更具挑战性的数据和新的模态，如数据图像和数据视频，而创意子赛道则针对成熟、用户友好的数据代理系统。

Final Review  最终评审
Jul 1 – Jul 14, 2026 (AoE)
2026 年 7 月 1 日-7 月 14 日（AoE）
Final Freeze & Award Review
最终冻结 & 奖项评审
The final leaderboard is frozen, manual checks are completed, and award-winning teams are confirmed.
最终排行榜已冻结，手动检查完成，获奖团队已确认。

Final  最终
Jul 15, 2026 (AoE)  2026 年 7 月 15 日 (AoE)
Award Notification  获奖通知
Winning teams receive official notification of the final results.
获奖团队将收到最终结果的正式通知。

Final  最终
Aug 9, 2026 (AoE)  2026 年 8 月 9 日（美国东部时间）
KDD 2026 Announcement  KDD 2026 宣布
Formal announcement of winners at KDD 2026.
KDD 2026 获奖者正式公告。

07 / Registration  07 / 注册
How to Register  如何注册
Registration is a four-step process. The team leader registers first, then all members complete identity verification individually. Team composition is 3 team members + 1 optional advisor.
注册是一个四步流程。队长首先注册，然后所有成员分别完成身份验证。团队组成是 3 名团队成员 + 1 名可选顾问。

Step 01
Initial Registration
Team Leader Only

The Team Leader submits the team's basic information via the registration form.

Registration Form
Step 02
Get Verification Code
All Members

Each team member will receive an email from kddcup@hkust-gz.edu.cn with a unique verification code within 24 hours. Previously registered email addresses will be rejected.

Step 03
Sign Consent Form
All Members

Every member will receive a second email containing a link to the Informed Consent Form. Each member must complete the form and enter their personal verification code to validate their application.

Step 04
Final Confirmation
You're In

Once verified, each member will receive a "Registration Confirmed" email. You are now officially enrolled!

08 / Prizes
Prizes
Prize allocation is defined separately for the leaderboard track and the creative track. All prizes are paid in Chinese Yuan (CNY); USD figures are estimates based on an approximate exchange rate.

Leaderboard Track
Main benchmark ranking awards

10 teams awarded
Champion
$6,000
Runner-up
$4,000
Second Runner-up
$2,500
Merit Award (4th–10th)
$350 each
Creative Track
Product and system design awards

Up to 3 awards
Champion
$1,200
Runner-up
$800
Second Runner-up
$350
Awards in the creative track are subject to final committee decision.

* Prizes are disbursed in Chinese Yuan (CNY) by the sponsoring organization. USD amounts shown above are estimates based on an approximate exchange rate of 1 USD ≈ 6.91 CNY at the time of publication. The actual CNY amount is fixed; the final USD equivalent will vary with the prevailing exchange rate at the time of payment. All prize amounts listed above are before tax. Winners are responsible for any taxes, withholdings, or reporting obligations arising from accepting a prize, as required by applicable law.

Beyond Prizes
KDD Cup Workshop Presentation
Winning teams will have the opportunity to present their solutions at the KDD Cup Workshop at KDD 2026, a dedicated half-day session providing significant visibility for their work to the broader data mining and AI community.

Community Recognition
Top-performing teams will be recognized at the formal KDD 2026 Winners Announcement ceremony, gaining visibility among leading researchers and practitioners in the field.

09 / Committee
Organizing Committee
The competition is coordinated by a set of general chairs and several committee chairs who lead registration, publicity, data, and evaluation.

General Chairs
These four members oversee the competition as a whole and coordinate the overall direction.

Yuyu Luo profile photo
Yuyu Luo
Primary Contact
General Chair
Assistant Professor

HKUST (Guangzhou) & HKUST

Research at the intersection of Data and AI, focusing on Data Agents and Data-centric AI. 50+ publications in top-tier DB and AI venues (SIGMOD, VLDB, KDD, ICML, NeurIPS, ICLR). Best-of-SIGMOD 2023 Papers recipient. Co-organized the LLM+Vector Data Workshop at ICDE 2026, the Agentic Data System Workshop at VLDB 2026, and presented Data Agent tutorials at SIGMOD and VLDB.

Homepage
Guoliang Li profile photo
Guoliang Li
General Chair
Professor

Tsinghua University

ACM Fellow and IEEE Fellow. Research focuses on learning-based databases and data-centric AI. VLDB 2017 Early Research Contribution Award recipient. Served as SIGMOD 2021 General Co-Chair and ICDE 2027 PC Co-Chair.

Homepage
Nan Tang profile photo
Nan Tang
General Chair
Associate Professor

HKUST (Guangzhou) & HKUST

ACM Distinguished Member. Research interests include AI4DB and data-centric AI. Recipient of the VLDB 2010 Best Paper Award and the SIGMOD 2024 Research Highlight Award. Co-organized the KDD Cup 2024 CRAG Challenge.

Homepage
Boyan Li profile photo
Boyan Li
Primary Contact
General Chair
PhD Student

HKUST (Guangzhou)

Research focuses on Text-to-SQL and Data Agents. Published 14 papers in top venues including KDD, ICML, NeurIPS, and VLDB.

Homepage
Committee Chairs
Different operational areas are led by dedicated chairs, with some members covering multiple responsibilities.

Zhengxuan Zhang profile photo
Zhengxuan Zhang
PhD Student

HKUST (Guangzhou)

Registration
Evaluation
Research focuses on Document AI, Information Extraction, and Database Systems, with several papers published in top-tier conferences and journals.

Homepage
Yupeng Xie profile photo
Yupeng Xie
PhD Student

HKUST (Guangzhou)

Publicity
Data
Research focuses on Data Agents and Data Visualization. Published papers in top venues including VLDB, SIGMOD, ICLR, and IEEE VIS.

Homepage
Zhuowen Liang profile photo
Zhuowen Liang
PhD Student

HKUST (Guangzhou)

Data
Evaluation
Research focuses on Information Extraction and Document AI. Published several papers in top venues including ICLR, VLDB, and MM.

Homepage
Yuan Li profile photo
Yuan Li
PhD Student

Tsinghua University

Evaluation
Research focuses on Unstructured Data Analysis and Data Agents. Published papers in VLDB.

Jiayi Zhang profile photo
Jiayi Zhang
PhD Student

HKUST (Guangzhou)

Publicity
Research focuses on Language Agents. Published papers in top venues including ICLR, ICML, and NeurIPS.

Homepage
Xiaotian Lin profile photo
Xiaotian Lin
PhD Student

HKUST (Guangzhou)

Evaluation
Research focuses on Data-centric AI and Data Agents. Published papers in top venues including VLDB, ICLR, and ACL.

Xinyu Liu profile photo
Xinyu Liu
PhD Student

HKUST (Guangzhou)

Evaluation
Research focuses on NL2SQL and Data Agents. Published papers in top venues including KDD and TKDE.

Homepage
Yizhang Zhu profile photo
Yizhang Zhu
PhD Student

HKUST (Guangzhou)

Evaluation
Research focuses on Data Agents and AI4DB. Published papers in top venues including NeurIPS, SIGMOD, and COLM.

Homepage
Zhangyang Peng profile photo
Zhangyang Peng
PhD Student

HKUST (Guangzhou)

Evaluation
Research focuses on Table Intelligence and Data Agents. Published papers in top venues including SIGMOD.

Homepage
10 / Community
Join the Community
Connect with other participants, get the latest updates, and reach the organizing team through our official channels.

WeChat Official Account
数据智能与分析实验室 DIAL

Follow and reply KDD进群 for the WeChat group QR code, or KDD大赛 for competition FAQ and resources.

DIAL WeChat QR Code
Discord
Join our Discord server to discuss with participants worldwide and get real-time updates from the organizing team.

KDD Cup 2026 | DataAgents →
Discord QR Code
Data Agent icon
KDD Cup 2026
Data Agents for Complex Data Analysis. A competition at the intersection of data management, artificial intelligence, and large language models.

Quick Links
News
Overview
Benchmark
Tracks
Evaluation
Timeline
Prizes
Committee
FAQ
Contact
For questions about the competition, please reach out to the primary contacts.

Boyan Li
Yuyu Luo
Community
Discord — KDD Cup 2026 | DataAgents
WeChat Official Account: 数据智能与分析实验室 DIAL

Reply KDD进群 for the WeChat group QR code, or KDD大赛 for competition FAQ and resources.

DIAL WeChat QR Code
KDD Cup 2026. Organized by Tsinghua University & HKUST (Guangzhou).

Part of ACM SIGKDD 2026