# Spider2-Snow Gold Join Value-Overlap Audit

This report measures the 127 unique physical-column pairs extracted from the local Spider2-Snow gold SQL lineage.
Values are normalized with `LOWER(TRIM(TO_VARCHAR(value)))` before exact distinct-set comparison.

## Summary

- Gold physical-column pairs: 127
- Exact measurements: 124
- Statement timeouts/errors: 3
- Exact pairs with non-empty intersection: 121
- Exact pairs with zero intersection: 3
- Lowest positive `overlap/min`: 0.000235746777
- Lowest positive raw/raw `overlap/min`: 0.014905707

### Overlap/min distribution

- `zero`: 3
- `(0, 0.0001)`: 0
- `[0.0001, 0.001)`: 1
- `[0.001, 0.01)`: 0
- `[0.01, 0.05)`: 3
- `[0.05, 0.1)`: 1
- `[0.1, 0.5)`: 4
- `[0.5, 1]`: 112

### Jaccard distribution

- `zero`: 3
- `(0, 0.0001)`: 2
- `[0.0001, 0.001)`: 3
- `[0.001, 0.01)`: 6
- `[0.01, 0.05)`: 4
- `[0.05, 0.1)`: 7
- `[0.1, 0.5)`: 11
- `[0.5, 1]`: 88

## All Gold Pairs

| # | DB / SQL | Predicate | Lineage | Left cardinality | Right cardinality | Intersection | Overlap/min | Jaccard | Status |
|---:|---|---|---|---:|---:|---:|---:|---:|---|
| 1 | CRYPTO / sf_bq093.sql | `t."block_number" = b."number"` | raw / raw | 143,676 | 229,890 | 0 | 0 | 0 | exact |
<!-- left=CRYPTO.CRYPTO_ETHEREUM_CLASSIC.TRANSACTIONS.block_number right=CRYPTO.CRYPTO_ETHEREUM_CLASSIC.BLOCKS.number pair_status=kept_candidate -->
| 2 | ETHEREUM_BLOCKCHAIN / sf_bq012.sql | `t."block_hash" = b."hash"` | raw / raw | 14,636 | 5,764 | 0 | 0 | 0 | exact |
<!-- left=ETHEREUM_BLOCKCHAIN.ETHEREUM_BLOCKCHAIN.TRANSACTIONS.block_hash right=ETHEREUM_BLOCKCHAIN.ETHEREUM_BLOCKCHAIN.BLOCKS.hash pair_status=kept_candidate -->
| 3 | GITHUB_REPOS_DATE / sf_bq224.sql | `T.repo_name = A."repo_name"` | cast / raw | 503,994 | 3,325,550 | 0 | 0 | 0 | exact |
<!-- left=GITHUB_REPOS_DATE.MONTH._202204.repo right=GITHUB_REPOS_DATE.GITHUB_REPOS.LICENSES.repo_name pair_status=filtered -->
| 4 | GEO_OPENSTREETMAP / sf_bq254.sql | `nt.id = gc.id` | coalesce / coalesce | 5,776,952 | 5,234,430 | 1,234 | 0.000235746777 | 0.000112078421 | exact |
<!-- left=GEO_OPENSTREETMAP.GEO_OPENSTREETMAP.PLANET_FEATURES.osm_way_id right=GEO_OPENSTREETMAP.GEO_OPENSTREETMAP.PLANET_FEATURES.osm_id pair_status=kept_candidate -->
| 5 | GITHUB_REPOS / sf_bq233.sql | `sf."id" = sc."id"` | raw / raw | 450,413 | 24,286 | 362 | 0.014905707 | 0.000763170488 | exact |
<!-- left=GITHUB_REPOS.GITHUB_REPOS.SAMPLE_FILES.id right=GITHUB_REPOS.GITHUB_REPOS.SAMPLE_CONTENTS.id pair_status=kept_candidate -->
| 6 | CALIFORNIA_TRAFFIC_COLLISION / sf_local015.sql | `mc."case_id" = p."case_id"` | raw / raw | 94,243 | 185,581 | 1,831 | 0.0194284987 | 0.00658649678 | exact |
<!-- left=CALIFORNIA_TRAFFIC_COLLISION.CALIFORNIA_TRAFFIC_COLLISION.COLLISIONS.case_id right=CALIFORNIA_TRAFFIC_COLLISION.CALIFORNIA_TRAFFIC_COLLISION.PARTIES.case_id pair_status=kept_candidate -->
| 7 | IDC / sf_bq346.sql | `seg."SOPInstanceUID" = r."seg_sop"` | raw / raw | 25,560 | 1,751,216 | 1,056 | 0.041314554 | 0.000594688352 | exact |
<!-- left=IDC.IDC_V17.SEGMENTATIONS.SOPInstanceUID right=IDC.IDC_V17.DICOM_ALL.SOPInstanceUID pair_status=filtered -->
| 8 | DEPS_DEV_V1 / sf_bq028.sql | `lp."ProjectName" = mp."ProjectName"` | raw / raw | 960,967 | 16,635 | 1,245 | 0.0748422002 | 0.00127514833 | exact |
<!-- left=DEPS_DEV_V1.DEPS_DEV_V1.PROJECTS.Name right=DEPS_DEV_V1.DEPS_DEV_V1.PACKAGEVERSIONTOPROJECT.ProjectName pair_status=kept_candidate -->
| 9 | ETHEREUM_BLOCKCHAIN / sf_bq187.sql | `aa.address = i.address` | raw / raw | 47,086 | 145,109 | 16,245 | 0.345007008 | 0.0923273657 | exact |
<!-- left=ETHEREUM_BLOCKCHAIN.ETHEREUM_BLOCKCHAIN.TOKEN_TRANSFERS.from_address right=ETHEREUM_BLOCKCHAIN.ETHEREUM_BLOCKCHAIN.TOKEN_TRANSFERS.to_address pair_status=kept_candidate -->
| 10 | PATENTSVIEW / sf_bq052.sql | `cfp."id" = usc."citation_id"` | raw / raw | 659,007 | 3,545,484 | 238,819 | 0.362392205 | 0.0602215715 | exact |
<!-- left=PATENTSVIEW.PATENTSVIEW.PATENT.id right=PATENTSVIEW.PATENTSVIEW.USPATENTCITATION.citation_id pair_status=kept_candidate -->
| 11 | DEPS_DEV_V1 / sf_bq028.sql | `mp."Name" = lr."Name"` | raw / raw | 43,203 | 2,054,129 | 16,663 | 0.385690809 | 0.00800848189 | exact |
<!-- left=DEPS_DEV_V1.DEPS_DEV_V1.PACKAGEVERSIONTOPROJECT.Name right=DEPS_DEV_V1.DEPS_DEV_V1.PACKAGEVERSIONS.Name pair_status=filtered -->
| 12 | DEPS_DEV_V1 / sf_bq028.sql | `mp."Version" = lr."Version"` | raw / raw | 105,962 | 1,457,888 | 46,083 | 0.434901191 | 0.0303623679 | exact |
<!-- left=DEPS_DEV_V1.DEPS_DEV_V1.PACKAGEVERSIONTOPROJECT.Version right=DEPS_DEV_V1.DEPS_DEV_V1.PACKAGEVERSIONS.Version pair_status=kept_candidate -->
| 13 | META_KAGGLE / sf_bq167.sql | `"d2"."FROM_USER_ID" = "d"."TO_USER_ID"` | raw / raw | 346,693 | 178,223 | 105,791 | 0.593587809 | 0.252409186 | exact |
<!-- left=META_KAGGLE.META_KAGGLE.FORUMMESSAGEVOTES.FromUserId right=META_KAGGLE.META_KAGGLE.FORUMMESSAGEVOTES.ToUserId pair_status=filtered -->
| 14 | PATENTSVIEW / sf_bq246.sql | `app_cited."patent_id" = b."citation_id"` | raw / raw | 7,903,067 | 3,545,484 | 2,861,544 | 0.807095449 | 0.33324114 | exact |
<!-- left=PATENTSVIEW.PATENTSVIEW.APPLICATION.patent_id right=PATENTSVIEW.PATENTSVIEW.USPATENTCITATION.citation_id pair_status=kept_candidate -->
| 15 | GITHUB_REPOS / sf_bq255.sql | `"sc"."repo_name" = "licensed_repos"."repo_name"` | raw / raw | 6 | 3,325,550 | 5 | 0.833333333 | 1.50351025e-06 | exact |
<!-- left=GITHUB_REPOS.GITHUB_REPOS.SAMPLE_COMMITS.repo_name right=GITHUB_REPOS.GITHUB_REPOS.LICENSES.repo_name pair_status=kept_candidate -->
| 16 | GITHUB_REPOS / sf_bq255.sql | `"sc"."repo_name" = "shell_repos"."repo_name"` | raw / raw | 6 | 3,325,550 | 5 | 0.833333333 | 1.50351025e-06 | exact |
<!-- left=GITHUB_REPOS.GITHUB_REPOS.SAMPLE_COMMITS.repo_name right=GITHUB_REPOS.GITHUB_REPOS.LANGUAGES.repo_name pair_status=kept_candidate -->
| 17 | GITHUB_REPOS / sf_bq193.sql | `rl."repo_name" = f."repo_name"` | raw / raw | 3,325,550 | 12,837 | 11,031 | 0.859312924 | 0.0033152449 | exact |
<!-- left=GITHUB_REPOS.GITHUB_REPOS.LANGUAGES.repo_name right=GITHUB_REPOS.GITHUB_REPOS.SAMPLE_CONTENTS.sample_repo_name pair_status=kept_candidate -->
| 18 | CRYPTO / sf_bq334.sql | `m."year" = t."year"` | extract / extract | 560,397 | 97,821 | 90,902 | 0.929268766 | 0.160231687 | exact |
<!-- left=CRYPTO.CRYPTO_BITCOIN.OUTPUTS.block_timestamp right=CRYPTO.CRYPTO_BITCOIN.TRANSACTIONS.block_timestamp pair_status=kept_candidate -->
| 19 | SAN_FRANCISCO_PLUS / sf_bq294.sql | `si."station_id" = CAST(t."start_station_id" AS VARCHAR)` | raw / cast | 472 | 311 | 290 | 0.932475884 | 0.588235294 | exact |
<!-- left=SAN_FRANCISCO_PLUS.SAN_FRANCISCO_BIKESHARE.BIKESHARE_STATION_INFO.station_id right=SAN_FRANCISCO_PLUS.SAN_FRANCISCO_BIKESHARE.BIKESHARE_TRIPS.start_station_id pair_status=kept_candidate -->
| 20 | TCGA_HG19_DATA_V0 / sf_bq150.sql | `e."sample_barcode" = m.sample_barcode` | raw / raw | 11,087 | 9,511 | 8,884 | 0.934076333 | 0.758408742 | exact |
<!-- left=TCGA_HG19_DATA_V0.TCGA_HG19_DATA_V0.RNASEQ_GENE_EXPRESSION_UNC_RSEM.sample_barcode right=TCGA_HG19_DATA_V0.TCGA_HG19_DATA_V0.SOMATIC_MUTATION_MC3.sample_barcode_tumor pair_status=kept_candidate -->
| 21 | CRYPTO / sf_bq334.sql | `m."year" = t."year"` | extract / extract | 564,520 | 97,821 | 92,486 | 0.945461608 | 0.162297427 | exact |
<!-- left=CRYPTO.CRYPTO_BITCOIN.INPUTS.block_timestamp right=CRYPTO.CRYPTO_BITCOIN.TRANSACTIONS.block_timestamp pair_status=kept_candidate -->
| 22 | NEW_YORK_CITIBIKE_1 / sf_bq050.sql | `czs."zip" = twz."start_zip"` | raw / cast | 192 | 33,113 | 186 | 0.96875 | 0.0056161116 | exact |
<!-- left=NEW_YORK_CITIBIKE_1.CYCLISTIC.ZIP_CODES.zip right=NEW_YORK_CITIBIKE_1.GEO_US_BOUNDARIES.ZIP_CODES.zip_code pair_status=filtered -->
| 23 | TCGA_HG38_DATA_V0 / sf_bq155.sql | `T1."case_barcode" = T2."case_barcode"` | raw / raw | 10,237 | 10,250 | 9,929 | 0.96991306 | 0.940424323 | exact |
<!-- left=TCGA_HG38_DATA_V0.TCGA_HG38_DATA_V0.RNASEQ_GENE_EXPRESSION.case_barcode right=TCGA_HG38_DATA_V0.TCGA_HG38_DATA_V0.MIRNASEQ_EXPRESSION.case_barcode pair_status=kept_candidate -->
| 24 | PANCANCER_ATLAS_1 / sf_bq153.sql | `T1."bcr_patient_barcode" = T2."ParticipantBarcode"` | raw / raw | 10,761 | 9,921 | 9,711 | 0.978832779 | 0.885151764 | exact |
<!-- left=PANCANCER_ATLAS_1.PANCANCER_ATLAS_FILTERED.CLINICAL_PANCAN_PATIENT_WITH_FOLLOWUP_FILTERED.bcr_patient_barcode right=PANCANCER_ATLAS_1.PANCANCER_ATLAS_FILTERED.EBPP_ADJUSTPANCAN_ILLUMINAHISEQ_RNASEQV2_GENEXP_FILTERED.ParticipantBarcode pair_status=kept_candidate -->
| 25 | PANCANCER_ATLAS_1 / sf_bq158.sql | `p."ParticipantBarcode" = cb."ParticipantBarcode"` | raw / raw | 10,063 | 10,761 | 9,854 | 0.979230846 | 0.898268004 | exact |
<!-- left=PANCANCER_ATLAS_1.PANCANCER_ATLAS_FILTERED.MC3_MAF_V5_ONE_PER_TUMOR_SAMPLE.ParticipantBarcode right=PANCANCER_ATLAS_1.PANCANCER_ATLAS_FILTERED.CLINICAL_PANCAN_PATIENT_WITH_FOLLOWUP_FILTERED.bcr_patient_barcode pair_status=kept_candidate -->
| 26 | META_KAGGLE / sf_bq167.sql | `"u_from"."Id" = "d"."FROM_USER_ID"` | raw / raw | 19,506,372 | 346,693 | 342,789 | 0.988739317 | 0.0175696643 | exact |
<!-- left=META_KAGGLE.META_KAGGLE.USERS.Id right=META_KAGGLE.META_KAGGLE.FORUMMESSAGEVOTES.FromUserId pair_status=kept_candidate -->
| 27 | META_KAGGLE / sf_bq167.sql | `"u_to"."Id" = "d"."TO_USER_ID"` | raw / raw | 19,506,372 | 178,223 | 176,337 | 0.989417752 | 0.00903909514 | exact |
<!-- left=META_KAGGLE.META_KAGGLE.USERS.Id right=META_KAGGLE.META_KAGGLE.FORUMMESSAGEVOTES.ToUserId pair_status=filtered -->
| 28 | IDC / sf_bq390.sql | `T1."StudyInstanceUID" = T2."StudyInstanceUID"` | raw / raw | 111,829 | 11,147 | 11,104 | 0.99614246 | 0.0992562929 | exact |
<!-- left=IDC.IDC_V17.DICOM_ALL.StudyInstanceUID right=IDC.IDC_V17.SEGMENTATIONS.StudyInstanceUID pair_status=filtered -->
| 29 | TCGA / sf_bq043.sql | `c."submitter_id" = m."case_barcode"` | raw / raw | 11,428 | 10,513 | 10,476 | 0.996480548 | 0.913737462 | exact |
<!-- left=TCGA.TCGA_VERSIONED.CLINICAL_GDC_R39.submitter_id right=TCGA.TCGA_VERSIONED.SOMATIC_MUTATION_HG19_DCC_2017_02.case_barcode pair_status=filtered -->
| 30 | SQLITE_SAKILA / sf_local195.sql | `I."film_id" = FA."film_id"` | raw / raw | 958 | 997 | 955 | 0.996868476 | 0.955 | exact |
<!-- left=SQLITE_SAKILA.SQLITE_SAKILA.INVENTORY.film_id right=SQLITE_SAKILA.SQLITE_SAKILA.FILM_ACTOR.film_id pair_status=kept_candidate -->
| 31 | IPL / sf_local026.sql | `"t3wb"."bowler" = "p"."player_id"` | raw / raw | 330 | 468 | 329 | 0.996969697 | 0.701492537 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.bowler right=IPL.IPL.PLAYER.player_id pair_status=filtered -->
| 32 | DEATH / sf_bq072.sql | `"icd"."Code" = "e"."Icd10Code"` | raw / raw | 12,131 | 5,384 | 5,371 | 0.997585438 | 0.442276021 | exact |
<!-- left=DEATH.DEATH.ICD10CODE.Code right=DEATH.DEATH.ENTITYAXISCONDITIONS.Icd10Code pair_status=kept_candidate -->
| 33 | IPL / sf_local022.sql | `"P"."player_id" = "PR"."player_id"` | raw / raw | 468 | 434 | 433 | 0.997695853 | 0.923240938 | exact |
<!-- left=IPL.IPL.PLAYER.player_id right=IPL.IPL.BALL_BY_BALL.striker pair_status=filtered -->
| 34 | CENSUS_BUREAU_ACS_2 / sf_bq429.sql | `T1."geo_id" = T3."zip_code"` | raw / raw | 33,120 | 33,113 | 33,089 | 0.999275209 | 0.998340574 | exact |
<!-- left=CENSUS_BUREAU_ACS_2.CENSUS_BUREAU_ACS.ZCTA5_2015_5YR.geo_id right=CENSUS_BUREAU_ACS_2.GEO_US_BOUNDARIES.ZIP_CODES.zip_code pair_status=filtered -->
| 35 | US_ADDRESSES__POI / sf040.sql | `a."ID_ZIP" = z."ZIP_GEO_ID"` | raw / raw | 38,056 | 569,503 | 38,043 | 0.999658398 | 0.0667988257 | exact |
<!-- left=US_ADDRESSES__POI.CYBERSYN.US_ADDRESSES.ID_ZIP right=US_ADDRESSES__POI.CYBERSYN.GEOGRAPHY_RELATIONSHIPS.RELATED_GEO_ID pair_status=kept_candidate -->
| 36 | PATENTSVIEW / sf_bq052.sql | `cfp."id" = core_app."patent_id"` | raw / raw | 659,007 | 7,903,067 | 658,842 | 0.999749623 | 0.0833636163 | exact |
<!-- left=PATENTSVIEW.PATENTSVIEW.PATENT.id right=PATENTSVIEW.PATENTSVIEW.APPLICATION.patent_id pair_status=kept_candidate -->
| 37 | US_ADDRESSES__POI / sf040.sql | `c."GEO_ID" = f."ZIP_GEO_ID"` | raw / raw | 212,577 | 569,503 | 212,573 | 0.999981183 | 0.373257923 | exact |
<!-- left=US_ADDRESSES__POI.CYBERSYN.GEOGRAPHY_CHARACTERISTICS.GEO_ID right=US_ADDRESSES__POI.CYBERSYN.GEOGRAPHY_RELATIONSHIPS.RELATED_GEO_ID pair_status=kept_candidate -->
| 38 | AIRLINES / sf_local009.sql | `arr."airport_code" = f."arrival_airport"` | raw / raw | 104 | 104 | 104 | 1 | 1 | exact |
<!-- left=AIRLINES.AIRLINES.AIRPORTS_DATA.airport_code right=AIRLINES.AIRLINES.FLIGHTS.arrival_airport pair_status=kept_candidate -->
| 39 | AIRLINES / sf_local009.sql | `dep."airport_code" = f."departure_airport"` | raw / raw | 104 | 104 | 104 | 1 | 1 | exact |
<!-- left=AIRLINES.AIRLINES.AIRPORTS_DATA.airport_code right=AIRLINES.AIRLINES.FLIGHTS.departure_airport pair_status=kept_candidate -->
| 40 | BANK_SALES_TRADING / sf_local075.sql | `e."page_id" = p."page_id"` | raw / raw | 13 | 13 | 13 | 1 | 1 | exact |
<!-- left=BANK_SALES_TRADING.BANK_SALES_TRADING.SHOPPING_CART_EVENTS.page_id right=BANK_SALES_TRADING.BANK_SALES_TRADING.SHOPPING_CART_PAGE_HIERARCHY.page_id pair_status=kept_candidate -->
| 41 | BANK_SALES_TRADING / sf_local285.sql | `w."item_code" = c."item_code"` | raw / raw | 251 | 251 | 251 | 1 | 1 | exact |
<!-- left=BANK_SALES_TRADING.BANK_SALES_TRADING.VEG_WHSLE_DF.item_code right=BANK_SALES_TRADING.BANK_SALES_TRADING.VEG_CAT.item_code pair_status=kept_candidate -->
| 42 | BANK_SALES_TRADING / sf_local285.sql | `w."item_code" = l."item_code"` | raw / raw | 251 | 251 | 251 | 1 | 1 | exact |
<!-- left=BANK_SALES_TRADING.BANK_SALES_TRADING.VEG_WHSLE_DF.item_code right=BANK_SALES_TRADING.BANK_SALES_TRADING.VEG_LOSS_RATE_DF.item_code pair_status=kept_candidate -->
| 43 | BANK_SALES_TRADING / sf_local285.sql | `w."item_code" = t."item_code"` | raw / raw | 251 | 246 | 246 | 1 | 0.980079681 | exact |
<!-- left=BANK_SALES_TRADING.BANK_SALES_TRADING.VEG_WHSLE_DF.item_code right=BANK_SALES_TRADING.BANK_SALES_TRADING.VEG_TXN_DF.item_code pair_status=kept_candidate -->
| 44 | BANK_SALES_TRADING / sf_local285.sql | `w."whsle_date" = t."txn_date"` | raw / raw | 1,091 | 1,085 | 1,085 | 1 | 0.994500458 | exact |
<!-- left=BANK_SALES_TRADING.BANK_SALES_TRADING.VEG_WHSLE_DF.whsle_date right=BANK_SALES_TRADING.BANK_SALES_TRADING.VEG_TXN_DF.txn_date pair_status=kept_candidate -->
| 45 | BRAZILIAN_E_COMMERCE / sf_local030.sql | `O."customer_id" = C."customer_id"` | raw / raw | 99,441 | 99,441 | 99,441 | 1 | 1 | exact |
<!-- left=BRAZILIAN_E_COMMERCE.BRAZILIAN_E_COMMERCE.OLIST_ORDERS.customer_id right=BRAZILIAN_E_COMMERCE.BRAZILIAN_E_COMMERCE.OLIST_CUSTOMERS.customer_id pair_status=kept_candidate -->
| 46 | BRAZILIAN_E_COMMERCE / sf_local030.sql | `O."order_id" = P."order_id"` | raw / raw | 99,441 | 99,440 | 99,440 | 1 | 0.999989944 | exact |
<!-- left=BRAZILIAN_E_COMMERCE.BRAZILIAN_E_COMMERCE.OLIST_ORDERS.order_id right=BRAZILIAN_E_COMMERCE.BRAZILIAN_E_COMMERCE.OLIST_ORDER_PAYMENTS.order_id pair_status=kept_candidate -->
| 47 | CENSUS_BUREAU_ACS_2 / sf_bq429.sql | `T1."geo_id" = T2."geo_id"` | raw / raw | 33,120 | 33,120 | 33,120 | 1 | 1 | exact |
<!-- left=CENSUS_BUREAU_ACS_2.CENSUS_BUREAU_ACS.ZCTA5_2015_5YR.geo_id right=CENSUS_BUREAU_ACS_2.CENSUS_BUREAU_ACS.ZCTA5_2018_5YR.geo_id pair_status=kept_candidate -->
| 48 | CENSUS_BUREAU_ACS_2 / sf_bq429.sql | `T1."geo_id" = T2."geo_id"` | raw / raw | 33,120 | 33,120 | 33,120 | 1 | 1 | exact |
<!-- left=CENSUS_BUREAU_ACS_2.CENSUS_BUREAU_ACS.ZCTA5_2015_5YR.geo_id right=CENSUS_BUREAU_ACS_2.CENSUS_BUREAU_ACS.ZCTA5_2017_5YR.geo_id pair_status=kept_candidate -->
| 49 | CENSUS_GALAXY__AIML_MODEL_DATA_ENRICHMENT_SAMPLE / sf014.sql | `f."ZipCode" = l."ZipCode"` | raw / raw | 2,056 | 2,056 | 2,056 | 1 | 1 | exact |
<!-- left=CENSUS_GALAXY__AIML_MODEL_DATA_ENRICHMENT_SAMPLE.PUBLIC.Fact_CensusValues_ACS2021_ByZip.ZipCode right=CENSUS_GALAXY__AIML_MODEL_DATA_ENRICHMENT_SAMPLE.PUBLIC.LU_GeographyExpanded.ZipCode pair_status=kept_candidate -->
| 50 | CENSUS_GALAXY__ZIP_CODE_TO_BLOCK_GROUP_SAMPLE / sf011.sql | `T1."BlockGroupID" = T2."BlockGroupID"` | raw / raw | 16,070 | 16,070 | 16,070 | 1 | 1 | exact |
<!-- left=CENSUS_GALAXY__ZIP_CODE_TO_BLOCK_GROUP_SAMPLE.PUBLIC.Dim_CensusGeography.BlockGroupID right=CENSUS_GALAXY__ZIP_CODE_TO_BLOCK_GROUP_SAMPLE.PUBLIC.Fact_CensusValues_ACS2021.BlockGroupID pair_status=kept_candidate -->
| 51 | DEATH / sf_bq072.sql | `"dr"."Id" = "e"."DeathRecordId"` | raw / raw | 2,631,171 | 2,631,171 | 2,631,171 | 1 | 1 | exact |
<!-- left=DEATH.DEATH.DEATHRECORDS.Id right=DEATH.DEATH.ENTITYAXISCONDITIONS.DeathRecordId pair_status=kept_candidate -->
| 52 | DEATH / sf_bq072.sql | `"r"."Code" = "dr"."Race"` | raw / raw | 16 | 14 | 14 | 1 | 0.875 | exact |
<!-- left=DEATH.DEATH.RACE.Code right=DEATH.DEATH.DEATHRECORDS.Race pair_status=filtered -->
| 53 | DELIVERY_CENTER / sf_local209.sql | `s."store_id" = ts."store_id"` | raw / raw | 951 | 951 | 951 | 1 | 1 | exact |
<!-- left=DELIVERY_CENTER.DELIVERY_CENTER.STORES.store_id right=DELIVERY_CENTER.DELIVERY_CENTER.ORDERS.store_id pair_status=filtered -->
| 54 | DELIVERY_CENTER / sf_local210.sql | `s."hub_id" = h."hub_id"` | raw / raw | 32 | 32 | 32 | 1 | 1 | exact |
<!-- left=DELIVERY_CENTER.DELIVERY_CENTER.STORES.hub_id right=DELIVERY_CENTER.DELIVERY_CENTER.HUBS.hub_id pair_status=kept_candidate -->
| 55 | EU_SOCCER / sf_local283.sql | `l."country_id" = c."id"` | raw / raw | 11 | 11 | 11 | 1 | 1 | exact |
<!-- left=EU_SOCCER.EU_SOCCER.LEAGUE.country_id right=EU_SOCCER.EU_SOCCER.COUNTRY.id pair_status=kept_candidate -->
| 56 | EU_SOCCER / sf_local283.sql | `rc."league_id" = l."id"` | raw / raw | 11 | 11 | 11 | 1 | 1 | exact |
<!-- left=EU_SOCCER.EU_SOCCER.MATCH.league_id right=EU_SOCCER.EU_SOCCER.LEAGUE.id pair_status=kept_candidate -->
| 57 | EU_SOCCER / sf_local283.sql | `tsp."team_api_id" = t."team_api_id"` | raw / raw | 299 | 299 | 299 | 1 | 1 | exact |
<!-- left=EU_SOCCER.EU_SOCCER.MATCH.home_team_api_id right=EU_SOCCER.EU_SOCCER.TEAM.team_api_id pair_status=kept_candidate -->
| 58 | EU_SOCCER / sf_local283.sql | `tsp."team_api_id" = t."team_api_id"` | raw / raw | 299 | 299 | 299 | 1 | 1 | exact |
<!-- left=EU_SOCCER.EU_SOCCER.MATCH.away_team_api_id right=EU_SOCCER.EU_SOCCER.TEAM.team_api_id pair_status=kept_candidate -->
| 59 | E_COMMERCE / sf_local003.sql | `C."customer_id" = O."customer_id"` | raw / raw | 99,441 | 99,441 | 99,441 | 1 | 1 | exact |
<!-- left=E_COMMERCE.E_COMMERCE.CUSTOMERS.customer_id right=E_COMMERCE.E_COMMERCE.ORDERS.customer_id pair_status=kept_candidate -->
| 60 | E_COMMERCE / sf_local003.sql | `O."order_id" = P."order_id"` | raw / raw | 99,441 | 99,440 | 99,440 | 1 | 0.999989944 | exact |
<!-- left=E_COMMERCE.E_COMMERCE.ORDERS.order_id right=E_COMMERCE.E_COMMERCE.ORDER_PAYMENTS.order_id pair_status=kept_candidate -->
| 61 | F1 / sf_local309.sql | `cp."constructor_id" = c."constructor_id"` | raw / raw | 211 | 212 | 211 | 1 | 0.995283019 | exact |
<!-- left=F1.F1.RESULTS.constructor_id right=F1.F1.CONSTRUCTORS.constructor_id pair_status=kept_candidate -->
| 62 | F1 / sf_local309.sql | `cp."constructor_id" = c."constructor_id"` | raw / raw | 12 | 212 | 12 | 1 | 0.0566037736 | exact |
<!-- left=F1.F1.SPRINT_RESULTS.constructor_id right=F1.F1.CONSTRUCTORS.constructor_id pair_status=kept_candidate -->
| 63 | F1 / sf_local309.sql | `cs."constructor_id" = c."constructor_id"` | raw / raw | 160 | 212 | 160 | 1 | 0.754716981 | exact |
<!-- left=F1.F1.CONSTRUCTOR_STANDINGS.constructor_id right=F1.F1.CONSTRUCTORS.constructor_id pair_status=kept_candidate -->
| 64 | F1 / sf_local309.sql | `cs."race_id" = fr."race_id"` | raw / raw | 1,049 | 1,125 | 1,049 | 1 | 0.932444444 | exact |
<!-- left=F1.F1.CONSTRUCTOR_STANDINGS.race_id right=F1.F1.RACES.race_id pair_status=filtered -->
| 65 | F1 / sf_local309.sql | `dp."driver_id" = d."driver_id"` | raw / raw | 859 | 859 | 859 | 1 | 1 | exact |
<!-- left=F1.F1.RESULTS.driver_id right=F1.F1.DRIVERS.driver_id pair_status=kept_candidate -->
| 66 | F1 / sf_local309.sql | `dp."driver_id" = d."driver_id"` | raw / raw | 29 | 859 | 29 | 1 | 0.0337601863 | exact |
<!-- left=F1.F1.SPRINT_RESULTS.driver_id right=F1.F1.DRIVERS.driver_id pair_status=kept_candidate -->
| 67 | F1 / sf_local309.sql | `dp."points" = md."max_points"` | aggregate / aggregate | 39 | 9 | 9 | 1 | 0.230769231 | exact |
<!-- left=F1.F1.RESULTS.points right=F1.F1.SPRINT_RESULTS.points pair_status=kept_candidate -->
| 68 | F1 / sf_local309.sql | `ds."driver_id" = d."driver_id"` | raw / raw | 852 | 859 | 852 | 1 | 0.99185099 | exact |
<!-- left=F1.F1.DRIVER_STANDINGS.driver_id right=F1.F1.DRIVERS.driver_id pair_status=kept_candidate -->
| 69 | F1 / sf_local309.sql | `ds."race_id" = fr."race_id"` | raw / raw | 1,113 | 1,125 | 1,113 | 1 | 0.989333333 | exact |
<!-- left=F1.F1.DRIVER_STANDINGS.race_id right=F1.F1.RACES.race_id pair_status=filtered -->
| 70 | F1 / sf_local309.sql | `res."race_id" = r."race_id"` | raw / raw | 1,113 | 1,125 | 1,113 | 1 | 0.989333333 | exact |
<!-- left=F1.F1.RESULTS.race_id right=F1.F1.RACES.race_id pair_status=filtered -->
| 71 | F1 / sf_local309.sql | `sr."race_id" = r."race_id"` | raw / raw | 15 | 1,125 | 15 | 1 | 0.0133333333 | exact |
<!-- left=F1.F1.SPRINT_RESULTS.race_id right=F1.F1.RACES.race_id pair_status=filtered -->
| 72 | F1 / sf_local311.sql | `bdpc."driver_id" = fds."driver_id"` | raw / raw | 859 | 852 | 852 | 1 | 0.99185099 | exact |
<!-- left=F1.F1.RESULTS.driver_id right=F1.F1.DRIVER_STANDINGS.driver_id pair_status=kept_candidate -->
| 73 | F1 / sf_local311.sql | `fcs."constructor_id" = bdpc."constructor_id"` | raw / raw | 160 | 211 | 160 | 1 | 0.758293839 | exact |
<!-- left=F1.F1.CONSTRUCTOR_STANDINGS.constructor_id right=F1.F1.RESULTS.constructor_id pair_status=kept_candidate -->
| 74 | F1 / sf_local336.sql | `g1."driver_id" = c1."driver_id"` | raw / raw | 859 | 859 | 859 | 1 | 1 | exact |
<!-- left=F1.F1.RESULTS.driver_id right=F1.F1.LAP_POSITIONS.driver_id pair_status=kept_candidate -->
| 75 | F1 / sf_local336.sql | `g1."race_id" = c1."race_id"` | raw / raw | 1,113 | 1,113 | 1,113 | 1 | 1 | exact |
<!-- left=F1.F1.RESULTS.race_id right=F1.F1.LAP_POSITIONS.race_id pair_status=filtered -->
| 76 | F1 / sf_local354.sql | `t.first_constructor_id = c."constructor_id"` | aggregate / raw | 24 | 212 | 24 | 1 | 0.113207547 | exact |
<!-- left=F1.F1.RACES.round right=F1.F1.CONSTRUCTORS.constructor_id pair_status=filtered -->
| 77 | IPL / sf_local022.sql | `"B"."ball_id" = "BS"."ball_id"` | raw / raw | 9 | 9 | 9 | 1 | 1 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.ball_id right=IPL.IPL.BATSMAN_SCORED.ball_id pair_status=kept_candidate -->
| 78 | IPL / sf_local022.sql | `"B"."innings_no" = "BS"."innings_no"` | raw / raw | 2 | 2 | 2 | 1 | 1 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.innings_no right=IPL.IPL.BATSMAN_SCORED.innings_no pair_status=kept_candidate -->
| 79 | IPL / sf_local022.sql | `"B"."match_id" = "BS"."match_id"` | raw / raw | 568 | 568 | 568 | 1 | 1 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.match_id right=IPL.IPL.BATSMAN_SCORED.match_id pair_status=kept_candidate -->
| 80 | IPL / sf_local022.sql | `"B"."over_id" = "BS"."over_id"` | raw / raw | 20 | 20 | 20 | 1 | 1 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.over_id right=IPL.IPL.BATSMAN_SCORED.over_id pair_status=kept_candidate -->
| 81 | IPL / sf_local022.sql | `"M"."match_id" = "PR"."match_id"` | raw / raw | 567 | 568 | 567 | 1 | 0.998239437 | exact |
<!-- left=IPL.IPL.MATCH.match_id right=IPL.IPL.BALL_BY_BALL.match_id pair_status=kept_candidate -->
| 82 | IPL / sf_local022.sql | `"PM"."match_id" = "PR"."match_id"` | raw / raw | 568 | 568 | 568 | 1 | 1 | exact |
<!-- left=IPL.IPL.PLAYER_MATCH.match_id right=IPL.IPL.BALL_BY_BALL.match_id pair_status=kept_candidate -->
| 83 | IPL / sf_local022.sql | `"PM"."player_id" = "PR"."player_id"` | raw / raw | 468 | 434 | 434 | 1 | 0.927350427 | exact |
<!-- left=IPL.IPL.PLAYER_MATCH.player_id right=IPL.IPL.BALL_BY_BALL.striker pair_status=filtered -->
| 84 | IPL / sf_local026.sql | `"bbb"."ball_id" = "er"."ball_id"` | raw / raw | 9 | 9 | 9 | 1 | 1 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.ball_id right=IPL.IPL.EXTRA_RUNS.ball_id pair_status=kept_candidate -->
| 85 | IPL / sf_local026.sql | `"bbb"."innings_no" = "er"."innings_no"` | raw / raw | 2 | 2 | 2 | 1 | 1 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.innings_no right=IPL.IPL.EXTRA_RUNS.innings_no pair_status=kept_candidate -->
| 86 | IPL / sf_local026.sql | `"bbb"."match_id" = "er"."match_id"` | raw / raw | 568 | 568 | 568 | 1 | 1 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.match_id right=IPL.IPL.EXTRA_RUNS.match_id pair_status=kept_candidate -->
| 87 | IPL / sf_local026.sql | `"bbb"."over_id" = "er"."over_id"` | raw / raw | 20 | 20 | 20 | 1 | 1 | exact |
<!-- left=IPL.IPL.BALL_BY_BALL.over_id right=IPL.IPL.EXTRA_RUNS.over_id pair_status=kept_candidate -->
| 88 | IPL / sf_local026.sql | `"or"."total_runs_conceded" = "mrpm"."max_runs_conceded_in_single_over"` | aggregate / aggregate | 7 | 5 | 5 | 1 | 0.714285714 | exact |
<!-- left=IPL.IPL.BATSMAN_SCORED.runs_scored right=IPL.IPL.EXTRA_RUNS.extra_runs pair_status=kept_candidate -->
| 89 | MUSIC / sf_local244.sql | `tl."TrackId" = rv."TrackId"` | raw / raw | 3,503 | 1,984 | 1,984 | 1 | 0.566371681 | exact |
<!-- left=MUSIC.MUSIC.TRACK.TrackId right=MUSIC.MUSIC.INVOICELINE.TrackId pair_status=kept_candidate -->
| 90 | PAGILA / sf_local038.sql | `"a"."actor_id" = "fa"."actor_id"` | raw / raw | 200 | 200 | 200 | 1 | 1 | exact |
<!-- left=PAGILA.PAGILA.ACTOR.actor_id right=PAGILA.PAGILA.FILM_ACTOR.actor_id pair_status=kept_candidate -->
| 91 | PAGILA / sf_local038.sql | `"c"."category_id" = "fc"."category_id"` | raw / raw | 16 | 16 | 16 | 1 | 1 | exact |
<!-- left=PAGILA.PAGILA.CATEGORY.category_id right=PAGILA.PAGILA.FILM_CATEGORY.category_id pair_status=kept_candidate -->
| 92 | PAGILA / sf_local038.sql | `"f"."language_id" = "l"."language_id"` | raw / raw | 1 | 6 | 1 | 1 | 0.166666667 | exact |
<!-- left=PAGILA.PAGILA.FILM.language_id right=PAGILA.PAGILA.LANGUAGE.language_id pair_status=kept_candidate -->
| 93 | PAGILA / sf_local038.sql | `"fa"."film_id" = "f"."film_id"` | raw / raw | 997 | 1,000 | 997 | 1 | 0.997 | exact |
<!-- left=PAGILA.PAGILA.FILM_ACTOR.film_id right=PAGILA.PAGILA.FILM.film_id pair_status=kept_candidate -->
| 94 | PAGILA / sf_local038.sql | `"fc"."film_id" = "f"."film_id"` | raw / raw | 1,000 | 1,000 | 1,000 | 1 | 1 | exact |
<!-- left=PAGILA.PAGILA.FILM_CATEGORY.film_id right=PAGILA.PAGILA.FILM.film_id pair_status=kept_candidate -->
| 95 | PAGILA / sf_local039.sql | `a."city_id" = ci."city_id"` | raw / raw | 599 | 600 | 599 | 1 | 0.998333333 | exact |
<!-- left=PAGILA.PAGILA.ADDRESS.city_id right=PAGILA.PAGILA.CITY.city_id pair_status=filtered -->
| 96 | PAGILA / sf_local039.sql | `cu."address_id" = a."address_id"` | raw / raw | 599 | 603 | 599 | 1 | 0.993366501 | exact |
<!-- left=PAGILA.PAGILA.CUSTOMER.address_id right=PAGILA.PAGILA.ADDRESS.address_id pair_status=kept_candidate -->
| 97 | PAGILA / sf_local039.sql | `i."film_id" = fc."film_id"` | raw / raw | 958 | 1,000 | 958 | 1 | 0.958 | exact |
<!-- left=PAGILA.PAGILA.INVENTORY.film_id right=PAGILA.PAGILA.FILM_CATEGORY.film_id pair_status=kept_candidate -->
| 98 | PAGILA / sf_local039.sql | `r."customer_id" = cu."customer_id"` | raw / raw | 599 | 599 | 599 | 1 | 1 | exact |
<!-- left=PAGILA.PAGILA.RENTAL.customer_id right=PAGILA.PAGILA.CUSTOMER.customer_id pair_status=filtered -->
| 99 | PAGILA / sf_local039.sql | `r."inventory_id" = i."inventory_id"` | raw / raw | 4,580 | 4,581 | 4,580 | 1 | 0.999781707 | exact |
<!-- left=PAGILA.PAGILA.RENTAL.inventory_id right=PAGILA.PAGILA.INVENTORY.inventory_id pair_status=filtered -->
| 100 | PATENTSVIEW / sf_bq052.sql | `p."id" = cpc."patent_id"` | raw / raw | 659,007 | 596,307 | 596,307 | 1 | 0.904856853 | exact |
<!-- left=PATENTSVIEW.PATENTSVIEW.PATENT.id right=PATENTSVIEW.PATENTSVIEW.CPC_CURRENT.patent_id pair_status=kept_candidate -->
| 101 | PATENTSVIEW / sf_bq052.sql | `usc."patent_id" = citing_app."patent_id"` | raw / raw | 616,128 | 7,903,067 | 616,128 | 1 | 0.0779606196 | exact |
<!-- left=PATENTSVIEW.PATENTSVIEW.USPATENTCITATION.patent_id right=PATENTSVIEW.PATENTSVIEW.APPLICATION.patent_id pair_status=kept_candidate -->
| 102 | PATENTSVIEW / sf_bq128.sql | `bc."patent_id" = b."patent_id"` | raw / raw | 616,128 | 659,007 | 616,128 | 1 | 0.934933923 | exact |
<!-- left=PATENTSVIEW.PATENTSVIEW.USPATENTCITATION.patent_id right=PATENTSVIEW.PATENTSVIEW.PATENT.id pair_status=kept_candidate -->
| 103 | PATENTS_GOOGLE / sf_bq127.sql | `"A"."publication_number" = "FP"."publication_number"` | raw / trim | 469,075 | 469,075 | 469,075 | 1 | 1 | exact |
<!-- left=PATENTS_GOOGLE.PATENTS_GOOGLE.ABS_AND_EMB.publication_number right=PATENTS_GOOGLE.PATENTS_GOOGLE.PUBLICATIONS.publication_number pair_status=kept_candidate -->
| 104 | SAN_FRANCISCO_PLUS / sf_bq294.sql | `r."region_id" = si."region_id"` | raw / raw | 6 | 5 | 5 | 1 | 0.833333333 | exact |
<!-- left=SAN_FRANCISCO_PLUS.SAN_FRANCISCO_BIKESHARE.BIKESHARE_REGIONS.region_id right=SAN_FRANCISCO_PLUS.SAN_FRANCISCO_BIKESHARE.BIKESHARE_STATION_INFO.region_id pair_status=kept_candidate -->
| 105 | SQLITE_SAKILA / sf_local056.sql | `T1."customer_id" = T2."customer_id"` | raw / raw | 599 | 599 | 599 | 1 | 1 | exact |
<!-- left=SQLITE_SAKILA.SQLITE_SAKILA.CUSTOMER.customer_id right=SQLITE_SAKILA.SQLITE_SAKILA.PAYMENT.customer_id pair_status=kept_candidate -->
| 106 | SQLITE_SAKILA / sf_local194.sql | `a."actor_id" = fa."actor_id"` | raw / raw | 200 | 200 | 200 | 1 | 1 | exact |
<!-- left=SQLITE_SAKILA.SQLITE_SAKILA.ACTOR.actor_id right=SQLITE_SAKILA.SQLITE_SAKILA.FILM_ACTOR.actor_id pair_status=kept_candidate -->
| 107 | SQLITE_SAKILA / sf_local194.sql | `f."film_id" = i."film_id"` | raw / raw | 1,000 | 958 | 958 | 1 | 0.958 | exact |
<!-- left=SQLITE_SAKILA.SQLITE_SAKILA.FILM.film_id right=SQLITE_SAKILA.SQLITE_SAKILA.INVENTORY.film_id pair_status=kept_candidate -->
| 108 | SQLITE_SAKILA / sf_local194.sql | `fr."film_id" = fac."film_id"` | raw / raw | 1,000 | 997 | 997 | 1 | 0.997 | exact |
<!-- left=SQLITE_SAKILA.SQLITE_SAKILA.FILM.film_id right=SQLITE_SAKILA.SQLITE_SAKILA.FILM_ACTOR.film_id pair_status=kept_candidate -->
| 109 | SQLITE_SAKILA / sf_local194.sql | `i."inventory_id" = r."inventory_id"` | raw / raw | 4,581 | 4,580 | 4,580 | 1 | 0.999781707 | exact |
<!-- left=SQLITE_SAKILA.SQLITE_SAKILA.INVENTORY.inventory_id right=SQLITE_SAKILA.SQLITE_SAKILA.RENTAL.inventory_id pair_status=filtered -->
| 110 | SQLITE_SAKILA / sf_local194.sql | `r."rental_id" = p."rental_id"` | raw / raw | 16,044 | 16,044 | 16,044 | 1 | 1 | exact |
<!-- left=SQLITE_SAKILA.SQLITE_SAKILA.RENTAL.rental_id right=SQLITE_SAKILA.SQLITE_SAKILA.PAYMENT.rental_id pair_status=filtered -->
| 111 | SQLITE_SAKILA / sf_local199.sql | `r."staff_id" = s."staff_id"` | raw / raw | 2 | 2 | 2 | 1 | 1 | exact |
<!-- left=SQLITE_SAKILA.SQLITE_SAKILA.RENTAL.staff_id right=SQLITE_SAKILA.SQLITE_SAKILA.STAFF.staff_id pair_status=kept_candidate -->
| 112 | STACKING / sf_local263.sql | `ms."name" = lpm."name"` | raw / raw | 20 | 20 | 20 | 1 | 1 | exact |
<!-- left=STACKING.STACKING.MODEL_SCORE.name right=STACKING.STACKING.MODEL.name pair_status=kept_candidate -->
| 113 | STACKING / sf_local263.sql | `ms."version" = lpm."version"` | raw / raw | 8 | 8 | 8 | 1 | 1 | exact |
<!-- left=STACKING.STACKING.MODEL_SCORE.version right=STACKING.STACKING.MODEL.version pair_status=kept_candidate -->
| 114 | TCGA / sf_bq043.sql | `c."submitter_id" = m."case_barcode"` | raw / raw | 11,428 | 9,445 | 9,445 | 1 | 0.826478824 | exact |
<!-- left=TCGA.TCGA_VERSIONED.CLINICAL_GDC_R39.submitter_id right=TCGA.TCGA_VERSIONED.SOMATIC_MUTATION_HG19_MC3_2017_02.case_barcode pair_status=filtered -->
| 115 | TCGA / sf_bq043.sql | `e."case_barcode" = c."submitter_id"` | raw / raw | 10,293 | 11,428 | 10,293 | 1 | 0.900682534 | exact |
<!-- left=TCGA.TCGA_VERSIONED.RNASEQ_HG19_GDC_2017_02.case_barcode right=TCGA.TCGA_VERSIONED.CLINICAL_GDC_R39.submitter_id pair_status=filtered -->
| 116 | TCGA_HG38_DATA_V0 / sf_bq155.sql | `T1."case_barcode" = T2."case_barcode"` | raw / raw | 10,237 | 11,315 | 10,237 | 1 | 0.904728237 | exact |
<!-- left=TCGA_HG38_DATA_V0.TCGA_HG38_DATA_V0.RNASEQ_GENE_EXPRESSION.case_barcode right=TCGA_HG38_DATA_V0.TCGA_BIOCLIN_V0.CLINICAL.case_barcode pair_status=filtered -->
| 117 | TCGA_HG38_DATA_V0 / sf_bq155.sql | `T1."case_barcode" = T2."case_barcode"` | raw / raw | 10,250 | 11,315 | 10,250 | 1 | 0.905877154 | exact |
<!-- left=TCGA_HG38_DATA_V0.TCGA_HG38_DATA_V0.MIRNASEQ_EXPRESSION.case_barcode right=TCGA_HG38_DATA_V0.TCGA_BIOCLIN_V0.CLINICAL.case_barcode pair_status=filtered -->
| 118 | TCGA_MITELMAN / sf_bq166.sql | `s."chromosome" = b."chromosome"` | raw / raw | 24 | 24 | 24 | 1 | 1 | exact |
<!-- left=TCGA_MITELMAN.TCGA_VERSIONED.COPY_NUMBER_SEGMENT_ALLELIC_HG38_GDC_R23.chromosome right=TCGA_MITELMAN.PROD.CYTOBANDS_HG38.chromosome pair_status=filtered -->
| 119 | THELOOK_ECOMMERCE / sf_bq263.sql | `"OI"."order_id" = "O"."order_id"` | raw / raw | 124,847 | 124,847 | 124,847 | 1 | 1 | exact |
<!-- left=THELOOK_ECOMMERCE.THELOOK_ECOMMERCE.ORDER_ITEMS.order_id right=THELOOK_ECOMMERCE.THELOOK_ECOMMERCE.ORDERS.order_id pair_status=filtered -->
| 120 | THELOOK_ECOMMERCE / sf_bq263.sql | `"OI"."product_id" = "P"."id"` | raw / raw | 29,061 | 29,120 | 29,061 | 1 | 0.997973901 | exact |
<!-- left=THELOOK_ECOMMERCE.THELOOK_ECOMMERCE.ORDER_ITEMS.product_id right=THELOOK_ECOMMERCE.THELOOK_ECOMMERCE.PRODUCTS.id pair_status=kept_candidate -->
| 121 | THELOOK_ECOMMERCE / sf_bq265.sql | `u."user_id" = p."user_id"` | raw / raw | 100,000 | 79,815 | 79,815 | 1 | 0.79815 | exact |
<!-- left=THELOOK_ECOMMERCE.THELOOK_ECOMMERCE.USERS.id right=THELOOK_ECOMMERCE.THELOOK_ECOMMERCE.ORDERS.user_id pair_status=filtered -->
| 122 | THELOOK_ECOMMERCE / sf_bq271.sql | `"ORDER_ITEMS"."inventory_item_id" = "INVENTORY_ITEMS"."id"` | raw / raw | 180,581 | 488,040 | 180,581 | 1 | 0.370012704 | exact |
<!-- left=THELOOK_ECOMMERCE.THELOOK_ECOMMERCE.ORDER_ITEMS.inventory_item_id right=THELOOK_ECOMMERCE.THELOOK_ECOMMERCE.INVENTORY_ITEMS.id pair_status=kept_candidate -->
| 123 | WWE / sf_local019.sql | `m."loser_id" = CAST(w2."id" AS VARCHAR)` | raw / cast | 13,409 | 17,182 | 13,409 | 1 | 0.780409731 | exact |
<!-- left=WWE.WWE.MATCHES.loser_id right=WWE.WWE.WRESTLERS.id pair_status=kept_candidate -->
| 124 | WWE / sf_local019.sql | `m."winner_id" = CAST(w1."id" AS VARCHAR)` | raw / cast | 7,668 | 17,182 | 7,668 | 1 | 0.446280992 | exact |
<!-- left=WWE.WWE.MATCHES.winner_id right=WWE.WWE.WRESTLERS.id pair_status=kept_candidate -->
| 125 | FINANCE__ECONOMICS / sf002.sql | `"E"."ID_RSSD" = "BF"."ID_RSSD_NUM"` | raw / cast | - | - | - | - | - | statement_timeout |
<!-- left=FINANCE__ECONOMICS.CYBERSYN.FINANCIAL_INSTITUTION_ENTITIES.ID_RSSD right=FINANCE__ECONOMICS.CYBERSYN.FINANCIAL_INSTITUTION_TIMESERIES.ID_RSSD pair_status=kept_candidate -->
| 126 | GOOGLE_TRENDS / sf_bq104.sql | `b."refresh_date" = lr."refresh_date"` | raw / aggregate | - | - | - | - | - | statement_timeout |
<!-- left=GOOGLE_TRENDS.GOOGLE_TRENDS.TOP_RISING_TERMS.refresh_date right=GOOGLE_TRENDS.GOOGLE_TRENDS.INTERNATIONAL_TOP_RISING_TERMS.refresh_date pair_status=kept_candidate -->
| 127 | GOOGLE_TRENDS / sf_bq104.sql | `b."week" = t."target_week"` | raw / dateadd | - | - | - | - | - | statement_timeout |
<!-- left=GOOGLE_TRENDS.GOOGLE_TRENDS.TOP_RISING_TERMS.week right=GOOGLE_TRENDS.GOOGLE_TRENDS.INTERNATIONAL_TOP_RISING_TERMS.week pair_status=kept_candidate -->

## Interpretation Notes

- `overlap/min = |A intersection B| / min(|A|, |B|)` is the Pontis target metric.
- Jaccard can be very small even when `overlap/min` is high if the two column cardinalities are strongly skewed.
- Expression lineage such as `coalesce`, `cast`, or `extract` expands to physical source columns; its full-column metric is not necessarily the runtime expression-domain metric.
- A zero result means the current hosted Snowflake snapshot has no normalized physical-value intersection. It does not by itself prove that the gold SQL lineage extraction is semantically correct.
