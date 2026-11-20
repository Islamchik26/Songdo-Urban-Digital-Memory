# Evidence kit · Songdo Urban Digital Memory

Generated 2026-11-21T01:39:48+09:00 by `python scripts/evidence.py` from commit `a1045d9 (with uncommitted changes)`.
Every number below was produced by that run on a **new, disposable database** (`songdo_evidence`) built from this repository, not on a developer's working database. Rerun the command to reproduce.

## Gates

The gates of the midterm assessment ("Verification and staged remediation"), each with the run that proves it.

| Component | Verification | Status | Detail |
|---|---|---|---|
| PostgreSQL | Connection and version | PASS | PostgreSQL 18.3 on x86_64-windows |
| PostGIS | Extension | PASS | 3.7.0 |
| Boot / DDL / seed | psql ON_ERROR_STOP on a new database | PASS | 9 files, exit 0, 7.7 s |
| Schema | Objects created | PASS | 39 tables, 4 views, 3 materialized views, 3 exclusion constraints, 5 triggers |
| Quarantine | Bad input stored with a reason, API path included | PASS | CORE-1, CORE-2, api_contract.csv |
| GiST | Spatial index used | PASS | 8 tasks use a GiST index; see index_comparison.csv |
| API | Contract, idempotency, concurrency | PASS | 37/37 cases |
| Tests | unittest on the clean database | PASS | 72 tests, OK |
| Recovery | pg_restore and integrity comparison | PASS | 63/63 identical |
| Scenarios | Acceptance proofs | PASS | 29 pass, 3 limited, 0 fail |
| Web console | Every task run in a browser | PASS | 36/36 tasks |
| Backup | File | PASS | outputs/songdo_evidence.backup |

## Remediation items from the midterm feedback

| Item | Required proof | Evidence from this run |
|---|---|---|
| P0 Hash-only write | Staging, domain row and audit in one transaction; invalid input stored with a reason | PASS: audit step forced to fail -> 0 observation rows, 0 audit rows, staging REJECTED, payload quarantined with the error; batch still completes. Concurrent duplicates: 1 stored row(s). |
| P1 HTTP status / timestamp | Swapped coordinates and malformed time return 422; a valid request persists once | swapped -> 422, malformed time -> 422, no offset -> 422; valid -> 202 LOADED (1 stored row(s)), 3 retries -> DUPLICATE |
| P1 Assignment history | Exclusion rule in a versioned migration; overlap 23P01; adjacent succeeds | sql/03 `ex_assignment_no_overlap`. 3 consecutive assignments, each ends where the next starts; overlap rejected (23P01); adjacent interval accepted; inverted interval rejected (23514); concurrent overlap: see DB2-3-RACE. Race: two connections insert overlapping intervals at the same time: the second waited 0.50 s for the first to commit, then failed with ExclusionViolation (23P01); 1 row stored |
| P1 Safety progression | Lock, validate and append atomically; REPORTED -> RESOLVED rejected | sql/06 `transition_incident` (SELECT ... FOR UPDATE) and guard trigger. REPORTED -> RESOLVED rejected (23514); plain UPDATE bypassing the function rejected by trigger (23514); deleting history rejected (23001); accepted DISPATCHED -> status and history row written together (2 rows); concurrent transitions: see DB5-3-RACE. Race: two dispatches of one REPORTED incident at the same time: the second waited 0.50 s on the row lock (SELECT ... FOR UPDATE), then saw DISPATCHED and failed with CheckViolation (23514); history has exactly one DISPATCHED row |
| P1 Time and spatial paths | Bind T, stable ties, indexed geography, valid GeoJSON, boundary distances | T bound as a parameter; 16 vehicles, none observed after T; a later packet leaves the answer for T unchanged; same (vehicle, instant) stored once (UNIQUE), so no ties to break. same packet sent 3 times -> 1 stored row; radius 400 m around stop ICB365000721 (next stop 2734 m away): point at 399 m returns it (distance 399.0 m), point at 401 m does not. GeoJSON: FeatureCollection, 513 Point features, all inside the bbox: True |
| Federation | Nearest station by distance; alert only with a distance rule | rows [142, 132, 107]; transit x tourism max distance 498.1 m (cap 500); incident x stops max 599.9 m (cap 600); building station = true nearest for every device; alerts only from the incident's nearest station (0 exceptions) |

## Scenario acceptance matrix

One executed check per scenario, written against the "Required acceptance" column of the feedback. Each check runs in a transaction that is rolled back (the two races commit on the disposable database). `LIMITED` means the check ran and passed, but part of the criterion needs data the project does not have.

| ID | Scenario | Required acceptance | Status | Evidence |
|---|---|---|---|---|
| CORE-1 | Core | Repeated submission leaves one logical record; bad rows quarantined with a reason | PASS | 4 records -> 1 loaded, 2 quarantined (OUT_OF_BOUNDS, COORDINATE_SWAPPED), 1 duplicate; re-delivering the batch -> 0 loaded, 4 duplicates; 1 stored row |
| CORE-2 | Core | P0: failure before audit leaves no partial rows | PASS | audit step forced to fail -> 0 observation rows, 0 audit rows, staging REJECTED, payload quarantined with the error; batch still completes |
| CORE-3 | Core | Audit ledger is append-only with three timestamps | PASS | UPDATE / DELETE / TRUNCATE rejected (SQLSTATE 23001, 23001, 23001); 1 entries carry observed/received/recorded, none recorded before received |
| DB1-1 | Ordered Route Sequence | Expected ordering per direction; duplicate sequence rejected | PASS | 6 patterns ordered 1..n without gaps (8/0:11, 8/1:9, SD-A/0:11, SD-A/1:6, AV-1/0:10, AV-1/1:5); duplicate sequence rejected (23505); same stop twice in one direction rejected (23505) |
| DB1-2 | Trip Execution & Stop Arrivals | Dwell, boardings and generated lateness on known events | PASS | 11 events of trip T20261002-8-0-0800; known event set to +120 s / 30 s dwell / 7 boardings -> lateness 120, dwell 30 computed by the database; writing lateness directly rejected (428C9) |
| DB1-3 | Historical Position Replay | Bind fixed T; no future rows; stable ties | PASS | T bound as a parameter; 16 vehicles, none observed after T; a later packet leaves the answer for T unchanged; same (vehicle, instant) stored once (UNIQUE), so no ties to break |
| DB1-4 | Peak-Hour Lateness & Gap Analysis | Partition by route, direction and stop across peak trips | PASS | LAG over PARTITION BY route, direction, stop: 181 headways across 19 trips and 20 stop/direction pairs in 07:00-09:30; worst row recomputed independently (1382 s); statuses seen ['BUNCHED', 'GAP', 'OK'] |
| DB1-5 | Smart Mobility Telemetry Ingestion | One stored event after retries; 399 / 401 m results | PASS | same packet sent 3 times -> 1 stored row; radius 400 m around stop ICB365000721 (next stop 2734 m away): point at 399 m returns it (distance 399.0 m), point at 401 m does not |
| DB2-1 | Building & Address Reconciliation | Canonical match; registry match evidence retained | PASS | 5 linked buildings: linked place = nearest canonical place (metre distance) for all; the unlinked test-bed is reported UNLINKED; registry address kept next to the place address; statuses {'REVIEW': 2, 'CONFIRMED': 3, 'UNLINKED': 1} (REVIEW = address text differs, shown not hidden) |
| DB2-2 | Space Hierarchy & Device Registration | Orphan rejection; correct active floor/space | PASS | orphan parent rejected (23503); parent in another building rejected (23503); G-Tower tree shows 13 installed devices = 13 open assignments |
| DB2-3 | Device Relocation History | Overlap rejected (23P01); adjacent intervals accepted | PASS | 3 consecutive assignments, each ends where the next starts; overlap rejected (23P01); adjacent interval accepted; inverted interval rejected (23514); concurrent overlap: see DB2-3-RACE |
| DB2-3-RACE | Device Relocation History | Concurrent overlapping inserts cannot both commit | PASS | two connections insert overlapping intervals at the same time: the second waited 0.50 s for the first to commit, then failed with ExclusionViolation (23P01); 1 row stored |
| DB2-4 | Missing Heartbeat & Fault Detection | Stale, missing and exactly-15-minute cases | PASS | fixture: 4 silent devices at 10:00; seeded cases: never reported -> NEVER_REPORTED, last reading exactly 15:00 old -> silent (window is (T-15 min, T]), 14:59 old -> not silent |
| DB2-5 | VIP Facility State Retrieval | Maintenance returned; device with known history | PASS | G-Tower: 13 devices with latest reading, unit, age and maintenance; DEV-GT-TMP-06 has an open action and a 80 min old reading; relocated DEV-PT-TMP-03 reported in its current space '1001호' |
| DB3-1 | Attractions & Accessibility Metadata | Metadata and registry lineage for valid venues | PASS | 9 venues with wheelchair/parking flags and nearest stop; wheelchair filter -> 7; registry lineage: SRC_TOU_SYN (SYNTHETIC_FIXTURE, 9) |
| DB3-2 | Event Management & Schedule Integrity | Inverted dates rejected; open/close boundaries | PASS | inverted dates rejected (23514); Friday hours 09:00-18:00: event exactly inside -> OK, ending 1 minute after closing -> OUTSIDE_OPERATING_HOURS |
| DB3-3 | Official Statistics vs. Synthetic Samples | Reconcile totals to archived official payload | LIMITED | 9 venues: monthly totals and hourly samples kept in separate tables and shown side by side (coverage 28.7-39.9 %); every total is labelled SYNTHETIC_FIXTURE. No official tourism payload is archived, so reconciliation to an official hash is not possible |
| DB3-4 | Open Attraction Search Near Transit | Bound weekday, time, radius; closed venues excluded | PASS | weekday, time and radius are bound parameters; Friday 14:00 within 1500 m -> 7 venues; Convensia (09:00-18:00, 342 m away) present at 17:59, absent at 18:00 and 20:00, absent at 300 m |
| DB3-5 | Temporal Demand & Crowd Forecasting | Scheduled demand vs actual capacity, assumptions stated | PASS | scheduled demand vs seats of trips arriving within 500 m in the 120 min before start (assumption: each trip brings its full vehicle capacity). EVT_MOONLIGHT_FEST: 12000 vs 0 seats -> NO_SERVICE; EVT_OUTLET_SALE: 5000 vs 540 seats -> OVER_CAPACITY; EVT_SMARTCITY_EXPO: 3500 vs 1968 seats -> OVER_CAPACITY; EVT_BIO_FORUM: 800 vs 1968 seats -> OK |
| DB4-1 | Station Registration & Unit Standards | Six units; threshold provenance cited | LIMITED | 6 pollutant types with units (CO ppm, NO2 ppm, O3 ppm, PM10 µg/m³, PM25 µg/m³, SO2 ppm); 12 active thresholds equal to the AirKorea 나쁨/매우나쁨 grade boundaries (cited in sql/05). The source document is cited by name only; no archived copy is in the repo |
| DB4-2 | Telemetry Ingestion & Quality Flags | Six pollutant types ingested; malformed values quarantined | PASS | 6 pollutant types for one hour -> 6 loaded and read back as GOOD; malformed values ('-', 'abc', empty) -> 3 quarantined (FAULT_READING) |
| DB4-3 | Fault Handling & Late Arrivals | Late, duplicate and fault-marker replay with exact results | PASS | replay: first -> loaded, identical re-send -> duplicate, revised value -> replaces row (0.005) and is logged as UPDATE, '-' -> quarantined; DB4-3 reports readings 1, late 1 (received >60 min after the hour), faults 1, corrections 1 |
| DB4-4 | Hourly Rollup & Threshold Alerts | PM2.5 at and above 75 | PASS | hourly PM2.5 75 -> BAD only (VERY_BAD needs > 75), 76 -> BAD + VERY_BAD, clean hours resolve the episodes; alerts created: [('10:00', 'BAD'), ('12:00', 'BAD'), ('12:00', 'VERY_BAD')] |
| DB4-5 | Contextual Air Exposure Service | Latest good readings with distance and staleness | PASS | nearest station 송도 at 745.5 m (checked by full scan); 6 latest GOOD readings with age; at 18:10 the ages grow to 0 min |
| DB5-1 | Public Safety Facilities Registration | Source payload and coordinate bounds | LIMITED | 5 facilities, all inside the Songdo bounding box, each with nearest stop and workload; location provenance shown as ['CURATED_MANUAL']: coordinates are placeholders, not yet checked against an official facility payload |
| DB5-2 | Incident Creation & Severity Triage | Create incident; invalid severity rejected | PASS | valid report -> REPORTED with 1 history row; severity 'URGENT' -> quarantined (SCHEMA_VIOLATION); API returns 422 for it (see api_contract.csv); triage lists 6 incidents most urgent first with SLA {'CRITICAL': 300, 'HIGH': 420, 'MEDIUM': 600, 'LOW': 900} |
| DB5-3 | State Machine Transitions & Drills | Illegal UPDATE rejected; atomic history | PASS | REPORTED -> RESOLVED rejected (23514); plain UPDATE bypassing the function rejected by trigger (23514); deleting history rejected (23001); accepted DISPATCHED -> status and history row written together (2 rows); concurrent transitions: see DB5-3-RACE |
| DB5-3-RACE | State Machine Transitions & Drills | Row lock: concurrent transitions cannot both win | PASS | two dispatches of one REPORTED incident at the same time: the second waited 0.50 s on the row lock (SELECT ... FOR UPDATE), then saw DISPATCHED and failed with CheckViolation (23514); history has exactly one DISPATCHED row |
| DB5-4 | Response Time Analytics & Hotspots | Durations asserted; reproducible cluster membership | PASS | 송도2지구대: 1201 incidents, average response 260 s = independent recomputation 260 s; DBSCAN hotspots identical on two runs (5 clusters, largest 1440 incidents) |
| DB5-5 | Smart Mobility Hazard Avoidance | Active restriction geometry published; resolved zones expire | PASS | 18:30: fire zone published as a closed GeoJSON Polygon (33 points, ring at 300 m for a 300 m restriction, 1 stops inside); after RESOLVED at 19:00 it is gone at 19:30 but still shown when replaying 18:30 |
| X-VIEWS | Cross-domain federation | Three views with live data, distance caps and nearest-station rule | PASS | rows [142, 132, 107]; transit x tourism max distance 498.1 m (cap 500); incident x stops max 599.9 m (cap 600); building station = true nearest for every device; alerts only from the incident's nearest station (0 exceptions) |
| X-MV | Cross-domain federation | Materialized views equal live queries; refresh logged | PASS | after a logged refresh the facility statistics MV equals the live aggregate for all 5 facilities; refresh logged for 3 views (mv_facility_response_stats 40, mv_stop_hourly_service 612, mv_place_context 15) |

Totals: 29 PASS, 3 LIMITED, 0 FAIL (25 of 25 scenarios have a dedicated check; full table in `acceptance_matrix.csv`).

## Clean build

`CREATE DATABASE songdo_evidence`, then `psql -X -q -v ON_ERROR_STOP=1 -f` for each file, in order.

| File | Exit | ms | Size |
|---|---|---|---|
| sql/00_init_database.sql | 0 | 599 | 3 KB |
| sql/01_core_schema.sql | 0 | 114 | 11 KB |
| sql/02_db1_mobility.sql | 0 | 121 | 9 KB |
| sql/03_db2_building.sql | 0 | 131 | 10 KB |
| sql/04_db3_tourism.sql | 0 | 103 | 7 KB |
| sql/05_db4_environment.sql | 0 | 106 | 12 KB |
| sql/06_db5_safety.sql | 0 | 110 | 14 KB |
| sql/07_cross_domain_views.sql | 0 | 110 | 13 KB |
| fixtures/seed_data.sql | 0 | 6141 | 8602 KB |

Objects: 39 tables, 4 views, 3 materialized views, 109 indexes, 20 gist indexes, 39 primary keys, 58 foreign keys, 23 unique constraints, 87 check constraints, 3 exclusion constraints, 5 triggers, 12 functions, 4 generated columns.
Rows: 111,543 in 39 tables (per table in `schema_inventory.csv`).

## Backup and restore

`pg_dump -Fc` (2.5 MB, 0.7 s) then `pg_restore --exit-on-error` into `songdo_evidence_restore` (2.8 s). **63 of 63 comparisons identical**: every object count, every table's row count, the result hash of 8 tasks, and behaviour (ledger still append-only, exclusion constraint still enforced, materialized views refresh).

## Tests

`72` tests on the clean database: OK (5.223 s). Full verbose output in `test_results.txt`.

## Performance

### Index versus no index at scale (1,000,000 GPS rows)

A synthetic copy of the GPS table with the same columns and index definitions, filled with 1,000,000 rows (500 vehicles x 2,000 fixes, fixed random seed; built in 11.9 s, then dropped). Median of 5 runs with indexes, 3 runs with index scans disabled.

| Query | Index used | ms with index | ms without | Speed-up |
|---|---|---|---|---|
| Radius search: GPS fixes within 150 m of a point | vehicle_observations_geom_idx | 29.42 | 733.1 | 25x |
| Point-in-time replay: last fix of 50 vehicles at T | vehicle_observations_vehicle_id_observed_at_idx | 1.3 | 3847.7 | 2961x |
| Time window: fixes in a 5-minute interval | vehicle_observations_observed_at_idx | 1.22 | 58.1 | 48x |

### Index versus no index on the fixture data

The same comparison on the project's own tables (513 stops, about 20,000 GPS rows, about 50,000 status history rows). The gains are smaller than at scale because small tables fit in a few memory pages; the table above shows how the gap grows with volume. Median of 15 runs; full plans in `explain_plans.md`.

| Query | Table | Planner choice | Index | ms (planner) | ms (no index) | Speed-up |
|---|---|---|---|---|---|---|
| Stops within 1000 m (starter notebook, module 19) | mobility.stops, 513 rows | index | idx_stops_geog | 0.419 | 1.215 | 2.9x |
| GPS points within 150 m of a point | mobility.vehicle_observations | index | idx_obs_geog | 4.61 | 36.199 | 7.9x |
| Each vehicle's last position at T (point-in-time replay) | mobility.vehicle_observations | index | idx_obs_time vehicles_pkey | 0.992 | 46.771 | 47.2x |
| Audit history of one entity | core.event_audit_log | index | idx_audit_entity | 0.286 | 0.168 | 0.6x |
| Status history of one incident | safety.incident_status_history | index | idx_history_incident_time | 0.181 | 4.97 | 27.4x |

### Every task

Median of 7 runs after a warm-up, default parameters.

| Task | Rows | Median ms | Execution ms | Indexes used | Seq scans |
|---|---|---|---|---|---|
| CORE-1 | 5 | 0.41 | 0.076 | idx_places_geog | — |
| CORE-2 | 15 | 0.27 | 0.033 | — | source_registry |
| CORE-3 | 0 | 0.21 | 0.014 | — | quarantine_records |
| CORE-4 | 5 | 0.28 | 0.032 | — | event_audit_log |
| DB1-1 | 11 | 1.21 | 0.214 | — | route_stops routes stops |
| DB1-2 | 11 | 0.67 | 0.154 | uq_trip_stop | stops |
| DB1-3 | 16 | 9.15 | 8.226 | idx_obs_time | vehicles |
| DB1-4 | 50 | 3.0 | 1.581 | — | routes stops trip_stop_events trips |
| DB1-5 | 15 | 1.32 | 0.816 | idx_obs_time idx_stops_geog | — |
| DB2-1 | 6 | 0.8 | 0.309 | buildings_pkey | places |
| DB2-2 | 14 | 1.45 | 0.278 | — | device_space_assignments devices spaces |
| DB2-3 | 3 | 0.58 | 0.086 | — | device_space_assignments spaces |
| DB2-4 | 4 | 1.68 | 0.516 | ex_assignment_no_overlap idx_telemetry_device_time idx_telemetry_time spaces_pkey | devices |
| DB2-5 | 13 | 1.43 | 0.373 | idx_telemetry_device_time | device_space_assignments devices maintenance_actions metric_types spaces |
| DB3-1 | 9 | 12.13 | 11.99 | — | attractions stops |
| DB3-2 | 7 | 0.92 | 0.162 | — | attractions events operating_hours |
| DB3-3 | 9 | 2.3 | 1.515 | sample_visits_pkey | aggregate_visits attractions source_registry |
| DB3-4 | 7 | 0.81 | 0.189 | ex_hours_no_overlap idx_attractions_geog stops_stop_number_key | — |
| DB3-5 | 4 | 7.88 | 5.881 | idx_events_stop_arrival idx_stops_geog trips_pkey vehicles_pkey | attractions events |
| DB4-1 | 6 | 1.05 | 0.09 | — | alert_rules measurement_types source_registry stations |
| DB4-2 | 6 | 3.06 | 1.779 | uq_measurement | alert_rules measurement_types stations |
| DB4-3 | 7 | 9.68 | 6.784 | idx_audit_entity | measurement_types measurements quarantine_records stations |
| DB4-4 | 24 | 1.85 | 0.292 | hourly_summaries_pkey | alert_rules alerts measurement_types stations |
| DB4-5 | 6 | 3.16 | 1.64 | idx_env_stations_geog uq_measurement | alert_rules measurement_types |
| DB5-1 | 5 | 15.09 | 14.511 | idx_drills_facility | facilities incidents source_registry stops |
| DB5-2 | 6 | 1.02 | 0.131 | idx_incidents_reported | facilities incident_types severity_levels |
| DB5-3 | 5 | 0.68 | 0.101 | idx_history_incident_time | facilities |
| DB5-4 | 5 | 26.67 | 28.188 | — | facilities incident_status_history incidents severity_levels |
| DB5-4B | 5 | 51.87 | 51.642 | — | incidents places |
| DB5-5 | 2 | 5.93 | 5.582 | idx_history_incident_time idx_incidents_reported idx_obs_time idx_stops_geog | incident_types |
| X-1 | 2 | 0.8 | 0.145 | idx_attractions_geog stops_stop_number_key | events |
| X-2 | 26 | 10.77 | 9.607 | uq_measurement | buildings device_space_assignments devices spaces stations |
| X-3 | 50 | 3.39 | 2.045 | idx_incidents_active idx_stops_geog | alerts incident_types stations |
| X-4 | 15 | 0.35 | 0.077 | — | mv_place_context |
| X-5 | 5 | 0.49 | 0.073 | — | mv_facility_response_stats |
| X-6 | 3 | 0.29 | 0.032 | — | refresh_log |

## API

Real HTTP server (uvicorn) on the clean database. 37 of 37 contract cases pass.

| Case | Request | Expected | Actual | Database check | Status |
|---|---|---|---|---|---|
| valid observation | POST /api/v1/mobility/observation | 202 LOADED | 202 LOADED | 1 stored row(s) | PASS |
| retry 1 of the same packet | POST /api/v1/mobility/observation | 202 DUPLICATE | 202 DUPLICATE | 1 stored row(s) | PASS |
| retry 2 of the same packet | POST /api/v1/mobility/observation | 202 DUPLICATE | 202 DUPLICATE | 1 stored row(s) | PASS |
| retry 3 of the same packet | POST /api/v1/mobility/observation | 202 DUPLICATE | 202 DUPLICATE | 1 stored row(s) | PASS |
| no token | POST /api/v1/mobility/observation | 401 | 401 |  | PASS |
| wrong token | POST /api/v1/mobility/observation | 401 | 401 |  | PASS |
| speed 400 km/h | POST /api/v1/mobility/observation | 422 | 422 | 0 stored row(s) | PASS |
| swapped coordinates (lat 126.64) | POST /api/v1/mobility/observation | 422 | 422 | 0 stored row(s) | PASS |
| time without offset | POST /api/v1/mobility/observation | 422 | 422 |  | PASS |
| malformed time | POST /api/v1/mobility/observation | 422 | 422 |  | PASS |
| missing field (speed) | POST /api/v1/mobility/observation | 422 | 422 |  | PASS |
| GPS fix outside Songdo | POST /api/v1/mobility/observation | 202 QUARANTINED | 202 QUARANTINED | 1 OUT_OF_BOUNDS quarantine row(s) | PASS |
| unknown vehicle | POST /api/v1/mobility/observation | 202 QUARANTINED | 202 QUARANTINED | 1 FK_VIOLATION quarantine row(s) | PASS |
| invalid severity | POST /api/v1/safety/incidents | 422 | 422 |  | PASS |
| report incident | POST /api/v1/safety/incidents | 202 LOADED | 202 LOADED |  | PASS |
| REPORTED -> RESOLVED (skips states) | POST /api/v1/safety/incidents/INC-EVD-API-1/transition | 409 | 409 |  | PASS |
| REPORTED -> DISPATCHED | POST /api/v1/safety/incidents/INC-EVD-API-1/transition | 200 | 200 | 2 history rows | PASS |
| CLOSED incident -> DISPATCHED | POST /api/v1/safety/incidents/INC-20261002-0001/transition | 409 | 409 |  | PASS |
| add a place to edit | POST /api/v1/core/places | 202 | 202 |  | PASS |
| PATCH place without token | PATCH /api/v1/core/places/PLC_EVD_EDIT | 401 | 401 |  | PASS |
| PATCH place: rename | PATCH /api/v1/core/places/PLC_EVD_EDIT | 200 UPDATED | 200 UPDATED | 1 UPDATE audit entry | PASS |
| PATCH place: same value again | PATCH /api/v1/core/places/PLC_EVD_EDIT | 200 UNCHANGED | 200 UNCHANGED | still 1 audit entry | PASS |
| PATCH place: move outside Songdo | PATCH /api/v1/core/places/PLC_EVD_EDIT | 422 | 422 |  | PASS |
| PATCH place: unknown field | PATCH /api/v1/core/places/PLC_EVD_EDIT | 422 | 422 |  | PASS |
| PATCH unknown place | PATCH /api/v1/core/places/PLC_NOPE | 404 | 404 |  | PASS |
| DELETE place still in use | DELETE /api/v1/core/places/PLC_CONVENSIA | 409 | 409 | 1 row kept | PASS |
| DELETE unreferenced place | DELETE /api/v1/core/places/PLC_EVD_EDIT | 200 DELETED | 200 DELETED | 0 rows left, 1 DELETE audit entry with the full row | PASS |
| PATCH attraction: parking spaces without parking | PATCH /api/v1/tourism/attractions/ATT_HANOK_VILLAGE | 409 | 409 |  | PASS |
| DELETE event (cancel, row kept) | DELETE /api/v1/tourism/events/EVT_SKYLINE_PHOTO | 200 CANCELLED | 200 CANCELLED | 1 row kept, is_cancelled | PASS |
| DELETE device (retire) | DELETE /api/v1/building/devices/DEV-CV-PWR-01 | 200 RETIRED | 200 RETIRED | 0 open locations, 1 history rows kept | PASS |
| PATCH retired device | PATCH /api/v1/building/devices/DEV-CV-PWR-01 | 409 | 409 |  | PASS |
| unknown task | POST /api/v1/scenarios/DB9-9/run | 404 | 404 |  | PASS |
| task with bad parameter | POST /api/v1/scenarios/DB1-3/run | 422 | 422 |  | PASS |
| query out of range | GET /api/v1/core/places/nearby?radius_m=-5 | 422 | 422 |  | PASS |
| stops GeoJSON | GET /api/v1/mobility/stops.geojson | 200 | 200 | FeatureCollection, 513 Point features, all inside the bbox: True | PASS |
| OpenAPI document | GET /openapi.json | 200 | 200 | 53 paths documented | PASS |
| 12 identical packets sent concurrently | POST /api/v1/mobility/observation x12 (barrier) | 1 stored row | {"DUPLICATE": 11, "LOADED": 1} | 1 stored row(s) | PASS |

Latency over 30 requests per endpoint (after one warm-up), same machine:

| Endpoint | p50 ms | p95 ms | max ms |
|---|---|---|---|
| GET /health | 2.2 | 3.2 | 5.4 |
| GET /api/v1/mobility/stops/nearby | 2.9 | 3.8 | 26.5 |
| GET /api/v1/mobility/vehicles/positions | 12.9 | 13.7 | 13.9 |
| GET /api/v1/building/buildings/BLD_G_TOWER/state | 5.4 | 7.3 | 9.2 |
| GET /api/v1/tourism/attractions | 2.5 | 3.2 | 3.4 |
| GET /api/v1/environment/exposure | 5.9 | 6.9 | 7.3 |
| GET /api/v1/safety/incidents/active | 8.2 | 8.8 | 9.3 |
| GET /api/v1/cross/incident-impact | 8.1 | 9.4 | 9.5 |
| GET /api/v1/system/map/frame | 13.8 | 15.4 | 16.6 |
| POST /api/v1/scenarios/DB1-3/run | 13.0 | 15.2 | 15.9 |
| POST /api/v1/scenarios/X-5/run | 3.5 | 4.6 | 6.0 |
| POST /api/v1/mobility/observation | 5.9 | 7.3 | 11.5 |

## Web console

Each task was opened in headless chrome through its link (`#/tasks?task=<id>&run=1`). The row count was read from the rendered page and compared with the API: **36 of 36 tasks match**. Screenshots in `screenshots/`.

| Task | Rows in browser | Rows from API | Status | Screenshot |
|---|---|---|---|---|
| CORE-1 | 5 | 5 | PASS | [png](screenshots/task_CORE-1.png) |
| CORE-2 | 15 | 15 | PASS | [png](screenshots/task_CORE-2.png) |
| CORE-3 | 2 | 2 | PASS | [png](screenshots/task_CORE-3.png) |
| CORE-4 | 45 | 45 | PASS | [png](screenshots/task_CORE-4.png) |
| DB1-1 | 11 | 11 | PASS | [png](screenshots/task_DB1-1.png) |
| DB1-2 | 11 | 11 | PASS | [png](screenshots/task_DB1-2.png) |
| DB1-3 | 16 | 16 | PASS | [png](screenshots/task_DB1-3.png) |
| DB1-4 | 50 | 50 | PASS | [png](screenshots/task_DB1-4.png) |
| DB1-5 | 15 | 15 | PASS | [png](screenshots/task_DB1-5.png) |
| DB2-1 | 6 | 6 | PASS | [png](screenshots/task_DB2-1.png) |
| DB2-2 | 14 | 14 | PASS | [png](screenshots/task_DB2-2.png) |
| DB2-3 | 3 | 3 | PASS | [png](screenshots/task_DB2-3.png) |
| DB2-4 | 4 | 4 | PASS | [png](screenshots/task_DB2-4.png) |
| DB2-5 | 13 | 13 | PASS | [png](screenshots/task_DB2-5.png) |
| DB3-1 | 9 | 9 | PASS | [png](screenshots/task_DB3-1.png) |
| DB3-2 | 7 | 7 | PASS | [png](screenshots/task_DB3-2.png) |
| DB3-3 | 9 | 9 | PASS | [png](screenshots/task_DB3-3.png) |
| DB3-4 | 7 | 7 | PASS | [png](screenshots/task_DB3-4.png) |
| DB3-5 | 4 | 4 | PASS | [png](screenshots/task_DB3-5.png) |
| DB4-1 | 6 | 6 | PASS | [png](screenshots/task_DB4-1.png) |
| DB4-2 | 6 | 6 | PASS | [png](screenshots/task_DB4-2.png) |
| DB4-3 | 7 | 7 | PASS | [png](screenshots/task_DB4-3.png) |
| DB4-4 | 24 | 24 | PASS | [png](screenshots/task_DB4-4.png) |
| DB4-5 | 6 | 6 | PASS | [png](screenshots/task_DB4-5.png) |
| DB5-1 | 5 | 5 | PASS | [png](screenshots/task_DB5-1.png) |
| DB5-2 | 6 | 6 | PASS | [png](screenshots/task_DB5-2.png) |
| DB5-3 | 5 | 5 | PASS | [png](screenshots/task_DB5-3.png) |
| DB5-4 | 5 | 5 | PASS | [png](screenshots/task_DB5-4.png) |
| DB5-4B | 5 | 5 | PASS | [png](screenshots/task_DB5-4B.png) |
| DB5-5 | 2 | 2 | PASS | [png](screenshots/task_DB5-5.png) |
| X-1 | 2 | 2 | PASS | [png](screenshots/task_X-1.png) |
| X-2 | 26 | 26 | PASS | [png](screenshots/task_X-2.png) |
| X-3 | 50 | 50 | PASS | [png](screenshots/task_X-3.png) |
| X-4 | 15 | 15 | PASS | [png](screenshots/task_X-4.png) |
| X-5 | 5 | 5 | PASS | [png](screenshots/task_X-5.png) |
| X-6 | 3 | 3 | PASS | [png](screenshots/task_X-6.png) |

Pages: [page_map_0810](screenshots/page_map_0810.png), [page_map_1820](screenshots/page_map_1820.png), [page_ingest](screenshots/page_ingest.png), [page_verify](screenshots/page_verify.png), [page_task_result_map](screenshots/page_task_result_map.png)

## Known limitations (stated, not hidden)

- Real data: bus stops (MOLIT file) and AirKorea stations and readings. Routes, vehicles, trips, building interiors, tourism hours, events and visitor numbers, and incidents are generated by `fixtures/seed_fixtures.py` (seed 20261002) and labelled with SYNTHETIC_FIXTURE sources.
- DB3-3: 9 venues: monthly totals and hourly samples kept in separate tables and shown side by side (coverage 28.7-39.9 %); every total is labelled SYNTHETIC_FIXTURE. No official tourism payload is archived, so reconciliation to an official hash is not possible
- DB4-1: 6 pollutant types with units (CO ppm, NO2 ppm, O3 ppm, PM10 µg/m³, PM25 µg/m³, SO2 ppm); 12 active thresholds equal to the AirKorea 나쁨/매우나쁨 grade boundaries (cited in sql/05). The source document is cited by name only; no archived copy is in the repo
- DB5-1: 5 facilities, all inside the Songdo bounding box, each with nearest stop and workload; location provenance shown as ['CURATED_MANUAL']: coordinates are placeholders, not yet checked against an official facility payload
- Timings come from one laptop and a small data set (about 20,000 GPS rows, 10,000 incidents); they show the plan shape and relative cost, not production capacity.

## Files

| File | Content |
|---|---|
| `verification_summary.csv` | gate checklist (starter notebook format) |
| `environment.txt` | versions of every component and the git commit |
| `clean_build.log` | psql output of the clean build, object counts, rows per table |
| `schema_inventory.csv` | object counts and rows per table |
| `acceptance_matrix.csv` | scenario acceptance checks |
| `test_results.txt` | unittest verbose output |
| `backup_restore.log` | dump and restore commands |
| `backup_restore.csv` | original vs restored comparison |
| `performance.csv` | timing and plan summary of every task |
| `index_comparison.csv` | index vs no-index timings on the fixture tables |
| `scale_benchmark.csv` | index vs no-index timings on 1,000,000 synthetic GPS rows |
| `explain_plans.md` | full EXPLAIN ANALYZE plans |
| `api_contract.csv` | API negative and positive cases |
| `api_latency.csv` | endpoint latency |
| `ui_task_coverage.csv` | browser-to-API run of every task |
| `screenshots/` | console screenshots |
