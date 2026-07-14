# Spider 2.0 Snow Table Group 全库审计

本文件记录 152 个 Spider2-Snow 数据库在当前 `db_table_group` 规则下的压缩结果。统计来源是本地 Spider2 metadata，不连接 Snowflake。

运行命令：

```bash
uv run python scripts/spider/analyze_table_groups.py --json-out /tmp/spider2_table_groups_all_v3.json
```

这里的 `logical units` 就是当前需要 agent 认知的实体数：一个 `table_group` 算 1 个实体，未进入表组的 standalone table 各算 1 个实体。

总体结果：

```text
databases: 152
tables: 7860
columns: 522268
databases with table groups: 40/152
table groups: 392
grouped tables: 5361/7860 (68.2%)
logical units / cognitive entities after table_group: 2891
logical units <= 50: 142/152
logical units <= 80: 149/152
logical units <= 100: 150/152
```

审计结论：

- 当前 table_group 规则对物理分片、时间分区、release/version、chromosome、geo region shard 有效。
- 剩余最大的认知负担主要不是 table_group 漏识别，而是业务 topic、assay 和 data-product 层级。例如 `EBI_CHEMBL`、`HTAN_*`、`CPTAC_PDC`。
- `table_group` 不应为了降低数量强行合并业务模块。当前流程把剩余 outlier 交给 `agent_topic_group`，在官方 schema 内创建语义 topic；不再使用旧的 `db_topic_group/source_collection/topic_family` 结构。

## Live Snowflake object audit

Spider2-Snow schema 视野口径已单独固化在 `docs/spider_problem/spider2_snow_schema_visibility_policy.md`。本节只保留 live Snowflake 审计事实。

2026-07-07 用 Spider2-Snow Snowflake 账号执行过账户级对象审计：

```sql
SHOW TERSE OBJECTS IN ACCOUNT;
SHOW TERSE SCHEMAS IN ACCOUNT;
```

按当前 152 个 Spider2-Snow benchmark DB 过滤后的结果：

```text
user schemas: 373
user objects: 12964
user tables: 12714
user views: 250
other user objects: 0
information_schema objects: 9424
dbs with user views: 8/152
dbs with table/view count matching local metadata: 141/152
```

解释：

- 每个 Snowflake database 都有系统 `INFORMATION_SCHEMA`，账户级 `SHOW OBJECTS` 会看到这些系统 view；这些不是 benchmark 业务对象，Pontis 当前 Snowflake source 已排除 `INFORMATION_SCHEMA`。
- 用户/benchmark schema 内确实存在 view：共 250 个，集中在 `BRAZE_USER_EVENT_DEMO_DATASET`、`GLOBAL_GOVERNMENT`、`FINANCE__ECONOMICS`、`WEATHER__ENVIRONMENT`、`US_REAL_ESTATE`、`YES_ENERGY__SAMPLE_DATA`、`US_ADDRESSES__POI`、`GLOBAL_WEATHER__CLIMATE_DATA_FOR_BI`。
- 当前可见的 user object kind 只有 `TABLE` 和 `VIEW`，没有看到 stage、sequence、function、procedure 等其它 user object。
- `storage/stores/snowflake.py` 会从 live `information_schema.tables` 抽 `TABLE` 和 `VIEW`，并把 view 标成 `:view` 节点；但 `db_table_group` 目前只按 `:table` 做 physical grouping。

Live 对象数和本地 Spider2 metadata 不一致的库：

| DB | local table JSON | live TABLE+VIEW | live object kinds | 说明 |
| --- | ---: | ---: | --- | --- |
| `AMAZON_VENDOR_ANALYTICS__SAMPLE_DATASET` | 43 | 0 | `{}` | 当前角色账户级 `SHOW` 未返回 user table/view，需要单库权限再核验 |
| `FINANCE__ECONOMICS` | 50 | 48 | `VIEW: 48` | live 是 Cybersyn view，metadata 多 2 个对象 |
| `GITHUB_REPOS_DATE` | 38 | 5173 | `TABLE: 5173` | live 库远大于 benchmark metadata，直接 live 全抽会严重放大 |
| `GLOBAL_GOVERNMENT` | 50 | 51 | `VIEW: 51` | live 是 Cybersyn view，metadata 少 1 个对象 |
| `NETHERLANDS_OPEN_MAP_DATA` | 3 | 0 | `{}` | 当前角色账户级 `SHOW` 未返回 user table/view，需要单库权限再核验 |
| `NHTSA_TRAFFIC_FATALITIES_PLUS` | 122 | 123 | `TABLE: 123` | live 比 metadata 多 1 张表 |
| `SAN_FRANCISCO` | 8 | 9 | `TABLE: 9` | live 比 metadata 多 1 张表 |
| `SAN_FRANCISCO_PLUS` | 19 | 20 | `TABLE: 20` | live 比 metadata 多 1 张表 |
| `US_ADDRESSES__POI` | 7 | 10 | `VIEW: 10` | live 是 Cybersyn view，metadata 少 3 个对象 |
| `US_REAL_ESTATE` | 25 | 28 | `VIEW: 28` | live 是 Cybersyn view，metadata 少 3 个对象 |
| `WEATHER__ENVIRONMENT` | 22 | 29 | `VIEW: 29` | live 是 Cybersyn view，metadata 少 7 个对象 |

工程结论：Spider2-Snow 的 extractor 不应该无条件按 live database 全量抽对象。至少对 Spider2 benchmark pipeline 应增加一个 benchmark metadata allowlist，只抽官方 metadata/cases 覆盖的 table/view；否则 `GITHUB_REPOS_DATE` 这类库会从 38 个 benchmark 表膨胀到 5173 个 live 表。

### JSON / DDL.csv / live Snowflake 三种 schema 口径

进一步检查 `data/Spider2` 后发现，Spider2-Snow 的 schema 资源不是单一口径：

1. `resource/databases/<db>/<schema>/*.json`：逐表 JSON，包含 column names/types/descriptions/sample rows。当前 Pontis 离线统计使用的是这个口径。
2. `resource/databases/<db>/<schema>/DDL.csv`：schema-level DDL。官方 Spider-Agent prompt 明确要求先看 `DDL.csv`，再按需看 JSON。
3. live Snowflake：实际可查询对象，会受 shared database 当前可用性、publisher 更新和权限影响。

全 152 库统计：

```text
JSON count != DDL.csv row count: 7/152
DDL.csv row count != live TABLE+VIEW count: 11/152
JSON count != live TABLE+VIEW count: 11/152
```

最重要的差异：

| DB | JSON | DDL.csv | live TABLE+VIEW | 解释 |
| --- | ---: | ---: | ---: | --- |
| `GITHUB_REPOS_DATE` | 38 | 5173 | 5173 | DDL/live 包含完整 day/month/year 分片；JSON 只给任务相关/代表表的详细描述 |
| `FINANCE__ECONOMICS` | 50 | 136 | 48 | DDL 是更大的 Cybersyn schema 面；live 只暴露当前 shared views 子集 |
| `GLOBAL_GOVERNMENT` | 50 | 136 | 51 | 同上 |
| `WEATHER__ENVIRONMENT` | 22 | 136 | 29 | 同上 |
| `US_REAL_ESTATE` | 25 | 136 | 28 | 同上 |
| `US_ADDRESSES__POI` | 7 | 136 | 10 | 同上 |
| `BASEBALL` | 2 | 29 | 2 | DDL 比 JSON 广，JSON 是任务相关详细表 |

另外两个库 live 当前不可用：

```text
AMAZON_VENDOR_ANALYTICS__SAMPLE_DATASET: Snowflake shared database is no longer available
NETHERLANDS_OPEN_MAP_DATA: Snowflake shared database is no longer available
```

Gold tables 与 JSON metadata 的关系：

```text
spider2-snow gold table refs: 4219
missing from JSON table_fullname metadata: 0
```

也就是说，官方 `gold-tables` 引用的表都能在逐表 JSON metadata 中找到；但官方 baseline 同时让 agent 读取 `DDL.csv`，所以 `DDL.csv` 暴露的认知面在少数库上明显大于 JSON 表数。

Pontis 口径建议：

- benchmark/offline schema detail：以 JSON `table_fullname` 为主，因为它有 descriptions/sample rows，且覆盖 gold tables。
- schema discovery / table-group：必须解析 `DDL.csv`，至少识别 `GITHUB_REPOS_DATE.DAY/MONTH/YEAR` 这种 DDL-only 分片；否则会低估官方 agent 实际看到的 schema 面。
- live Snowflake：只作为执行/验证面，不应直接作为 schema extraction truth；shared database 会漂移或不可用。

| DB | tables | table_groups | grouped tables | cognitive entities | audit bucket |
| --- | ---: | ---: | ---: | ---: | --- |
| `EBI_CHEMBL` | 785 | 80 | 700 | 165 | needs topic/explorer; not just physical table groups |
| `HTAN_1` | 200 | 36 | 127 | 109 | needs topic/explorer; not just physical table groups |
| `HTAN_2` | 94 | 0 | 0 | 94 | needs topic/explorer; not just physical table groups |
| `CPTAC_PDC` | 79 | 0 | 0 | 79 | partially compressed; review remaining topics |
| `TCGA_MITELMAN` | 176 | 13 | 113 | 76 | partially compressed; review remaining topics |
| `BRAZE_USER_EVENT_DEMO_DATASET` | 62 | 0 | 0 | 62 | partially compressed; review remaining topics |
| `PATENTSVIEW` | 59 | 0 | 0 | 59 | partially compressed; review remaining topics |
| `TCGA` | 157 | 13 | 113 | 57 | partially compressed; review remaining topics |
| `FEC` | 486 | 42 | 474 | 54 | partially compressed; review remaining topics |
| `NOAA_DATA_PLUS` | 234 | 3 | 184 | 53 | partially compressed; review remaining topics |
| `FINANCE__ECONOMICS` | 50 | 0 | 0 | 50 | no high-confidence physical groups; direct/topic review |
| `GLOBAL_GOVERNMENT` | 50 | 0 | 0 | 50 | no high-confidence physical groups; direct/topic review |
| `CENSUS_BUREAU_ACS_1` | 351 | 29 | 331 | 49 | physical groups identified; manageable after grouping |
| `CENSUS_BUREAU_ACS_2` | 296 | 28 | 276 | 48 | physical groups identified; manageable after grouping |
| `PATENTS_USPTO` | 46 | 0 | 0 | 46 | no high-confidence physical groups; direct/topic review |
| `SDOH` | 294 | 28 | 276 | 46 | physical groups identified; manageable after grouping |
| `AMAZON_VENDOR_ANALYTICS__SAMPLE_DATASET` | 43 | 0 | 0 | 43 | no high-confidence physical groups; direct/topic review |
| `CMS_DATA` | 52 | 3 | 14 | 41 | physical groups identified; manageable after grouping |
| `CRYPTO` | 39 | 0 | 0 | 39 | no high-confidence physical groups; direct/topic review |
| `ORACLE_SQL` | 38 | 0 | 0 | 38 | no high-confidence physical groups; direct/topic review |
| `COVID19_USA` | 285 | 28 | 276 | 37 | physical groups identified; manageable after grouping |
| `NHTSA_TRAFFIC_FATALITIES_PLUS` | 122 | 17 | 102 | 37 | physical groups identified; manageable after grouping |
| `NOAA_DATA` | 218 | 3 | 184 | 37 | physical groups identified; manageable after grouping |
| `OPENAQ` | 33 | 0 | 0 | 33 | no high-confidence physical groups; direct/topic review |
| `EPA_HISTORICAL_AIR_QUALITY` | 32 | 0 | 0 | 32 | no high-confidence physical groups; direct/topic review |
| `WIDE_WORLD_IMPORTERS` | 31 | 0 | 0 | 31 | no high-confidence physical groups; direct/topic review |
| `F1` | 29 | 0 | 0 | 29 | no high-confidence physical groups; direct/topic review |
| `META_KAGGLE` | 29 | 0 | 0 | 29 | no high-confidence physical groups; direct/topic review |
| `OPEN_TARGETS_PLATFORM_2` | 29 | 0 | 0 | 29 | no high-confidence physical groups; direct/topic review |
| `FDA` | 83 | 1 | 56 | 28 | physical groups identified; manageable after grouping |
| `OPEN_TARGETS_PLATFORM_1` | 27 | 0 | 0 | 27 | no high-confidence physical groups; direct/topic review |
| `TCGA_HG38_DATA_V0` | 50 | 1 | 24 | 27 | physical groups identified; manageable after grouping |
| `BLS` | 143 | 1 | 118 | 26 | physical groups identified; manageable after grouping |
| `DEATH` | 28 | 1 | 3 | 26 | physical groups identified; manageable after grouping |
| `GEO_OPENSTREETMAP_BOUNDARIES` | 26 | 0 | 0 | 26 | no high-confidence physical groups; direct/topic review |
| `NEW_YORK_GEO` | 38 | 3 | 15 | 26 | physical groups identified; manageable after grouping |
| `COVID19_JHU_WORLD_BANK` | 25 | 0 | 0 | 25 | no high-confidence physical groups; direct/topic review |
| `US_REAL_ESTATE` | 25 | 0 | 0 | 25 | no high-confidence physical groups; direct/topic review |
| `STACKOVERFLOW_PLUS` | 24 | 0 | 0 | 24 | no high-confidence physical groups; direct/topic review |
| `COVID19_OPEN_WORLD_BANK` | 23 | 0 | 0 | 23 | no high-confidence physical groups; direct/topic review |
| `GOOGLE_DEI` | 140 | 1 | 118 | 23 | physical groups identified; manageable after grouping |
| `NHTSA_TRAFFIC_FATALITIES` | 108 | 17 | 102 | 23 | physical groups identified; manageable after grouping |
| `NEW_YORK_CITIBIKE_1` | 117 | 1 | 96 | 22 | physical groups identified; manageable after grouping |
| `WEATHER__ENVIRONMENT` | 22 | 0 | 0 | 22 | no high-confidence physical groups; direct/topic review |
| `PANCANCER_ATLAS_2` | 21 | 0 | 0 | 21 | no high-confidence physical groups; direct/topic review |
| `WORLD_BANK` | 21 | 0 | 0 | 21 | no high-confidence physical groups; direct/topic review |
| `TCGA_BIOCLIN_V0` | 80 | 7 | 67 | 20 | physical groups identified; manageable after grouping |
| `BANK_SALES_TRADING` | 19 | 0 | 0 | 19 | no high-confidence physical groups; direct/topic review |
| `LOG` | 19 | 0 | 0 | 19 | no high-confidence physical groups; direct/topic review |
| `MITELMAN` | 19 | 0 | 0 | 19 | no high-confidence physical groups; direct/topic review |
| `SAN_FRANCISCO_PLUS` | 19 | 0 | 0 | 19 | no high-confidence physical groups; direct/topic review |
| `YES_ENERGY__SAMPLE_DATA` | 19 | 0 | 0 | 19 | no high-confidence physical groups; direct/topic review |
| `EDUCATION_BUSINESS` | 18 | 0 | 0 | 18 | no high-confidence physical groups; direct/topic review |
| `NEW_YORK_PLUS` | 43 | 4 | 29 | 18 | physical groups identified; manageable after grouping |
| `NOAA_PORTS` | 18 | 0 | 0 | 18 | no high-confidence physical groups; direct/topic review |
| `ECOMMERCE` | 17 | 0 | 0 | 17 | no high-confidence physical groups; direct/topic review |
| `FHIR_SYNTHEA` | 17 | 0 | 0 | 17 | no high-confidence physical groups; direct/topic review |
| `MODERN_DATA` | 17 | 0 | 0 | 17 | no high-confidence physical groups; direct/topic review |
| `NCAA_INSIGHTS` | 17 | 0 | 0 | 17 | no high-confidence physical groups; direct/topic review |
| `IDC` | 16 | 0 | 0 | 16 | no high-confidence physical groups; direct/topic review |
| `PAGILA` | 16 | 0 | 0 | 16 | no high-confidence physical groups; direct/topic review |
| `SCHOOL_SCHEDULING` | 16 | 0 | 0 | 16 | no high-confidence physical groups; direct/topic review |
| `SQLITE_SAKILA` | 16 | 0 | 0 | 16 | no high-confidence physical groups; direct/topic review |
| `STACKOVERFLOW` | 16 | 0 | 0 | 16 | no high-confidence physical groups; direct/topic review |
| `CITY_LEGISLATION` | 15 | 0 | 0 | 15 | no high-confidence physical groups; direct/topic review |
| `NEW_YORK_GHCN` | 288 | 4 | 277 | 15 | physical groups identified; manageable after grouping |
| `NORTHWIND` | 15 | 0 | 0 | 15 | no high-confidence physical groups; direct/topic review |
| `CENSUS_BUREAU_USA` | 14 | 0 | 0 | 14 | no high-confidence physical groups; direct/topic review |
| `ADVENTUREWORKS` | 13 | 0 | 0 | 13 | no high-confidence physical groups; direct/topic review |
| `CHINOOK` | 13 | 0 | 0 | 13 | no high-confidence physical groups; direct/topic review |
| `DB_IMDB` | 13 | 0 | 0 | 13 | no high-confidence physical groups; direct/topic review |
| `ENTERTAINMENTAGENCY` | 13 | 0 | 0 | 13 | no high-confidence physical groups; direct/topic review |
| `GEO_OPENSTREETMAP_CENSUS_PLACES` | 67 | 1 | 55 | 13 | physical groups identified; manageable after grouping |
| `OPEN_TARGETS_GENETICS_1` | 13 | 0 | 0 | 13 | no high-confidence physical groups; direct/topic review |
| `OPEN_TARGETS_GENETICS_2` | 13 | 0 | 0 | 13 | no high-confidence physical groups; direct/topic review |
| `USFS_FIA` | 13 | 0 | 0 | 13 | no high-confidence physical groups; direct/topic review |
| `BOWLINGLEAGUE` | 12 | 0 | 0 | 12 | no high-confidence physical groups; direct/topic review |
| `NEW_YORK_NOAA` | 119 | 4 | 111 | 12 | physical groups identified; manageable after grouping |
| `E_COMMERCE` | 11 | 0 | 0 | 11 | no high-confidence physical groups; direct/topic review |
| `GEO_OPENSTREETMAP_WORLDPOP` | 11 | 0 | 0 | 11 | no high-confidence physical groups; direct/topic review |
| `MUSIC` | 11 | 0 | 0 | 11 | no high-confidence physical groups; direct/topic review |
| `NOAA_GLOBAL_FORECAST_SYSTEM` | 11 | 0 | 0 | 11 | no high-confidence physical groups; direct/topic review |
| `BRAZILIAN_E_COMMERCE` | 10 | 0 | 0 | 10 | no high-confidence physical groups; direct/topic review |
| `COMPLEX_ORACLE` | 10 | 0 | 0 | 10 | no high-confidence physical groups; direct/topic review |
| `DEPS_DEV_V1` | 10 | 0 | 0 | 10 | no high-confidence physical groups; direct/topic review |
| `GEO_OPENSTREETMAP` | 10 | 0 | 0 | 10 | no high-confidence physical groups; direct/topic review |
| `NCAA_BASKETBALL` | 10 | 0 | 0 | 10 | no high-confidence physical groups; direct/topic review |
| `NEW_YORK` | 22 | 3 | 15 | 10 | physical groups identified; manageable after grouping |
| `PANCANCER_ATLAS_1` | 10 | 0 | 0 | 10 | no high-confidence physical groups; direct/topic review |
| `SEC_QUARTERLY_FINANCIALS` | 10 | 0 | 0 | 10 | no high-confidence physical groups; direct/topic review |
| `TCGA_HG19_DATA_V0` | 33 | 1 | 24 | 10 | physical groups identified; manageable after grouping |
| `WWE` | 10 | 0 | 0 | 10 | no high-confidence physical groups; direct/topic review |
| `ELECTRONIC_SALES` | 9 | 0 | 0 | 9 | no high-confidence physical groups; direct/topic review |
| `GITHUB_REPOS_DATE` | 38 | 3 | 32 | 9 | physical groups identified; manageable after grouping |
| `TARGETOME_REACTOME` | 9 | 0 | 0 | 9 | no high-confidence physical groups; direct/topic review |
| `AIRLINES` | 8 | 0 | 0 | 8 | no high-confidence physical groups; direct/topic review |
| `CENSUS_BUREAU_INTERNATIONAL` | 8 | 0 | 0 | 8 | no high-confidence physical groups; direct/topic review |
| `CENSUS_GALAXY__AIML_MODEL_DATA_ENRICHMENT_SAMPLE` | 8 | 0 | 0 | 8 | no high-confidence physical groups; direct/topic review |
| `CENSUS_GALAXY__ZIP_CODE_TO_BLOCK_GROUP_SAMPLE` | 8 | 0 | 0 | 8 | no high-confidence physical groups; direct/topic review |
| `EU_SOCCER` | 8 | 0 | 0 | 8 | no high-confidence physical groups; direct/topic review |
| `HUMAN_GENOME_VARIANTS` | 8 | 0 | 0 | 8 | no high-confidence physical groups; direct/topic review |
| `IPL` | 8 | 0 | 0 | 8 | no high-confidence physical groups; direct/topic review |
| `SAN_FRANCISCO` | 8 | 0 | 0 | 8 | no high-confidence physical groups; direct/topic review |
| `DELIVERY_CENTER` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `ETHEREUM_BLOCKCHAIN` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `GENOMICS_CANNABIS` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `GOOG_BLOCKCHAIN` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `IMDB_MOVIES` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `LIBRARIES_IO` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `STACKING` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `THELOOK_ECOMMERCE` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `USDA_NASS_AGRICULTURE` | 11 | 2 | 6 | 7 | physical groups identified; manageable after grouping |
| `US_ADDRESSES__POI` | 7 | 0 | 0 | 7 | no high-confidence physical groups; direct/topic review |
| `AUSTIN` | 10 | 1 | 5 | 6 | physical groups identified; manageable after grouping |
| `COVID19_SYMPTOM_SEARCH` | 6 | 0 | 0 | 6 | no high-confidence physical groups; direct/topic review |
| `DIMENSIONS_AI_COVID19` | 6 | 0 | 0 | 6 | no high-confidence physical groups; direct/topic review |
| `GITHUB_REPOS` | 6 | 0 | 0 | 6 | no high-confidence physical groups; direct/topic review |
| `ECLIPSE_MEGAMOVIE` | 7 | 1 | 3 | 5 | physical groups identified; manageable after grouping |
| `GHCN_D` | 266 | 1 | 262 | 5 | physical groups identified; manageable after grouping |
| `CALIFORNIA_TRAFFIC_COLLISION` | 4 | 0 | 0 | 4 | no high-confidence physical groups; direct/topic review |
| `COVID19_NYT` | 4 | 0 | 0 | 4 | no high-confidence physical groups; direct/topic review |
| `GOOGLE_TRENDS` | 4 | 0 | 0 | 4 | no high-confidence physical groups; direct/topic review |
| `IRS_990` | 18 | 3 | 17 | 4 | physical groups identified; manageable after grouping |
| `NPPES` | 20 | 1 | 17 | 4 | physical groups identified; manageable after grouping |
| `OPEN_IMAGES` | 4 | 0 | 0 | 4 | no high-confidence physical groups; direct/topic review |
| `PATENTS_GOOGLE` | 4 | 0 | 0 | 4 | no high-confidence physical groups; direct/topic review |
| `GLOBAL_WEATHER__CLIMATE_DATA_FOR_BI` | 3 | 0 | 0 | 3 | no high-confidence physical groups; direct/topic review |
| `GNOMAD` | 71 | 3 | 71 | 3 | physical groups identified; manageable after grouping |
| `IOWA_LIQUOR_SALES_PLUS` | 3 | 0 | 0 | 3 | no high-confidence physical groups; direct/topic review |
| `MLB` | 3 | 0 | 0 | 3 | no high-confidence physical groups; direct/topic review |
| `NETHERLANDS_OPEN_MAP_DATA` | 3 | 0 | 0 | 3 | no high-confidence physical groups; direct/topic review |
| `PATENTS` | 3 | 0 | 0 | 3 | no high-confidence physical groups; direct/topic review |
| `THE_MET` | 3 | 0 | 0 | 3 | no high-confidence physical groups; direct/topic review |
| `WORD_VECTORS_US` | 3 | 0 | 0 | 3 | no high-confidence physical groups; direct/topic review |
| `_1000_GENOMES` | 3 | 0 | 0 | 3 | no high-confidence physical groups; direct/topic review |
| `BASEBALL` | 2 | 0 | 0 | 2 | no high-confidence physical groups; direct/topic review |
| `CHICAGO` | 2 | 0 | 0 | 2 | no high-confidence physical groups; direct/topic review |
| `COVID19_OPEN_DATA` | 2 | 0 | 0 | 2 | no high-confidence physical groups; direct/topic review |
| `GOOGLE_ADS` | 2 | 0 | 0 | 2 | no high-confidence physical groups; direct/topic review |
| `LONDON` | 2 | 0 | 0 | 2 | no high-confidence physical groups; direct/topic review |
| `NOAA_GSOD` | 97 | 1 | 96 | 2 | physical groups identified; manageable after grouping |
| `PYPI` | 2 | 0 | 0 | 2 | no high-confidence physical groups; direct/topic review |
| `SUNROOF_SOLAR` | 2 | 0 | 0 | 2 | no high-confidence physical groups; direct/topic review |
| `USA_NAMES` | 2 | 0 | 0 | 2 | no high-confidence physical groups; direct/topic review |
| `BBC` | 1 | 0 | 0 | 1 | no high-confidence physical groups; direct/topic review |
| `CYMBAL_INVESTMENTS` | 1 | 0 | 0 | 1 | no high-confidence physical groups; direct/topic review |
| `FIREBASE` | 114 | 1 | 114 | 1 | physical groups identified; manageable after grouping |
| `GA360` | 366 | 1 | 366 | 1 | physical groups identified; manageable after grouping |
| `GA4` | 92 | 1 | 92 | 1 | physical groups identified; manageable after grouping |
| `GBIF` | 1 | 0 | 0 | 1 | no high-confidence physical groups; direct/topic review |
| `HACKER_NEWS` | 1 | 0 | 0 | 1 | no high-confidence physical groups; direct/topic review |
| `IOWA_LIQUOR_SALES` | 1 | 0 | 0 | 1 | no high-confidence physical groups; direct/topic review |
