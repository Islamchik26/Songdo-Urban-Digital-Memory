# Query plans of every task

EXPLAIN (ANALYZE, BUFFERS) on `songdo_evidence`, default parameters, after one warm-up run.

## CORE-1 · Places near a point

Parameters: `{"lon": 126.6548, "lat": 37.3888, "radius_m": 1000.0}`

```
Sort  (cost=95.90..95.91 rows=1 width=91) (actual time=0.107..0.108 rows=5.00 loops=1)
  Sort Key: ((round((st_distance((geom)::geography, '0101000020E6100000EE5A423EE8A95F40A857CA32C4B14240'::geography, true))::numeric, 1))::double precision)
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=2
  ->  Index Scan using idx_places_geog on places  (cost=0.26..95.89 rows=1 width=91) (actual time=0.032..0.061 rows=5.00 loops=1)
        Index Cond: ((geom)::geography && _st_expand('0101000020E6100000EE5A423EE8A95F40A857CA32C4B14240'::geography, '1000'::double precision))
        Filter: st_dwithin((geom)::geography, '0101000020E6100000EE5A423EE8A95F40A857CA32C4B14240'::geography, '1000'::double precision, true)
        Rows Removed by Filter: 2
        Index Searches: 1
        Buffers: shared hit=2
Planning Time: 0.124 ms
Execution Time: 0.123 ms
```

## CORE-2 · Source registry

Parameters: `{}`

```
Sort  (cost=1.44..1.48 rows=15 width=164) (actual time=0.033..0.034 rows=15.00 loops=1)
  Sort Key: source_id
  Sort Method: quicksort  Memory: 27kB
  Buffers: shared hit=1
  ->  Seq Scan on source_registry  (cost=0.00..1.15 rows=15 width=164) (actual time=0.006..0.009 rows=15.00 loops=1)
        Buffers: shared hit=1
Planning Time: 0.034 ms
Execution Time: 0.044 ms
```

## CORE-3 · Quarantined records

Parameters: `{"limit": 50}`

```
Limit  (cost=0.01..0.02 rows=1 width=472) (actual time=0.006..0.007 rows=0.00 loops=1)
  ->  Sort  (cost=0.01..0.02 rows=1 width=472) (actual time=0.006..0.006 rows=0.00 loops=1)
        Sort Key: recorded_at DESC
        Sort Method: quicksort  Memory: 25kB
        ->  Seq Scan on quarantine_records  (cost=0.00..0.00 rows=1 width=472) (actual time=0.004..0.004 rows=0.00 loops=1)
Planning Time: 0.042 ms
Execution Time: 0.017 ms
```

## CORE-4 · Audit ledger (latest events)

Parameters: `{"limit": 50}`

```
Limit  (cost=23.11..23.12 rows=5 width=93) (actual time=0.046..0.048 rows=5.00 loops=1)
  Buffers: shared hit=23
  ->  Sort  (cost=23.11..23.12 rows=5 width=93) (actual time=0.045..0.046 rows=5.00 loops=1)
        Sort Key: audit_id DESC
        Sort Method: quicksort  Memory: 25kB
        Buffers: shared hit=23
        ->  Seq Scan on event_audit_log  (cost=0.00..23.05 rows=5 width=93) (actual time=0.033..0.036 rows=5.00 loops=1)
              Buffers: shared hit=23
Planning Time: 0.112 ms
Execution Time: 0.067 ms
```

## DB1-1 · Ordered route sequence

Parameters: `{"route_number": "8", "direction": 0}`

```
WindowAgg  (cost=20.42..158.28 rows=11 width=67) (actual time=0.140..0.204 rows=11.00 loops=1)
  Window: w AS (ORDER BY rs.sequence_order)
  Storage: Memory  Maximum Storage: 17kB
  Buffers: shared hit=12
  ->  Sort  (cost=20.40..20.43 rows=11 width=79) (actual time=0.129..0.130 rows=11.00 loops=1)
        Sort Key: rs.sequence_order
        Sort Method: quicksort  Memory: 26kB
        Buffers: shared hit=12
        ->  Hash Join  (cost=3.04..20.21 rows=11 width=79) (actual time=0.064..0.123 rows=11.00 loops=1)
              Hash Cond: ((s.stop_id)::text = (rs.stop_id)::text)
              Buffers: shared hit=12
              ->  Seq Scan on stops s  (cost=0.00..15.13 rows=513 width=75) (actual time=0.010..0.045 rows=513.00 loops=1)
                    Buffers: shared hit=10
              ->  Hash  (cost=2.91..2.91 rows=11 width=17) (actual time=0.029..0.029 rows=11.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=2
                    ->  Hash Join  (cost=1.05..2.91 rows=11 width=17) (actual time=0.016..0.025 rows=11.00 loops=1)
                          Hash Cond: ((rs.route_id)::text = (r.route_id)::text)
                          Buffers: shared hit=2
                          ->  Seq Scan on route_stops rs  (cost=0.00..1.65 rows=32 width=23) (actual time=0.005..0.009 rows=32.00 loops=1)
                                Filter: (direction = '0'::smallint)
                                Rows Removed by Filter: 20
                                Buffers: shared hit=1
                          ->  Hash  (cost=1.04..1.04 rows=1 width=6) (actual time=0.005..0.005 rows=1.00 loops=1)
                                Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                Buffers: shared hit=1
                                ->  Seq Scan on routes r  (cost=0.00..1.04 rows=1 width=6) (actual time=0.003..0.004 rows=1.00 loops=1)
                                      Filter: ((route_number)::text = '8'::text)
                                      Rows Removed by Filter: 2
                                      Buffers: shared hit=1
Planning:
  Buffers: shared hit=7
Planning Time: 0.368 ms
Execution Time: 0.237 ms
```

## DB1-2 · Trip execution & stop arrivals

Parameters: `{"trip_id": "T20261002-8-0-0800"}`

```
WindowAgg  (cost=48.33..48.51 rows=11 width=68) (actual time=0.128..0.136 rows=11.00 loops=1)
  Window: w1 AS (ORDER BY e.sequence_order)
  Storage: Memory  Maximum Storage: 17kB
  Buffers: shared hit=14
  ->  Sort  (cost=48.29..48.32 rows=11 width=60) (actual time=0.123..0.125 rows=11.00 loops=1)
        Sort Key: e.sequence_order
        Sort Method: quicksort  Memory: 25kB
        Buffers: shared hit=14
        ->  Hash Join  (cost=31.61..48.10 rows=11 width=60) (actual time=0.058..0.118 rows=11.00 loops=1)
              Hash Cond: ((s.stop_id)::text = (e.stop_id)::text)
              Buffers: shared hit=14
              ->  Seq Scan on stops s  (cost=0.00..15.13 rows=513 width=37) (actual time=0.009..0.042 rows=513.00 loops=1)
                    Buffers: shared hit=10
              ->  Hash  (cost=31.47..31.47 rows=11 width=49) (actual time=0.023..0.024 rows=11.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=4
                    ->  Bitmap Heap Scan on trip_stop_events e  (cost=4.37..31.47 rows=11 width=49) (actual time=0.017..0.019 rows=11.00 loops=1)
                          Recheck Cond: ((trip_id)::text = 'T20261002-8-0-0800'::text)
                          Heap Blocks: exact=2
                          Buffers: shared hit=4
                          ->  Bitmap Index Scan on uq_trip_stop  (cost=0.00..4.36 rows=11 width=0) (actual time=0.010..0.010 rows=12.00 loops=1)
                                Index Cond: ((trip_id)::text = 'T20261002-8-0-0800'::text)
                                Index Searches: 1
                                Buffers: shared hit=2
Planning:
  Buffers: shared hit=3
Planning Time: 0.275 ms
Execution Time: 0.161 ms
```

## DB1-3 · Historical position replay

Parameters: `{"as_of": "2026-10-02 08:20:00+09:00"}`

```
Unique  (cost=324.06..335.68 rows=16 width=86) (actual time=10.027..10.354 rows=16.00 loops=1)
  Buffers: shared hit=2239
  ->  Sort  (cost=324.06..329.87 rows=2324 width=86) (actual time=10.024..10.120 rows=2335.00 loops=1)
        Sort Key: o.vehicle_id, o.observed_at DESC
        Sort Method: quicksort  Memory: 342kB
        Buffers: shared hit=2239
        ->  Hash Join  (cost=1.65..194.12 rows=2324 width=86) (actual time=0.077..5.453 rows=2335.00 loops=1)
              Hash Cond: ((o.vehicle_id)::text = (v.vehicle_id)::text)
              Buffers: shared hit=2239
              ->  Index Scan using idx_obs_time on vehicle_observations o  (cost=0.29..138.57 rows=2324 width=82) (actual time=0.018..1.284 rows=2335.00 loops=1)
                    Index Cond: (observed_at <= '2026-10-02 08:20:00+09'::timestamp with time zone)
                    Index Searches: 1
                    Buffers: shared hit=2238
              ->  Hash  (cost=1.16..1.16 rows=16 width=21) (actual time=0.036..0.037 rows=16.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on vehicles v  (cost=0.00..1.16 rows=16 width=21) (actual time=0.022..0.026 rows=16.00 loops=1)
                          Buffers: shared hit=1
Planning:
  Buffers: shared hit=4
Planning Time: 0.540 ms
Execution Time: 10.454 ms
```

## DB1-4 · Peak-hour lateness & gap analysis

Parameters: `{"route_number": "8", "from_time": "2026-10-02 07:00:00+09:00", "to_time": "2026-10-02 09:30:00+09:00", "limit": 50}`

```
Limit  (cost=182.06..182.18 rows=50 width=102) (actual time=1.608..1.616 rows=50.00 loops=1)
  Buffers: shared hit=71
  ->  Sort  (cost=182.06..182.55 rows=198 width=102) (actual time=1.607..1.612 rows=50.00 loops=1)
        Sort Key: (abs((a.headway_seconds - a.scheduled_headway_seconds))) DESC, a.observed_arrival
        Sort Method: top-N heapsort  Memory: 36kB
        Buffers: shared hit=71
        ->  Hash Join  (cost=159.56..175.48 rows=198 width=102) (actual time=1.160..1.550 rows=181.00 loops=1)
              Hash Cond: ((a.stop_id)::text = (s.stop_id)::text)
              Buffers: shared hit=71
              ->  Subquery Scan on a  (cost=138.02..148.47 rows=198 width=55) (actual time=0.992..1.255 rows=181.00 loops=1)
                    Filter: (a.headway_seconds IS NOT NULL)
                    Rows Removed by Filter: 20
                    Buffers: shared hit=61
                    ->  WindowAgg  (cost=138.02..146.48 rows=199 width=61) (actual time=0.984..1.239 rows=201.00 loops=1)
                          Window: w AS (PARTITION BY t.route_id, t.direction, e.stop_id ORDER BY e.observed_arrival)
                          Storage: Memory  Maximum Storage: 17kB
                          Buffers: shared hit=61
                          ->  Sort  (cost=138.02..138.52 rows=199 width=61) (actual time=0.972..0.983 rows=201.00 loops=1)
                                Sort Key: t.route_id, t.direction, e.stop_id, e.observed_arrival
                                Sort Method: quicksort  Memory: 40kB
                                Buffers: shared hit=61
                                ->  Hash Join  (cost=17.08..130.42 rows=199 width=61) (actual time=0.133..0.608 rows=201.00 loops=1)
                                      Hash Cond: ((e.trip_id)::text = (t.trip_id)::text)
                                      Buffers: shared hit=61
                                      ->  Seq Scan on trip_stop_events e  (cost=0.00..109.11 rows=597 width=53) (actual time=0.008..0.397 rows=595.00 loops=1)
                                            Filter: ((observed_arrival >= '2026-10-02 07:00:00+09'::timestamp with time zone) AND (observed_arrival < '2026-10-02 09:30:00+09'::timestamp with time zone))
                                            Rows Removed by Filter: 3146
                                            Buffers: shared hit=53
                                      ->  Hash  (cost=15.24..15.24 rows=147 width=29) (actual time=0.122..0.124 rows=126.00 loops=1)
                                            Buckets: 1024  Batches: 1  Memory Usage: 16kB
                                            Buffers: shared hit=8
                                            ->  Hash Join  (cost=1.05..15.24 rows=147 width=29) (actual time=0.013..0.099 rows=126.00 loops=1)
                                                  Hash Cond: ((t.route_id)::text = (r.route_id)::text)
                                                  Buffers: shared hit=8
                                                  ->  Seq Scan on trips t  (cost=0.00..11.40 rows=440 width=29) (actual time=0.005..0.030 rows=440.00 loops=1)
                                                        Buffers: shared hit=7
                                                  ->  Hash  (cost=1.04..1.04 rows=1 width=6) (actual time=0.004..0.005 rows=1.00 loops=1)
                                                        Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                                        Buffers: shared hit=1
                                                        ->  Seq Scan on routes r  (cost=0.00..1.04 rows=1 width=6) (actual time=0.003..0.004 rows=1.00 loops=1)
                                                              Filter: ((route_number)::text = '8'::text)
                                                              Rows Removed by Filter: 2
                                                              Buffers: shared hit=1
              ->  Hash  (cost=15.13..15.13 rows=513 width=37) (actual time=0.159..0.159 rows=513.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 43kB
                    Buffers: shared hit=10
                    ->  Seq Scan on stops s  (cost=0.00..15.13 rows=513 width=37) (actual time=0.011..0.075 rows=513.00 loops=1)
                          Buffers: shared hit=10
Planning:
  Buffers: shared hit=19
Planning Time: 0.855 ms
Execution Time: 1.684 ms
```

## DB1-5 · Smart Mobility telemetry: candidate stops

Parameters: `{"vehicle_id": "VEH-AV1-01", "radius_m": 400.0, "last_n": 5}`

```
Incremental Sort  (cost=68.00..171.32 rows=5 width=95) (actual time=0.901..0.903 rows=15.00 loops=1)
  Sort Key: vehicle_observations.observed_at DESC, ((round((st_distance((vehicle_observations.geom)::geography, (s.geom)::geography, true))::numeric, 1))::double precision)
  Presorted Key: vehicle_observations.observed_at
  Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 26kB  Peak Memory: 26kB
  Buffers: shared hit=155
  ->  Nested Loop  (cost=33.61..171.13 rows=5 width=95) (actual time=0.238..0.891 rows=15.00 loops=1)
        Buffers: shared hit=155
        ->  Limit  (cost=0.29..4.39 rows=5 width=63) (actual time=0.053..0.067 rows=5.00 loops=1)
              Buffers: shared hit=116
              ->  Index Scan using idx_obs_time on vehicle_observations  (cost=0.29..1148.92 rows=1400 width=63) (actual time=0.053..0.065 rows=5.00 loops=1)
                    Filter: ((vehicle_id)::text = 'VEH-AV1-01'::text)
                    Rows Removed by Filter: 117
                    Index Searches: 1
                    Buffers: shared hit=116
        ->  Limit  (cost=33.32..33.33 rows=1 width=61) (actual time=0.162..0.163 rows=3.00 loops=5)
              Buffers: shared hit=39
              ->  Sort  (cost=33.32..33.33 rows=1 width=61) (actual time=0.161..0.162 rows=3.00 loops=5)
                    Sort Key: ((round((st_distance((vehicle_observations.geom)::geography, (s.geom)::geography, true))::numeric, 1))::double precision)
                    Sort Method: top-N heapsort  Memory: 25kB
                    Buffers: shared hit=39
                    ->  Index Scan using idx_stops_geog on stops s  (cost=0.27..33.31 rows=1 width=61) (actual time=0.028..0.152 rows=19.00 loops=5)
                          Index Cond: ((geom)::geography && _st_expand((vehicle_observations.geom)::geography, '400'::double precision))
                          Filter: st_dwithin((vehicle_observations.geom)::geography, (geom)::geography, '400'::double precision, true)
                          Rows Removed by Filter: 7
                          Index Searches: 5
                          Buffers: shared hit=39
Planning Time: 0.226 ms
Execution Time: 0.934 ms
```

## DB2-1 · Building & address reconciliation

Parameters: `{"max_distance_m": 150.0}`

```
Nested Loop Left Join  (cost=188.93..1295.38 rows=6 width=151) (actual time=0.147..0.604 rows=6.00 loops=1)
  Buffers: shared hit=8
  ->  Index Scan using buildings_pkey on buildings b  (cost=0.13..12.22 rows=6 width=127) (actual time=0.012..0.015 rows=6.00 loops=1)
        Index Searches: 1
        Buffers: shared hit=2
  ->  Limit  (cost=188.80..213.83 rows=1 width=79) (actual time=0.096..0.096 rows=1.00 loops=6)
        Buffers: shared hit=6
        ->  Result  (cost=188.80..564.25 rows=15 width=79) (actual time=0.095..0.095 rows=1.00 loops=6)
              Buffers: shared hit=6
              ->  Sort  (cost=188.80..188.84 rows=15 width=103) (actual time=0.089..0.089 rows=1.00 loops=6)
                    Sort Key: (st_distance((b.geom)::geography, (pl.geom)::geography, true))
                    Sort Method: top-N heapsort  Memory: 25kB
                    Buffers: shared hit=6
                    ->  Seq Scan on places pl  (cost=0.00..188.72 rows=15 width=103) (actual time=0.010..0.077 rows=15.00 loops=6)
                          Buffers: shared hit=6
Planning Time: 0.231 ms
Execution Time: 0.635 ms
```

## DB2-2 · Space hierarchy & device registration

Parameters: `{"building_id": "BLD_G_TOWER"}`

```
GroupAggregate  (cost=91.28..106.57 rows=200 width=242) (actual time=0.178..0.194 rows=14.00 loops=1)
  Group Key: t.floor_number, t.path, t.space_id, t.space_type, t.depth
  Buffers: shared hit=6
  CTE tree
    ->  Recursive Union  (cost=0.00..50.75 rows=465 width=55) (actual time=0.013..0.063 rows=14.00 loops=1)
          Storage: Memory  Maximum Storage: 33kB
          Buffers: shared hit=3
          ->  Seq Scan on spaces s  (cost=0.00..1.90 rows=5 width=55) (actual time=0.011..0.018 rows=5.00 loops=1)
                Filter: ((parent_space_id IS NULL) AND ((building_id)::text = 'BLD_G_TOWER'::text))
                Rows Removed by Filter: 67
                Buffers: shared hit=1
          ->  Hash Join  (cost=1.62..4.42 rows=46 width=55) (actual time=0.013..0.019 rows=4.50 loops=2)
                Hash Cond: ((c.parent_space_id)::text = (t_1.space_id)::text)
                Buffers: shared hit=2
                ->  Seq Scan on spaces c  (cost=0.00..1.72 rows=72 width=35) (actual time=0.002..0.005 rows=72.00 loops=2)
                      Buffers: shared hit=2
                ->  Hash  (cost=1.00..1.00 rows=50 width=150) (actual time=0.004..0.004 rows=7.00 loops=2)
                      Buckets: 1024  Batches: 1  Memory Usage: 9kB
                      ->  WorkTable Scan on tree t_1  (cost=0.00..1.00 rows=50 width=150) (actual time=0.001..0.001 rows=7.00 loops=2)
  ->  Sort  (cost=40.53..41.70 rows=465 width=228) (actual time=0.174..0.176 rows=18.00 loops=1)
        Sort Key: t.floor_number, t.path, t.space_id, t.space_type, t.depth, d.device_id
        Sort Method: quicksort  Memory: 26kB
        Buffers: shared hit=6
        ->  Hash Left Join  (cost=6.19..19.93 rows=465 width=228) (actual time=0.080..0.140 rows=18.00 loops=1)
              Hash Cond: ((t.space_id)::text = (a.space_id)::text)
              Buffers: shared hit=6
              ->  CTE Scan on tree t  (cost=0.00..9.30 rows=465 width=202) (actual time=0.014..0.068 rows=14.00 loops=1)
                    Storage: Memory  Maximum Storage: 17kB
                    Buffers: shared hit=3
              ->  Hash  (cost=5.37..5.37 rows=66 width=39) (actual time=0.064..0.065 rows=66.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 13kB
                    Buffers: shared hit=3
                    ->  Hash Left Join  (cost=3.49..5.37 rows=66 width=39) (actual time=0.029..0.052 rows=66.00 loops=1)
                          Hash Cond: ((a.device_id)::text = (d.device_id)::text)
                          Buffers: shared hit=3
                          ->  Seq Scan on device_space_assignments a  (cost=0.00..1.70 rows=66 width=27) (actual time=0.005..0.012 rows=66.00 loops=1)
                                Filter: (unassigned_at IS NULL)
                                Rows Removed by Filter: 4
                                Buffers: shared hit=1
                          ->  Hash  (cost=2.66..2.66 rows=66 width=26) (actual time=0.022..0.022 rows=66.00 loops=1)
                                Buckets: 1024  Batches: 1  Memory Usage: 12kB
                                Buffers: shared hit=2
                                ->  Seq Scan on devices d  (cost=0.00..2.66 rows=66 width=26) (actual time=0.004..0.010 rows=66.00 loops=1)
                                      Buffers: shared hit=2
Planning:
  Buffers: shared hit=8
Planning Time: 0.411 ms
Execution Time: 0.239 ms
```

## DB2-3 · Device relocation history

Parameters: `{"device_id": "DEV-PT-TMP-03", "as_of": "2026-10-02 10:00:00+09:00"}`

```
Sort  (cost=4.17..4.18 rows=3 width=53) (actual time=0.063..0.064 rows=3.00 loops=1)
  Sort Key: a.assigned_at
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=2
  ->  Hash Join  (cost=1.91..4.15 rows=3 width=53) (actual time=0.045..0.056 rows=3.00 loops=1)
        Hash Cond: ((sp.space_id)::text = (a.space_id)::text)
        Buffers: shared hit=2
        ->  Seq Scan on spaces sp  (cost=0.00..1.72 rows=72 width=32) (actual time=0.011..0.015 rows=72.00 loops=1)
              Buffers: shared hit=1
        ->  Hash  (cost=1.88..1.88 rows=3 width=37) (actual time=0.017..0.017 rows=3.00 loops=1)
              Buckets: 1024  Batches: 1  Memory Usage: 9kB
              Buffers: shared hit=1
              ->  Seq Scan on device_space_assignments a  (cost=0.00..1.88 rows=3 width=37) (actual time=0.009..0.014 rows=3.00 loops=1)
                    Filter: ((device_id)::text = 'DEV-PT-TMP-03'::text)
                    Rows Removed by Filter: 67
                    Buffers: shared hit=1
Planning:
  Buffers: shared hit=4
Planning Time: 0.255 ms
Execution Time: 0.088 ms
```

## DB2-4 · Missing heartbeat & fault detection

Parameters: `{"as_of": "2026-10-02 10:00:00+09:00", "silent_minutes": 15}`

```
Sort  (cost=69.41..69.42 rows=1 width=95) (actual time=0.499..0.500 rows=4.00 loops=1)
  Sort Key: ((InitPlan 1).col1) NULLS FIRST, d.device_id
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=159
  ->  Nested Loop Left Join  (cost=0.94..69.40 rows=1 width=95) (actual time=0.118..0.486 rows=4.00 loops=1)
        Buffers: shared hit=159
        ->  Nested Loop Left Join  (cost=0.57..69.00 rows=1 width=51) (actual time=0.098..0.434 rows=4.00 loops=1)
              Buffers: shared hit=150
              ->  Nested Loop Left Join  (cost=0.42..68.65 rows=1 width=46) (actual time=0.094..0.421 rows=4.00 loops=1)
                    Buffers: shared hit=142
                    ->  Nested Loop Anti Join  (cost=0.29..68.36 rows=1 width=33) (actual time=0.075..0.365 rows=4.00 loops=1)
                          Buffers: shared hit=134
                          ->  Seq Scan on devices d  (cost=0.00..2.83 rows=65 width=33) (actual time=0.011..0.024 rows=65.00 loops=1)
                                Filter: ((current_status)::text <> 'RETIRED'::text)
                                Rows Removed by Filter: 1
                                Buffers: shared hit=2
                          ->  Index Only Scan using idx_telemetry_device_time on telemetry t  (cost=0.29..3.21 rows=7 width=14) (actual time=0.005..0.005 rows=0.94 loops=65)
                                Index Cond: ((device_id = (d.device_id)::text) AND (observed_at > ('2026-10-02 10:00:00+09'::timestamp with time zone - '00:15:00'::interval)) AND (observed_at <= '2026-10-02 10:00:00+09'::timestamp with time zone))
                                Heap Fetches: 1
                                Index Searches: 65
                                Buffers: shared hit=132
                    ->  Index Scan using ex_assignment_no_overlap on device_space_assignments a  (cost=0.14..0.28 rows=1 width=27) (actual time=0.013..0.013 rows=1.00 loops=4)
                          Index Cond: ((device_id)::text = (d.device_id)::text)
                          Filter: (unassigned_at IS NULL)
                          Index Searches: 4
                          Buffers: shared hit=8
              ->  Index Scan using spaces_pkey on spaces sp  (cost=0.14..0.34 rows=1 width=30) (actual time=0.003..0.003 rows=1.00 loops=4)
                    Index Cond: ((space_id)::text = (a.space_id)::text)
                    Index Searches: 4
                    Buffers: shared hit=8
        ->  Result  (cost=0.37..0.38 rows=1 width=8) (actual time=0.011..0.011 rows=1.00 loops=4)
              Buffers: shared hit=9
              InitPlan 1
                ->  Limit  (cost=0.29..0.37 rows=1 width=8) (actual time=0.010..0.010 rows=0.75 loops=4)
                      Buffers: shared hit=9
                      ->  Index Only Scan using idx_telemetry_device_time on telemetry t_1  (cost=0.29..10.64 rows=118 width=8) (actual time=0.010..0.010 rows=0.75 loops=4)
                            Index Cond: ((device_id = (d.device_id)::text) AND (observed_at <= '2026-10-02 10:00:00+09'::timestamp with time zone))
                            Heap Fetches: 0
                            Index Searches: 4
                            Buffers: shared hit=9
Planning:
  Buffers: shared hit=16
Planning Time: 0.682 ms
Execution Time: 0.535 ms
```

## DB2-5 · VIP facility state retrieval

Parameters: `{"building_id": "BLD_G_TOWER", "as_of": "2026-10-02 10:00:00+09:00"}`

```
Sort  (cost=61.45..61.48 rows=13 width=92) (actual time=0.359..0.361 rows=13.00 loops=1)
  Sort Key: sp.floor_number, sp.space_name, d.device_id
  Sort Method: quicksort  Memory: 26kB
  Buffers: shared hit=57
  ->  Nested Loop Left Join  (cost=5.59..61.21 rows=13 width=92) (actual time=0.108..0.345 rows=13.00 loops=1)
        Buffers: shared hit=57
        ->  Nested Loop Left Join  (cost=4.41..45.42 rows=13 width=70) (actual time=0.092..0.263 rows=13.00 loops=1)
              Join Filter: ((mt.metric_code)::text = (t.metric)::text)
              Rows Removed by Join Filter: 5
              Buffers: shared hit=44
              ->  Nested Loop Left Join  (cost=4.41..43.88 rows=13 width=67) (actual time=0.083..0.249 rows=13.00 loops=1)
                    Buffers: shared hit=43
                    ->  Hash Join  (cost=4.12..7.16 rows=13 width=41) (actual time=0.062..0.076 rows=13.00 loops=1)
                          Hash Cond: ((d.device_id)::text = (a.device_id)::text)
                          Buffers: shared hit=4
                          ->  Seq Scan on devices d  (cost=0.00..2.66 rows=66 width=33) (actual time=0.011..0.015 rows=66.00 loops=1)
                                Buffers: shared hit=2
                          ->  Hash  (cost=3.96..3.96 rows=13 width=22) (actual time=0.047..0.047 rows=13.00 loops=1)
                                Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                Buffers: shared hit=2
                                ->  Hash Join  (cost=2.07..3.96 rows=13 width=22) (actual time=0.028..0.043 rows=13.00 loops=1)
                                      Hash Cond: ((a.space_id)::text = (sp.space_id)::text)
                                      Buffers: shared hit=2
                                      ->  Seq Scan on device_space_assignments a  (cost=0.00..1.70 rows=66 width=27) (actual time=0.005..0.012 rows=66.00 loops=1)
                                            Filter: (unassigned_at IS NULL)
                                            Rows Removed by Filter: 4
                                            Buffers: shared hit=1
                                      ->  Hash  (cost=1.90..1.90 rows=14 width=20) (actual time=0.019..0.019 rows=14.00 loops=1)
                                            Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                            Buffers: shared hit=1
                                            ->  Seq Scan on spaces sp  (cost=0.00..1.90 rows=14 width=20) (actual time=0.005..0.013 rows=14.00 loops=1)
                                                  Filter: ((building_id)::text = 'BLD_G_TOWER'::text)
                                                  Rows Removed by Filter: 58
                                                  Buffers: shared hit=1
                    ->  Limit  (cost=0.29..2.81 rows=1 width=26) (actual time=0.013..0.013 rows=1.00 loops=13)
                          Buffers: shared hit=39
                          ->  Index Scan using idx_telemetry_device_time on telemetry t  (cost=0.29..298.82 rows=118 width=26) (actual time=0.012..0.012 rows=1.00 loops=13)
                                Index Cond: (((device_id)::text = (d.device_id)::text) AND (observed_at <= '2026-10-02 10:00:00+09'::timestamp with time zone))
                                Filter: ((quality_flag)::text = 'GOOD'::text)
                                Index Searches: 13
                                Buffers: shared hit=39
              ->  Materialize  (cost=0.00..1.04 rows=3 width=13) (actual time=0.001..0.001 rows=1.38 loops=13)
                    Storage: Memory  Maximum Storage: 17kB
                    Buffers: shared hit=1
                    ->  Seq Scan on metric_types mt  (cost=0.00..1.03 rows=3 width=13) (actual time=0.004..0.004 rows=3.00 loops=1)
                          Buffers: shared hit=1
        ->  Aggregate  (cost=1.18..1.19 rows=1 width=16) (actual time=0.004..0.004 rows=1.00 loops=13)
              Buffers: shared hit=13
              ->  Seq Scan on maintenance_actions ma  (cost=0.00..1.18 rows=1 width=16) (actual time=0.002..0.002 rows=0.31 loops=13)
                    Filter: ((device_id)::text = (d.device_id)::text)
                    Rows Removed by Filter: 14
                    Buffers: shared hit=13
Planning:
  Buffers: shared hit=16
Planning Time: 0.582 ms
Execution Time: 0.418 ms
```

## DB3-1 · Attractions & accessibility metadata

Parameters: `{"wheelchair_only": 0, "parking_only": 0}`

```
Sort  (cost=58121.57..58121.59 rows=9 width=102) (actual time=13.084..13.086 rows=9.00 loops=1)
  Sort Key: a.attraction_name_ko
  Sort Method: quicksort  Memory: 26kB
  Buffers: shared hit=91
  ->  Nested Loop Left Join  (cost=6432.76..58121.42 rows=9 width=102) (actual time=1.624..13.056 rows=9.00 loops=1)
        Buffers: shared hit=91
        ->  Seq Scan on attractions a  (cost=0.00..1.09 rows=9 width=86) (actual time=0.018..0.021 rows=9.00 loops=1)
              Buffers: shared hit=1
        ->  Limit  (cost=6432.76..6457.79 rows=1 width=40) (actual time=1.446..1.446 rows=1.00 loops=9)
              Buffers: shared hit=90
              ->  Result  (cost=6432.76..19273.15 rows=513 width=40) (actual time=1.446..1.446 rows=1.00 loops=9)
                    Buffers: shared hit=90
                    ->  Sort  (cost=6432.76..6434.04 rows=513 width=64) (actual time=1.438..1.438 rows=1.00 loops=9)
                          Sort Key: (st_distance((a.geom)::geography, (s.geom)::geography, true))
                          Sort Method: top-N heapsort  Memory: 25kB
                          Buffers: shared hit=90
                          ->  Seq Scan on stops s  (cost=0.00..6430.19 rows=513 width=64) (actual time=0.010..1.333 rows=513.00 loops=9)
                                Buffers: shared hit=90
Planning Time: 0.283 ms
Execution Time: 13.133 ms
```

## DB3-2 · Event management & schedule integrity

Parameters: `{"from_date": "2026-09-01", "to_date": "2026-10-31"}`

```
Sort  (cost=2.75..2.77 rows=7 width=150) (actual time=0.147..0.149 rows=7.00 loops=1)
  Sort Key: e.start_date, e.event_id
  Sort Method: quicksort  Memory: 26kB
  Buffers: shared hit=8
  ->  Hash Join  (cost=1.20..2.65 rows=7 width=150) (actual time=0.049..0.139 rows=7.00 loops=1)
        Hash Cond: ((e.attraction_id)::text = (a.attraction_id)::text)
        Buffers: shared hit=8
        ->  Seq Scan on events e  (cost=0.00..1.10 rows=7 width=109) (actual time=0.011..0.014 rows=7.00 loops=1)
              Filter: (daterange(start_date, end_date, '[]'::text) && '[2026-09-01,2026-11-01)'::daterange)
              Buffers: shared hit=1
        ->  Hash  (cost=1.09..1.09 rows=9 width=37) (actual time=0.008..0.008 rows=9.00 loops=1)
              Buckets: 1024  Batches: 1  Memory Usage: 9kB
              Buffers: shared hit=1
              ->  Seq Scan on attractions a  (cost=0.00..1.09 rows=9 width=37) (actual time=0.004..0.005 rows=9.00 loops=1)
                    Buffers: shared hit=1
        SubPlan 1
          ->  Nested Loop Anti Join  (cost=0.01..32.22 rows=995 width=0) (actual time=0.017..0.017 rows=0.33 loops=6)
                Join Filter: (h.day_of_week = (EXTRACT(dow FROM d.d))::integer)
                Rows Removed by Join Filter: 13
                Buffers: shared hit=6
                ->  Function Scan on generate_series d  (cost=0.01..10.01 rows=1000 width=8) (actual time=0.002..0.002 rows=3.67 loops=6)
                ->  Materialize  (cost=0.00..2.21 rows=1 width=2) (actual time=0.002..0.003 rows=4.50 loops=22)
                      Storage: Memory  Maximum Storage: 17kB
                      Buffers: shared hit=6
                      ->  Seq Scan on operating_hours h  (cost=0.00..2.21 rows=1 width=2) (actual time=0.005..0.006 rows=5.33 loops=6)
                            Filter: ((opens_at <= e.daily_start_time) AND (closes_at >= e.daily_end_time) AND ((attraction_id)::text = (e.attraction_id)::text))
                            Rows Removed by Filter: 38
                            Buffers: shared hit=6
Planning:
  Buffers: shared hit=8
Planning Time: 0.320 ms
Execution Time: 0.179 ms
```

## DB3-3 · Official statistics vs. synthetic samples

Parameters: `{"month": "2026-09-01"}`

```
Sort  (cost=117.27..117.29 rows=9 width=71) (actual time=1.646..1.648 rows=9.00 loops=1)
  Sort Key: o.visitor_count DESC
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=155
  ->  Nested Loop Left Join  (cost=20.23..117.13 rows=9 width=71) (actual time=0.243..1.640 rows=9.00 loops=1)
        Buffers: shared hit=155
        ->  Hash Join  (cost=2.54..4.69 rows=9 width=63) (actual time=0.042..0.067 rows=9.00 loops=1)
              Hash Cond: ((o.source_id)::text = (r.source_id)::text)
              Buffers: shared hit=3
              ->  Hash Join  (cost=1.20..3.32 rows=9 width=57) (actual time=0.020..0.041 rows=9.00 loops=1)
                    Hash Cond: ((o.attraction_id)::text = (a.attraction_id)::text)
                    Buffers: shared hit=2
                    ->  Seq Scan on aggregate_visits o  (cost=0.00..2.08 rows=9 width=36) (actual time=0.008..0.025 rows=9.00 loops=1)
                          Filter: (year_month = (date_trunc('month'::text, ('2026-09-01'::date)::timestamp with time zone))::date)
                          Rows Removed by Filter: 45
                          Buffers: shared hit=1
                    ->  Hash  (cost=1.09..1.09 rows=9 width=37) (actual time=0.009..0.009 rows=9.00 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 9kB
                          Buffers: shared hit=1
                          ->  Seq Scan on attractions a  (cost=0.00..1.09 rows=9 width=37) (actual time=0.004..0.005 rows=9.00 loops=1)
                                Buffers: shared hit=1
              ->  Hash  (cost=1.15..1.15 rows=15 width=29) (actual time=0.021..0.021 rows=15.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on source_registry r  (cost=0.00..1.15 rows=15 width=29) (actual time=0.010..0.013 rows=15.00 loops=1)
                          Buffers: shared hit=1
        ->  Memoize  (cost=17.69..17.70 rows=1 width=16) (actual time=0.172..0.173 rows=1.00 loops=9)
              Cache Key: o.attraction_id, o.year_month
              Cache Mode: binary
              Hits: 0  Misses: 9  Evictions: 0  Overflows: 0  Memory Usage: 2kB
              Buffers: shared hit=152
              ->  Aggregate  (cost=17.68..17.69 rows=1 width=16) (actual time=0.172..0.172 rows=1.00 loops=9)
                    Buffers: shared hit=152
                    ->  Bitmap Heap Scan on sample_visits v  (cost=4.34..17.66 rows=4 width=4) (actual time=0.046..0.109 rows=720.00 loops=9)
                          Recheck Cond: (((attraction_id)::text = (o.attraction_id)::text) AND (hour_start >= ((o.year_month)::timestamp without time zone AT TIME ZONE 'Asia/Seoul'::text)) AND (hour_start < ((o.year_month + '1 mon'::interval) AT TIME ZONE 'Asia/Seoul'::text)))
                          Heap Blocks: exact=81
                          Buffers: shared hit=152
                          ->  Bitmap Index Scan on sample_visits_pkey  (cost=0.00..4.34 rows=4 width=0) (actual time=0.041..0.041 rows=720.00 loops=9)
                                Index Cond: (((attraction_id)::text = (o.attraction_id)::text) AND (hour_start >= ((o.year_month)::timestamp without time zone AT TIME ZONE 'Asia/Seoul'::text)) AND (hour_start < ((o.year_month + '1 mon'::interval) AT TIME ZONE 'Asia/Seoul'::text)))
                                Index Searches: 9
                                Buffers: shared hit=71
Planning:
  Buffers: shared hit=8
Planning Time: 0.356 ms
Execution Time: 1.702 ms
```

## DB3-4 · Open attractions near transit

Parameters: `{"stop_number": "38367", "radius_m": 1500.0, "day_of_week": 5, "at_time": "14:00:00"}`

```
Sort  (cost=42.68..42.69 rows=1 width=158) (actual time=0.190..0.191 rows=7.00 loops=1)
  Sort Key: ((round((st_distance((a.geom)::geography, (st.geom)::geography, true))::numeric, 1))::double precision)
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=19
  ->  Nested Loop  (cost=0.67..42.67 rows=1 width=158) (actual time=0.076..0.178 rows=7.00 loops=1)
        Buffers: shared hit=19
        ->  Nested Loop  (cost=0.54..29.09 rows=1 width=134) (actual time=0.034..0.049 rows=7.00 loops=1)
              Buffers: shared hit=5
              ->  Index Scan using stops_stop_number_key on stops st  (cost=0.28..8.29 rows=1 width=56) (actual time=0.010..0.011 rows=1.00 loops=1)
                    Index Cond: ((stop_number)::text = '38367'::text)
                    Index Searches: 1
                    Buffers: shared hit=3
              ->  Index Scan using idx_attractions_geog on attractions a  (cost=0.26..20.78 rows=1 width=78) (actual time=0.013..0.026 rows=7.00 loops=1)
                    Index Cond: ((geom)::geography && _st_expand((st.geom)::geography, '1500'::double precision))
                    Filter: st_dwithin((geom)::geography, (st.geom)::geography, '1500'::double precision, true)
                    Rows Removed by Filter: 1
                    Index Searches: 1
                    Buffers: shared hit=2
        ->  Index Scan using ex_hours_no_overlap on operating_hours h  (cost=0.14..1.05 rows=1 width=32) (actual time=0.009..0.010 rows=1.00 loops=7)
              Index Cond: (((attraction_id)::text = (a.attraction_id)::text) AND (day_of_week = '5'::smallint))
              Filter: ((opens_at <= '14:00:00'::time without time zone) AND (closes_at > '14:00:00'::time without time zone))
              Rows Removed by Filter: 0
              Index Searches: 7
              Buffers: shared hit=14
Planning:
  Buffers: shared hit=4
Planning Time: 0.341 ms
Execution Time: 0.223 ms
```

## DB3-5 · Temporal demand & crowd forecasting

Parameters: `{"event_date": "2026-10-02", "radius_m": 500.0, "window_min": 120}`

```
Sort  (cost=428.83..428.85 rows=5 width=144) (actual time=6.373..6.376 rows=4.00 loops=1)
  Sort Key: e.expected_attendance DESC
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=2903
  ->  Nested Loop  (cost=86.41..428.78 rows=5 width=144) (actual time=3.146..6.370 rows=4.00 loops=1)
        Buffers: shared hit=2903
        ->  Hash Join  (cost=1.17..2.35 rows=5 width=120) (actual time=0.036..0.044 rows=4.00 loops=1)
              Hash Cond: ((a.attraction_id)::text = (e.attraction_id)::text)
              Buffers: shared hit=2
              ->  Seq Scan on attractions a  (cost=0.00..1.09 rows=9 width=69) (actual time=0.017..0.018 rows=9.00 loops=1)
                    Buffers: shared hit=1
              ->  Hash  (cost=1.10..1.10 rows=5 width=82) (actual time=0.013..0.013 rows=4.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on events e  (cost=0.00..1.10 rows=5 width=82) (actual time=0.006..0.008 rows=4.00 loops=1)
                          Filter: ((NOT is_cancelled) AND ('2026-10-02'::date >= start_date) AND ('2026-10-02'::date <= end_date))
                          Rows Removed by Filter: 3
                          Buffers: shared hit=1
        ->  Aggregate  (cost=85.24..85.25 rows=1 width=16) (actual time=1.576..1.577 rows=1.00 loops=4)
              Buffers: shared hit=2901
              ->  Unique  (cost=85.22..85.23 rows=1 width=25) (actual time=1.554..1.572 rows=30.25 loops=4)
                    Buffers: shared hit=2901
                    ->  Sort  (cost=85.22..85.22 rows=1 width=25) (actual time=1.553..1.557 rows=100.00 loops=4)
                          Sort Key: tr.trip_id, v.capacity
                          Sort Method: quicksort  Memory: 25kB
                          Buffers: shared hit=2901
                          ->  Nested Loop  (cost=5.74..85.21 rows=1 width=25) (actual time=0.081..1.400 rows=100.00 loops=4)
                                Buffers: shared hit=2901
                                ->  Nested Loop  (cost=5.60..85.02 rows=1 width=31) (actual time=0.079..1.188 rows=100.00 loops=4)
                                      Buffers: shared hit=2101
                                      ->  Nested Loop  (cost=5.33..81.98 rows=1 width=20) (actual time=0.073..0.783 rows=100.00 loops=4)
                                            Buffers: shared hit=901
                                            ->  Index Scan using idx_stops_geog on stops s  (cost=0.27..20.79 rows=1 width=13) (actual time=0.028..0.082 rows=11.00 loops=4)
                                                  Index Cond: ((geom)::geography && _st_expand((a.geom)::geography, '500'::double precision))
                                                  Filter: st_dwithin((geom)::geography, (a.geom)::geography, '500'::double precision, true)
                                                  Rows Removed by Filter: 13
                                                  Index Searches: 4
                                                  Buffers: shared hit=42
                                            ->  Bitmap Heap Scan on trip_stop_events te  (cost=5.06..61.18 rows=1 width=33) (actual time=0.017..0.060 rows=9.09 loops=44)
                                                  Recheck Cond: ((s.stop_id)::text = (stop_id)::text)
                                                  Filter: ((scheduled_arrival <= (('2026-10-02'::date + e.daily_start_time) AT TIME ZONE 'Asia/Seoul'::text)) AND (scheduled_arrival > ((('2026-10-02'::date + e.daily_start_time) AT TIME ZONE 'Asia/Seoul'::text) - '02:00:00'::interval)))
                                                  Rows Removed by Filter: 62
                                                  Heap Blocks: exact=760
                                                  Buffers: shared hit=859
                                                  ->  Bitmap Index Scan on idx_events_stop_arrival  (cost=0.00..5.06 rows=104 width=0) (actual time=0.010..0.010 rows=71.41 loops=44)
                                                        Index Cond: ((stop_id)::text = (s.stop_id)::text)
                                                        Index Searches: 44
                                                        Buffers: shared hit=99
                                      ->  Index Scan using trips_pkey on trips tr  (cost=0.27..3.03 rows=1 width=31) (actual time=0.004..0.004 rows=1.00 loops=400)
                                            Index Cond: ((trip_id)::text = (te.trip_id)::text)
                                            Filter: ((status)::text <> 'CANCELLED'::text)
                                            Index Searches: 400
                                            Buffers: shared hit=1200
                                ->  Index Scan using vehicles_pkey on vehicles v  (cost=0.14..0.18 rows=1 width=14) (actual time=0.002..0.002 rows=1.00 loops=400)
                                      Index Cond: ((vehicle_id)::text = (tr.vehicle_id)::text)
                                      Index Searches: 400
                                      Buffers: shared hit=800
Planning:
  Buffers: shared hit=27
Planning Time: 1.003 ms
Execution Time: 6.456 ms
```

## DB4-1 · Station registration & unit standards

Parameters: `{"station": "송도"}`

```
Sort  (cost=6.04..6.05 rows=6 width=81) (actual time=0.068..0.069 rows=6.00 loops=1)
  Sort Key: t.type_code
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=5
  ->  Hash Left Join  (cost=3.52..5.96 rows=6 width=81) (actual time=0.046..0.058 rows=6.00 loops=1)
        Hash Cond: ((t.type_code)::text = (vb.type_code)::text)
        Buffers: shared hit=5
        ->  Hash Left Join  (cost=2.30..4.65 rows=6 width=86) (actual time=0.034..0.039 rows=6.00 loops=1)
              Hash Cond: ((t.type_code)::text = (b.type_code)::text)
              Buffers: shared hit=4
              ->  Nested Loop  (cost=1.07..3.40 rows=6 width=81) (actual time=0.026..0.029 rows=6.00 loops=1)
                    Buffers: shared hit=3
                    ->  Hash Join  (cost=1.07..2.28 rows=1 width=72) (actual time=0.023..0.024 rows=1.00 loops=1)
                          Hash Cond: ((r.source_id)::text = (s.source_id)::text)
                          Buffers: shared hit=2
                          ->  Seq Scan on source_registry r  (cost=0.00..1.15 rows=15 width=29) (actual time=0.010..0.011 rows=15.00 loops=1)
                                Buffers: shared hit=1
                          ->  Hash  (cost=1.06..1.06 rows=1 width=65) (actual time=0.008..0.008 rows=1.00 loops=1)
                                Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                Buffers: shared hit=1
                                ->  Seq Scan on stations s  (cost=0.00..1.06 rows=1 width=65) (actual time=0.005..0.005 rows=1.00 loops=1)
                                      Filter: ((station_name_ko)::text = '송도'::text)
                                      Rows Removed by Filter: 4
                                      Buffers: shared hit=1
                    ->  Seq Scan on measurement_types t  (cost=0.00..1.06 rows=6 width=9) (actual time=0.003..0.003 rows=6.00 loops=1)
                          Buffers: shared hit=1
              ->  Hash  (cost=1.15..1.15 rows=6 width=9) (actual time=0.006..0.006 rows=6.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on alert_rules b  (cost=0.00..1.15 rows=6 width=9) (actual time=0.003..0.005 rows=6.00 loops=1)
                          Filter: ((severity)::text = 'BAD'::text)
                          Rows Removed by Filter: 6
                          Buffers: shared hit=1
        ->  Hash  (cost=1.15..1.15 rows=6 width=9) (actual time=0.005..0.005 rows=6.00 loops=1)
              Buckets: 1024  Batches: 1  Memory Usage: 9kB
              Buffers: shared hit=1
              ->  Seq Scan on alert_rules vb  (cost=0.00..1.15 rows=6 width=9) (actual time=0.003..0.004 rows=6.00 loops=1)
                    Filter: ((severity)::text = 'VERY_BAD'::text)
                    Rows Removed by Filter: 6
                    Buffers: shared hit=1
Planning:
  Buffers: shared hit=8
Planning Time: 0.378 ms
Execution Time: 0.101 ms
```

## DB4-2 · Telemetry ingestion & quality flags

Parameters: `{"station": "송도", "as_of": "2026-10-02 14:30:00+09:00"}`

```
Merge Left Join  (cost=267.95..273.63 rows=6 width=78) (actual time=1.691..1.849 rows=6.00 loops=1)
  Merge Cond: ((m.type_code)::text = (vb.type_code)::text)
  Buffers: shared hit=33
  ->  Merge Left Join  (cost=266.72..272.16 rows=6 width=44) (actual time=1.672..1.820 rows=6.00 loops=1)
        Merge Cond: ((m.type_code)::text = (b.type_code)::text)
        Buffers: shared hit=32
        ->  Merge Join  (cost=265.50..270.83 rows=6 width=39) (actual time=1.662..1.807 rows=6.00 loops=1)
              Merge Cond: ((m.type_code)::text = (t.type_code)::text)
              Buffers: shared hit=31
              ->  Unique  (cost=264.36..269.59 rows=6 width=34) (actual time=1.649..1.790 rows=6.00 loops=1)
                    Buffers: shared hit=30
                    ->  Sort  (cost=264.36..266.97 rows=1046 width=34) (actual time=1.649..1.690 rows=1050.00 loops=1)
                          Sort Key: m.type_code, m.observed_at DESC
                          Sort Method: quicksort  Memory: 106kB
                          Buffers: shared hit=30
                          ->  Nested Loop  (cost=64.69..211.90 rows=1046 width=34) (actual time=0.133..0.363 rows=1050.00 loops=1)
                                Buffers: shared hit=30
                                ->  Seq Scan on stations s  (cost=0.00..1.06 rows=1 width=11) (actual time=0.010..0.011 rows=1.00 loops=1)
                                      Filter: ((station_name_ko)::text = '송도'::text)
                                      Rows Removed by Filter: 4
                                      Buffers: shared hit=1
                                ->  Bitmap Heap Scan on measurements m  (cost=64.69..200.38 rows=1046 width=45) (actual time=0.121..0.215 rows=1050.00 loops=1)
                                      Recheck Cond: (((station_id)::text = (s.station_id)::text) AND (observed_at <= '2026-10-02 14:30:00+09'::timestamp with time zone))
                                      Heap Blocks: exact=15
                                      Buffers: shared hit=29
                                      ->  Bitmap Index Scan on uq_measurement  (cost=0.00..64.42 rows=1046 width=0) (actual time=0.113..0.113 rows=1050.00 loops=1)
                                            Index Cond: (((station_id)::text = (s.station_id)::text) AND (observed_at <= '2026-10-02 14:30:00+09'::timestamp with time zone))
                                            Index Searches: 1
                                            Buffers: shared hit=14
              ->  Sort  (cost=1.14..1.15 rows=6 width=9) (actual time=0.011..0.012 rows=6.00 loops=1)
                    Sort Key: t.type_code
                    Sort Method: quicksort  Memory: 25kB
                    Buffers: shared hit=1
                    ->  Seq Scan on measurement_types t  (cost=0.00..1.06 rows=6 width=9) (actual time=0.005..0.006 rows=6.00 loops=1)
                          Buffers: shared hit=1
        ->  Sort  (cost=1.23..1.24 rows=6 width=9) (actual time=0.009..0.010 rows=6.00 loops=1)
              Sort Key: b.type_code
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=1
              ->  Seq Scan on alert_rules b  (cost=0.00..1.15 rows=6 width=9) (actual time=0.004..0.005 rows=6.00 loops=1)
                    Filter: ((severity)::text = 'BAD'::text)
                    Rows Removed by Filter: 6
                    Buffers: shared hit=1
  ->  Sort  (cost=1.23..1.24 rows=6 width=9) (actual time=0.010..0.010 rows=6.00 loops=1)
        Sort Key: vb.type_code
        Sort Method: quicksort  Memory: 25kB
        Buffers: shared hit=1
        ->  Seq Scan on alert_rules vb  (cost=0.00..1.15 rows=6 width=9) (actual time=0.003..0.004 rows=6.00 loops=1)
              Filter: ((severity)::text = 'VERY_BAD'::text)
              Rows Removed by Filter: 6
              Buffers: shared hit=1
Planning:
  Buffers: shared hit=4
Planning Time: 0.453 ms
Execution Time: 1.887 ms
```

## DB4-3 · Fault handling & late arrivals

Parameters: `{"from_time": "2026-10-02 00:00:00+09:00", "to_time": "2026-10-03 00:00:00+09:00", "late_minutes": 60, "only_problems": 1}`

```
Sort  (cost=892.54..892.60 rows=23 width=52) (actual time=10.291..10.295 rows=7.00 loops=1)
  Sort Key: s.station_name_ko, t.type_code
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=129
  ->  HashAggregate  (cost=662.95..892.02 rows=23 width=52) (actual time=10.213..10.281 rows=7.00 loops=1)
        Group Key: s.station_name_ko, t.type_code
        Filter: ((count(*) FILTER (WHERE ((m.received_at - m.observed_at) > '01:00:00'::interval)) > 0) OR (count(*) FILTER (WHERE ((m.quality_flag)::text = 'SUSPECT'::text)) > 0) OR EXISTS(SubPlan 3))
        Batches: 1  Memory Usage: 32kB
        Rows Removed by Filter: 23
        Buffers: shared hit=129
        ->  Hash Right Join  (cost=2.95..490.15 rows=8640 width=41) (actual time=0.043..6.209 rows=8640.00 loops=1)
              Hash Cond: (((m.station_id)::text = (s.station_id)::text) AND ((m.type_code)::text = (t.type_code)::text))
              Buffers: shared hit=122
              ->  Seq Scan on measurements m  (cost=0.00..249.60 rows=8640 width=44) (actual time=0.006..1.883 rows=8640.00 loops=1)
                    Filter: ((observed_at >= '2026-10-02 00:00:00+09'::timestamp with time zone) AND (observed_at < '2026-10-03 00:00:00+09'::timestamp with time zone))
                    Buffers: shared hit=120
              ->  Hash  (cost=2.50..2.50 rows=30 width=23) (actual time=0.034..0.035 rows=30.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 10kB
                    Buffers: shared hit=2
                    ->  Nested Loop  (cost=0.00..2.50 rows=30 width=23) (actual time=0.019..0.028 rows=30.00 loops=1)
                          Buffers: shared hit=2
                          ->  Seq Scan on measurement_types t  (cost=0.00..1.06 rows=6 width=4) (actual time=0.009..0.010 rows=6.00 loops=1)
                                Buffers: shared hit=1
                          ->  Materialize  (cost=0.00..1.07 rows=5 width=19) (actual time=0.001..0.002 rows=5.00 loops=6)
                                Storage: Memory  Maximum Storage: 17kB
                                Buffers: shared hit=1
                                ->  Seq Scan on stations s  (cost=0.00..1.05 rows=5 width=19) (actual time=0.004..0.005 rows=5.00 loops=1)
                                      Buffers: shared hit=1
        SubPlan 1
          ->  Aggregate  (cost=0.00..0.01 rows=1 width=8) (actual time=0.002..0.002 rows=1.00 loops=7)
                ->  Seq Scan on quarantine_records q  (cost=0.00..0.00 rows=1 width=0) (actual time=0.000..0.001 rows=0.00 loops=7)
                      Filter: (((domain_schema)::text = 'environment'::text) AND ((target_table)::text = 'measurements'::text) AND ((reason_code)::text = 'FAULT_READING'::text) AND ((payload ->> 'station_name'::text) = (s.station_name_ko)::text) AND ((payload ->> 'type_code'::text) = (t.type_code)::text))
        SubPlan 2
          ->  Aggregate  (cost=9.92..9.93 rows=1 width=8) (actual time=0.005..0.005 rows=1.00 loops=7)
                Buffers: shared hit=7
                ->  Index Scan using idx_audit_entity on event_audit_log a  (cost=0.13..9.92 rows=1 width=0) (actual time=0.005..0.005 rows=0.00 loops=7)
                      Index Cond: (((domain_schema)::text = 'environment'::text) AND ((table_name)::text = 'measurements'::text))
                      Filter: (((action)::text = 'UPDATE'::text) AND ((payload_snapshot ->> 'station_name'::text) = (s.station_name_ko)::text) AND ((payload_snapshot ->> 'type_code'::text) = (t.type_code)::text))
                      Index Searches: 7
                      Buffers: shared hit=7
        SubPlan 3
          ->  Seq Scan on quarantine_records q_1  (cost=0.00..0.00 rows=1 width=0) (actual time=0.001..0.001 rows=0.00 loops=23)
                Filter: (((domain_schema)::text = 'environment'::text) AND ((reason_code)::text = 'FAULT_READING'::text) AND ((payload ->> 'station_name'::text) = (s.station_name_ko)::text) AND ((payload ->> 'type_code'::text) = (t.type_code)::text))
Planning:
  Buffers: shared hit=15
Planning Time: 0.747 ms
Execution Time: 10.358 ms
```

## DB4-4 · Hourly rollup & threshold alerts

Parameters: `{"station": "송도", "type_code": "PM25", "day": "2026-10-02"}`

```
Sort  (cost=73.17..73.23 rows=24 width=62) (actual time=0.268..0.270 rows=24.00 loops=1)
  Sort Key: h.hour_start
  Sort Method: quicksort  Memory: 26kB
  Buffers: shared hit=34
  ->  Nested Loop Left Join  (cost=6.98..72.62 rows=24 width=62) (actual time=0.060..0.259 rows=24.00 loops=1)
        Buffers: shared hit=34
        ->  Nested Loop  (cost=4.64..15.74 rows=24 width=47) (actual time=0.037..0.048 rows=24.00 loops=1)
              Buffers: shared hit=6
              ->  Seq Scan on measurement_types t  (cost=0.00..1.07 rows=1 width=9) (actual time=0.010..0.011 rows=1.00 loops=1)
                    Filter: ((type_code)::text = 'PM25'::text)
                    Rows Removed by Filter: 5
                    Buffers: shared hit=1
              ->  Nested Loop  (cost=4.64..14.42 rows=24 width=42) (actual time=0.025..0.033 rows=24.00 loops=1)
                    Buffers: shared hit=5
                    ->  Seq Scan on stations s  (cost=0.00..1.06 rows=1 width=11) (actual time=0.003..0.004 rows=1.00 loops=1)
                          Filter: ((station_name_ko)::text = '송도'::text)
                          Rows Removed by Filter: 4
                          Buffers: shared hit=1
                    ->  Bitmap Heap Scan on hourly_summaries h  (cost=4.64..13.12 rows=24 width=42) (actual time=0.021..0.024 rows=24.00 loops=1)
                          Recheck Cond: (((station_id)::text = (s.station_id)::text) AND ((type_code)::text = 'PM25'::text) AND (hour_start >= '2026-10-02 00:00:00+09'::timestamp with time zone) AND (hour_start < '2026-10-03 00:00:00+09'::timestamp with time zone))
                          Heap Blocks: exact=2
                          Buffers: shared hit=4
                          ->  Bitmap Index Scan on hourly_summaries_pkey  (cost=0.00..4.64 rows=24 width=0) (actual time=0.015..0.015 rows=24.00 loops=1)
                                Index Cond: (((station_id)::text = (s.station_id)::text) AND ((type_code)::text = 'PM25'::text) AND (hour_start >= '2026-10-02 00:00:00+09'::timestamp with time zone) AND (hour_start < '2026-10-03 00:00:00+09'::timestamp with time zone))
                                Index Searches: 1
                                Buffers: shared hit=2
        ->  Limit  (cost=2.34..2.34 rows=1 width=26) (actual time=0.007..0.007 rows=0.12 loops=24)
              Buffers: shared hit=28
              ->  Sort  (cost=2.34..2.34 rows=1 width=26) (actual time=0.007..0.007 rows=0.12 loops=24)
                    Sort Key: r.threshold_value DESC
                    Sort Method: quicksort  Memory: 25kB
                    Buffers: shared hit=28
                    ->  Hash Join  (cost=1.15..2.33 rows=1 width=26) (actual time=0.004..0.004 rows=0.17 loops=24)
                          Hash Cond: (r.rule_id = al.rule_id)
                          Buffers: shared hit=28
                          ->  Seq Scan on alert_rules r  (cost=0.00..1.12 rows=12 width=15) (actual time=0.001..0.002 rows=9.25 loops=4)
                                Buffers: shared hit=4
                          ->  Hash  (cost=1.14..1.14 rows=1 width=19) (actual time=0.003..0.003 rows=0.17 loops=24)
                                Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                Buffers: shared hit=24
                                ->  Seq Scan on alerts al  (cost=0.00..1.14 rows=1 width=19) (actual time=0.002..0.002 rows=0.17 loops=24)
                                      Filter: ((h.hour_start >= started_hour) AND (h.hour_start <= last_exceeded_hour) AND ((station_id)::text = (h.station_id)::text) AND ((type_code)::text = (h.type_code)::text))
                                      Rows Removed by Filter: 7
                                      Buffers: shared hit=24
Planning:
  Buffers: shared hit=4
Planning Time: 0.406 ms
Execution Time: 0.303 ms
```

## DB4-5 · Contextual air exposure service

Parameters: `{"lon": 126.6548, "lat": 37.3888, "as_of": "2026-10-02 14:30:00+09:00"}`

```
Sort  (cost=279.06..279.08 rows=6 width=77) (actual time=1.697..1.699 rows=6.00 loops=1)
  Sort Key: m.type_code
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=34
  ->  Hash Left Join  (cost=259.11..278.98 rows=6 width=77) (actual time=1.540..1.692 rows=6.00 loops=1)
        Hash Cond: ((m.type_code)::text = (vb.type_code)::text)
        Buffers: shared hit=34
        ->  Hash Left Join  (cost=257.88..277.61 rows=6 width=43) (actual time=1.525..1.671 rows=6.00 loops=1)
              Hash Cond: ((m.type_code)::text = (b.type_code)::text)
              Buffers: shared hit=33
              ->  Hash Join  (cost=256.66..276.36 rows=6 width=38) (actual time=1.509..1.653 rows=6.00 loops=1)
                    Hash Cond: ((m.type_code)::text = (t.type_code)::text)
                    Buffers: shared hit=32
                    ->  Nested Loop  (cost=255.52..275.20 rows=6 width=33) (actual time=1.489..1.631 rows=6.00 loops=1)
                          Buffers: shared hit=31
                          ->  Limit  (cost=0.13..14.51 rows=1 width=35) (actual time=0.054..0.054 rows=1.00 loops=1)
                                Buffers: shared hit=2
                                ->  Index Scan using idx_env_stations_geog on stations s  (cost=0.13..72.03 rows=5 width=35) (actual time=0.054..0.054 rows=1.00 loops=1)
                                      Order By: ((geom)::geography <-> '0101000020E6100000EE5A423EE8A95F40A857CA32C4B14240'::geography)
                                      Index Searches: 1
                                      Buffers: shared hit=2
                          ->  Unique  (cost=255.39..260.62 rows=6 width=17) (actual time=1.433..1.574 rows=6.00 loops=1)
                                Buffers: shared hit=29
                                ->  Sort  (cost=255.39..258.01 rows=1045 width=17) (actual time=1.433..1.475 rows=1050.00 loops=1)
                                      Sort Key: m.type_code, m.observed_at DESC
                                      Sort Method: quicksort  Memory: 87kB
                                      Buffers: shared hit=29
                                      ->  Bitmap Heap Scan on measurements m  (cost=64.69..202.99 rows=1045 width=17) (actual time=0.130..0.337 rows=1050.00 loops=1)
                                            Recheck Cond: (((station_id)::text = (s.station_id)::text) AND (observed_at <= '2026-10-02 14:30:00+09'::timestamp with time zone))
                                            Filter: ((quality_flag)::text = 'GOOD'::text)
                                            Heap Blocks: exact=15
                                            Buffers: shared hit=29
                                            ->  Bitmap Index Scan on uq_measurement  (cost=0.00..64.42 rows=1046 width=0) (actual time=0.119..0.119 rows=1050.00 loops=1)
                                                  Index Cond: (((station_id)::text = (s.station_id)::text) AND (observed_at <= '2026-10-02 14:30:00+09'::timestamp with time zone))
                                                  Index Searches: 1
                                                  Buffers: shared hit=14
                    ->  Hash  (cost=1.06..1.06 rows=6 width=9) (actual time=0.017..0.017 rows=6.00 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 9kB
                          Buffers: shared hit=1
                          ->  Seq Scan on measurement_types t  (cost=0.00..1.06 rows=6 width=9) (actual time=0.013..0.014 rows=6.00 loops=1)
                                Buffers: shared hit=1
              ->  Hash  (cost=1.15..1.15 rows=6 width=9) (actual time=0.011..0.011 rows=6.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on alert_rules b  (cost=0.00..1.15 rows=6 width=9) (actual time=0.006..0.008 rows=6.00 loops=1)
                          Filter: ((severity)::text = 'BAD'::text)
                          Rows Removed by Filter: 6
                          Buffers: shared hit=1
        ->  Hash  (cost=1.15..1.15 rows=6 width=9) (actual time=0.007..0.007 rows=6.00 loops=1)
              Buckets: 1024  Batches: 1  Memory Usage: 9kB
              Buffers: shared hit=1
              ->  Seq Scan on alert_rules vb  (cost=0.00..1.15 rows=6 width=9) (actual time=0.003..0.004 rows=6.00 loops=1)
                    Filter: ((severity)::text = 'VERY_BAD'::text)
                    Rows Removed by Filter: 6
                    Buffers: shared hit=1
Planning Time: 0.808 ms
Execution Time: 1.789 ms
```

## DB5-1 · Public safety facilities registration

Parameters: `{"responder": ""}`

```
Sort  (cost=34168.26..34168.27 rows=5 width=140) (actual time=14.733..14.735 rows=5.00 loops=1)
  Sort Key: f.facility_type, f.facility_name_ko
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=1185
  ->  Nested Loop Left Join  (cost=6434.10..34168.20 rows=5 width=140) (actual time=3.209..14.723 rows=5.00 loops=1)
        Buffers: shared hit=1185
        ->  Hash Join  (cost=1.34..2.40 rows=5 width=108) (actual time=0.025..0.034 rows=5.00 loops=1)
              Hash Cond: ((f.source_id)::text = (r.source_id)::text)
              Buffers: shared hit=2
              ->  Seq Scan on facilities f  (cost=0.00..1.05 rows=5 width=102) (actual time=0.011..0.012 rows=5.00 loops=1)
                    Buffers: shared hit=1
              ->  Hash  (cost=1.15..1.15 rows=15 width=29) (actual time=0.011..0.011 rows=15.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on source_registry r  (cost=0.00..1.15 rows=15 width=29) (actual time=0.005..0.007 rows=15.00 loops=1)
                          Buffers: shared hit=1
        ->  Limit  (cost=6432.76..6457.79 rows=1 width=40) (actual time=1.425..1.425 rows=1.00 loops=5)
              Buffers: shared hit=50
              ->  Result  (cost=6432.76..19273.15 rows=513 width=40) (actual time=1.425..1.425 rows=1.00 loops=5)
                    Buffers: shared hit=50
                    ->  Sort  (cost=6432.76..6434.04 rows=513 width=64) (actual time=1.416..1.416 rows=1.00 loops=5)
                          Sort Key: (st_distance((f.geom)::geography, (s.geom)::geography, true))
                          Sort Method: top-N heapsort  Memory: 25kB
                          Buffers: shared hit=50
                          ->  Seq Scan on stops s  (cost=0.00..6430.19 rows=513 width=64) (actual time=0.010..1.317 rows=513.00 loops=5)
                                Buffers: shared hit=50
        SubPlan 1
          ->  Aggregate  (cost=351.05..351.06 rows=1 width=8) (actual time=1.431..1.431 rows=1.00 loops=5)
                Buffers: shared hit=1110
                ->  Seq Scan on incidents i  (cost=0.00..347.09 rows=1584 width=0) (actual time=0.003..1.356 rows=1580.00 loops=5)
                      Filter: ((NOT is_drill) AND ((assigned_facility_id)::text = (f.facility_id)::text))
                      Rows Removed by Filter: 8427
                      Buffers: shared hit=1110
        SubPlan 2
          ->  Aggregate  (cost=24.28..24.29 rows=1 width=8) (actual time=0.078..0.078 rows=1.00 loops=5)
                Buffers: shared hit=23
                ->  Index Only Scan using idx_drills_facility on response_drills d  (cost=0.28..23.28 rows=400 width=0) (actual time=0.017..0.058 rows=399.60 loops=5)
                      Index Cond: (facility_id = (f.facility_id)::text)
                      Heap Fetches: 0
                      Index Searches: 5
                      Buffers: shared hit=23
Planning Time: 0.308 ms
Execution Time: 14.782 ms
```

## DB5-2 · Incident creation & severity triage

Parameters: `{"from_time": "2026-10-02 00:00:00+09:00", "to_time": "2026-10-03 00:00:00+09:00", "limit": 50}`

```
Limit  (cost=48.36..173.66 rows=10 width=122) (actual time=0.081..0.096 rows=6.00 loops=1)
  Buffers: shared hit=7
  ->  Result  (cost=48.36..173.66 rows=10 width=122) (actual time=0.081..0.096 rows=6.00 loops=1)
        Buffers: shared hit=7
        ->  Sort  (cost=48.36..48.38 rows=10 width=182) (actual time=0.058..0.060 rows=6.00 loops=1)
              Sort Key: sl.severity_rank, i.reported_at
              Sort Method: quicksort  Memory: 26kB
              Buffers: shared hit=7
              ->  Hash Left Join  (cost=6.66..48.19 rows=10 width=182) (actual time=0.042..0.052 rows=6.00 loops=1)
                    Hash Cond: ((i.assigned_facility_id)::text = (f.facility_id)::text)
                    Buffers: shared hit=7
                    ->  Nested Loop  (cost=5.54..46.98 rows=10 width=129) (actual time=0.033..0.041 rows=6.00 loops=1)
                          Join Filter: ((sl.severity)::text = (i.severity)::text)
                          Rows Removed by Join Filter: 9
                          Buffers: shared hit=6
                          ->  Hash Join  (cost=5.54..45.40 rows=10 width=123) (actual time=0.027..0.031 rows=6.00 loops=1)
                                Hash Cond: ((i.type_code)::text = (t.type_code)::text)
                                Buffers: shared hit=5
                                ->  Bitmap Heap Scan on incidents i  (cost=4.41..44.22 rows=10 width=122) (actual time=0.011..0.013 rows=6.00 loops=1)
                                      Recheck Cond: ((reported_at >= '2026-10-02 00:00:00+09'::timestamp with time zone) AND (reported_at < '2026-10-03 00:00:00+09'::timestamp with time zone))
                                      Filter: (NOT is_drill)
                                      Heap Blocks: exact=2
                                      Buffers: shared hit=4
                                      ->  Bitmap Index Scan on idx_incidents_reported  (cost=0.00..4.41 rows=12 width=0) (actual time=0.005..0.005 rows=7.00 loops=1)
                                            Index Cond: ((reported_at >= '2026-10-02 00:00:00+09'::timestamp with time zone) AND (reported_at < '2026-10-03 00:00:00+09'::timestamp with time zone))
                                            Index Searches: 1
                                            Buffers: shared hit=2
                                ->  Hash  (cost=1.06..1.06 rows=6 width=22) (actual time=0.013..0.013 rows=6.00 loops=1)
                                      Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                      Buffers: shared hit=1
                                      ->  Seq Scan on incident_types t  (cost=0.00..1.06 rows=6 width=22) (actual time=0.009..0.010 rows=6.00 loops=1)
                                            Buffers: shared hit=1
                          ->  Materialize  (cost=0.00..1.06 rows=4 width=12) (actual time=0.001..0.001 rows=2.50 loops=6)
                                Storage: Memory  Maximum Storage: 17kB
                                Buffers: shared hit=1
                                ->  Seq Scan on severity_levels sl  (cost=0.00..1.04 rows=4 width=12) (actual time=0.002..0.003 rows=4.00 loops=1)
                                      Buffers: shared hit=1
                    ->  Hash  (cost=1.05..1.05 rows=5 width=71) (actual time=0.005..0.005 rows=5.00 loops=1)
                          Buckets: 1024  Batches: 1  Memory Usage: 9kB
                          Buffers: shared hit=1
                          ->  Seq Scan on facilities f  (cost=0.00..1.05 rows=5 width=71) (actual time=0.003..0.004 rows=5.00 loops=1)
                                Buffers: shared hit=1
Planning:
  Buffers: shared hit=20
Planning Time: 0.473 ms
Execution Time: 0.134 ms
```

## DB5-3 · State machine transitions & drills

Parameters: `{"incident_id": "INC-20261002-0001"}`

```
WindowAgg  (cost=24.43..25.78 rows=5 width=101) (actual time=0.065..0.089 rows=5.00 loops=1)
  Window: w AS (ORDER BY h.changed_at, h.history_id)
  Storage: Memory  Maximum Storage: 17kB
  Buffers: shared hit=5
  ->  Sort  (cost=24.40..24.42 rows=5 width=61) (actual time=0.049..0.050 rows=5.00 loops=1)
        Sort Key: h.changed_at, h.history_id
        Sort Method: quicksort  Memory: 25kB
        Buffers: shared hit=5
        ->  Hash Left Join  (cost=5.57..24.35 rows=5 width=61) (actual time=0.039..0.043 rows=5.00 loops=1)
              Hash Cond: ((h.facility_id)::text = (f.facility_id)::text)
              Buffers: shared hit=5
              ->  Bitmap Heap Scan on incident_status_history h  (cost=4.45..23.22 rows=5 width=56) (actual time=0.027..0.028 rows=5.00 loops=1)
                    Recheck Cond: ((incident_id)::text = 'INC-20261002-0001'::text)
                    Heap Blocks: exact=1
                    Buffers: shared hit=4
                    ->  Bitmap Index Scan on idx_history_incident_time  (cost=0.00..4.45 rows=5 width=0) (actual time=0.015..0.015 rows=5.00 loops=1)
                          Index Cond: ((incident_id)::text = 'INC-20261002-0001'::text)
                          Index Searches: 1
                          Buffers: shared hit=3
              ->  Hash  (cost=1.05..1.05 rows=5 width=39) (actual time=0.009..0.010 rows=5.00 loops=1)
                    Buckets: 1024  Batches: 1  Memory Usage: 9kB
                    Buffers: shared hit=1
                    ->  Seq Scan on facilities f  (cost=0.00..1.05 rows=5 width=39) (actual time=0.005..0.006 rows=5.00 loops=1)
                          Buffers: shared hit=1
  SubPlan 1
    ->  Aggregate  (cost=0.22..0.23 rows=1 width=32) (actual time=0.013..0.013 rows=1.00 loops=1)
          ->  Sort  (cost=0.21..0.22 rows=2 width=32) (actual time=0.011..0.011 rows=0.00 loops=1)
                Sort Key: nxt.nxt
                Sort Method: quicksort  Memory: 25kB
                ->  Function Scan on unnest nxt  (cost=0.00..0.20 rows=2 width=32) (actual time=0.008..0.008 rows=0.00 loops=1)
                      Filter: COALESCE(((((h.to_status)::text = 'REPORTED'::text) AND (nxt = 'DISPATCHED'::text)) OR (((h.to_status)::text = 'REPORTED'::text) AND (nxt = 'CANCELLED'::text)) OR (((h.to_status)::text = 'DISPATCHED'::text) AND (nxt = 'ON_SCENE'::text)) OR (((h.to_status)::text = 'DISPATCHED'::text) AND (nxt = 'CANCELLED'::text)) OR (((h.to_status)::text = 'ON_SCENE'::text) AND (nxt = 'RESOLVED'::text)) OR (((h.to_status)::text = 'RESOLVED'::text) AND (nxt = 'CLOSED'::text))), false)
                      Rows Removed by Filter: 5
Planning:
  Buffers: shared hit=4
Planning Time: 0.386 ms
Execution Time: 0.140 ms
```

## DB5-4 · Response time analytics by facility

Parameters: `{"from_date": "2026-07-01", "to_date": "2026-10-02", "include_drills": 0}`

```
Sort  (cost=2705.84..2705.85 rows=5 width=121) (actual time=31.931..31.936 rows=5.00 loops=1)
  Sort Key: (rank() OVER w1)
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=893
  ->  WindowAgg  (cost=2705.56..2705.78 rows=5 width=121) (actual time=31.915..31.930 rows=5.00 loops=1)
        Window: w1 AS (ORDER BY (avg((EXTRACT(epoch FROM (h.changed_at - i.reported_at)))::integer)) ROWS UNBOUNDED PRECEDING)
        Storage: Memory  Maximum Storage: 17kB
        Buffers: shared hit=893
        ->  Sort  (cost=2705.55..2705.56 rows=5 width=117) (actual time=31.899..31.902 rows=5.00 loops=1)
              Sort Key: (avg((EXTRACT(epoch FROM (h.changed_at - i.reported_at)))::integer))
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=893
              ->  GroupAggregate  (cost=2372.71..2705.49 rows=5 width=117) (actual time=26.460..31.896 rows=5.00 loops=1)
                    Group Key: f.facility_id
                    Buffers: shared hit=893
                    ->  Sort  (cost=2372.71..2392.28 rows=7828 width=73) (actual time=24.479..24.813 rows=7760.00 loops=1)
                          Sort Key: f.facility_id
                          Sort Method: quicksort  Memory: 924kB
                          Buffers: shared hit=893
                          ->  Hash Join  (cost=473.65..1866.46 rows=7828 width=73) (actual time=3.636..21.081 rows=7760.00 loops=1)
                                Hash Cond: ((i.assigned_facility_id)::text = (f.facility_id)::text)
                                Buffers: shared hit=893
                                ->  Hash Join  (cost=472.53..1827.56 rows=7828 width=37) (actual time=3.616..18.254 rows=7760.00 loops=1)
                                      Hash Cond: ((i.severity)::text = (sl.severity)::text)
                                      Buffers: shared hit=892
                                      ->  Hash Join  (cost=471.44..1784.15 rows=7828 width=38) (actual time=3.607..15.726 rows=7760.00 loops=1)
                                            Hash Cond: ((h.incident_id)::text = (i.incident_id)::text)
                                            Buffers: shared hit=891
                                            ->  Seq Scan on incident_status_history h  (cost=0.00..1286.83 rows=9857 width=26) (actual time=0.006..8.339 rows=9759.00 loops=1)
                                                  Filter: ((to_status)::text = 'ON_SCENE'::text)
                                                  Rows Removed by Filter: 39667
                                                  Buffers: shared hit=669
                                            ->  Hash  (cost=372.11..372.11 rows=7947 width=48) (actual time=3.577..3.577 rows=8008.00 loops=1)
                                                  Buckets: 8192  Batches: 1  Memory Usage: 702kB
                                                  Buffers: shared hit=222
                                                  ->  Seq Scan on incidents i  (cost=0.00..372.11 rows=7947 width=48) (actual time=0.004..2.018 rows=8008.00 loops=1)
                                                        Filter: ((NOT is_drill) AND (reported_at >= '2026-07-01 00:00:00+09'::timestamp with time zone) AND (reported_at < '2026-10-03 00:00:00+09'::timestamp with time zone))
                                                        Rows Removed by Filter: 1999
                                                        Buffers: shared hit=222
                                      ->  Hash  (cost=1.04..1.04 rows=4 width=10) (actual time=0.007..0.007 rows=4.00 loops=1)
                                            Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                            Buffers: shared hit=1
                                            ->  Seq Scan on severity_levels sl  (cost=0.00..1.04 rows=4 width=10) (actual time=0.004..0.005 rows=4.00 loops=1)
                                                  Buffers: shared hit=1
                                ->  Hash  (cost=1.05..1.05 rows=5 width=53) (actual time=0.016..0.016 rows=5.00 loops=1)
                                      Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                      Buffers: shared hit=1
                                      ->  Seq Scan on facilities f  (cost=0.00..1.05 rows=5 width=53) (actual time=0.009..0.010 rows=5.00 loops=1)
                                            Buffers: shared hit=1
Planning:
  Buffers: shared hit=31
Planning Time: 0.629 ms
Execution Time: 32.192 ms
```

## DB5-4B · Incident hotspots (spatial clusters)

Parameters: `{"from_date": "2026-07-01", "to_date": "2026-10-02", "eps_m": 40.0, "min_points": 40, "limit": 50}`

```
Limit  (cost=243817.59..243819.46 rows=50 width=90) (actual time=61.493..61.516 rows=5.00 loops=1)
  Buffers: shared hit=227
  ->  WindowAgg  (cost=243817.59..243825.07 rows=200 width=90) (actual time=61.493..61.515 rows=5.00 loops=1)
        Window: w1 AS (ORDER BY (count(*)) ROWS UNBOUNDED PRECEDING)
        Storage: Memory  Maximum Storage: 17kB
        Buffers: shared hit=227
        ->  Sort  (cost=243817.57..243818.07 rows=200 width=98) (actual time=61.480..61.482 rows=5.00 loops=1)
              Sort Key: (count(*)) DESC
              Sort Method: quicksort  Memory: 25kB
              Buffers: shared hit=227
              ->  Nested Loop Left Join  (cost=199906.78..243809.93 rows=200 width=98) (actual time=55.146..61.476 rows=5.00 loops=1)
                    Buffers: shared hit=227
                    ->  GroupAggregate  (cost=199717.98..201037.93 rows=200 width=76) (actual time=55.037..61.015 rows=5.00 loops=1)
                          Group Key: clustered.cluster_id
                          Buffers: shared hit=222
                          ->  Sort  (cost=199717.98..199737.75 rows=7907 width=47) (actual time=54.114..54.385 rows=4389.00 loops=1)
                                Sort Key: clustered.cluster_id
                                Sort Method: quicksort  Memory: 474kB
                                Buffers: shared hit=222
                                ->  Subquery Scan on clustered  (cost=199101.52..199206.05 rows=7907 width=47) (actual time=51.267..53.271 rows=4389.00 loops=1)
                                      Filter: (clustered.cluster_id IS NOT NULL)
                                      Rows Removed by Filter: 3619
                                      Buffers: shared hit=222
                                      ->  WindowAgg  (cost=199101.52..199126.58 rows=7947 width=47) (actual time=51.266..52.693 rows=8008.00 loops=1)
                                            Window: w1 AS ()
                                            Storage: Memory  Maximum Storage: 590kB
                                            Buffers: shared hit=222
                                            ->  Seq Scan on incidents i  (cost=0.00..372.11 rows=7947 width=43) (actual time=0.012..2.109 rows=8008.00 loops=1)
                                                  Filter: ((NOT is_drill) AND (reported_at >= '2026-07-01 00:00:00+09'::timestamp with time zone) AND (reported_at < '2026-10-03 00:00:00+09'::timestamp with time zone))
                                                  Rows Removed by Filter: 1999
                                                  Buffers: shared hit=222
                    ->  Limit  (cost=188.80..213.83 rows=1 width=34) (actual time=0.090..0.090 rows=1.00 loops=5)
                          Buffers: shared hit=5
                          ->  Result  (cost=188.80..564.25 rows=15 width=34) (actual time=0.089..0.089 rows=1.00 loops=5)
                                Buffers: shared hit=5
                                ->  Sort  (cost=188.80..188.84 rows=15 width=62) (actual time=0.081..0.081 rows=1.00 loops=5)
                                      Sort Key: (st_distance(((st_centroid(st_collect(clustered.geom))))::geography, (p.geom)::geography, true))
                                      Sort Method: top-N heapsort  Memory: 25kB
                                      Buffers: shared hit=5
                                      ->  Seq Scan on places p  (cost=0.00..188.72 rows=15 width=62) (actual time=0.018..0.066 rows=15.00 loops=5)
                                            Buffers: shared hit=5
Planning:
  Buffers: shared hit=7
Planning Time: 0.405 ms
Execution Time: 61.671 ms
```

## DB5-5 · Smart Mobility hazard avoidance

Parameters: `{"as_of": "2026-10-02 18:30:00+09:00"}`

```
Nested Loop  (cost=5.53..127559.17 rows=472 width=140) (actual time=7.821..8.066 rows=2.00 loops=1)
  Join Filter: ((t.type_code)::text = (i.type_code)::text)
  Rows Removed by Join Filter: 5
  Buffers: shared hit=2838
  ->  Nested Loop  (cost=5.53..5240.64 rows=472 width=107) (actual time=7.405..7.426 rows=2.00 loops=1)
        Buffers: shared hit=2743
        ->  Index Scan using idx_incidents_reported on incidents i  (cost=0.29..913.55 rows=472 width=98) (actual time=0.132..0.762 rows=521.00 loops=1)
              Index Cond: ((reported_at <= '2026-10-02 18:30:00+09'::timestamp with time zone) AND (reported_at > ('2026-10-02 18:30:00+09'::timestamp with time zone - '7 days'::interval)))
              Filter: ((NOT is_drill) AND (restriction_radius_m > 0))
              Rows Removed by Filter: 138
              Index Searches: 1
              Buffers: shared hit=654
        ->  Subquery Scan on st  (cost=5.24..9.16 rows=1 width=9) (actual time=0.013..0.013 rows=0.00 loops=521)
              Filter: ((st.status)::text = ANY ('{REPORTED,DISPATCHED,ON_SCENE}'::text[]))
              Rows Removed by Filter: 1
              Buffers: shared hit=2089
              ->  Limit  (cost=5.24..9.14 rows=1 width=25) (actual time=0.012..0.012 rows=1.00 loops=521)
                    Buffers: shared hit=2089
                    ->  Incremental Sort  (cost=5.24..24.74 rows=5 width=25) (actual time=0.012..0.012 rows=1.00 loops=521)
                          Sort Key: h.changed_at DESC, h.history_id DESC
                          Presorted Key: h.changed_at
                          Full-sort Groups: 521  Sort Method: quicksort  Average Memory: 25kB  Peak Memory: 25kB
                          Buffers: shared hit=2089
                          ->  Index Scan using idx_history_incident_time on incident_status_history h  (cost=0.41..24.51 rows=5 width=25) (actual time=0.010..0.010 rows=2.00 loops=521)
                                Index Cond: (((incident_id)::text = (i.incident_id)::text) AND (changed_at <= '2026-10-02 18:30:00+09'::timestamp with time zone))
                                Index Searches: 521
                                Buffers: shared hit=2089
  ->  Materialize  (cost=0.00..1.09 rows=6 width=22) (actual time=0.021..0.022 rows=3.50 loops=2)
        Storage: Memory  Maximum Storage: 17kB
        Buffers: shared hit=1
        ->  Seq Scan on incident_types t  (cost=0.00..1.06 rows=6 width=22) (actual time=0.028..0.029 rows=6.00 loops=1)
              Buffers: shared hit=1
  SubPlan 1
    ->  Aggregate  (cost=20.80..20.81 rows=1 width=8) (actual time=0.054..0.054 rows=1.00 loops=2)
          Buffers: shared hit=12
          ->  Index Scan using idx_stops_geog on stops s  (cost=0.27..20.80 rows=1 width=0) (actual time=0.045..0.046 rows=0.50 loops=2)
                Index Cond: ((geom)::geography && _st_expand((i.geom)::geography, (i.restriction_radius_m)::double precision))
                Filter: st_dwithin((geom)::geography, (i.geom)::geography, (i.restriction_radius_m)::double precision, true)
                Rows Removed by Filter: 4
                Index Searches: 2
                Buffers: shared hit=12
  SubPlan 2
    ->  Aggregate  (cost=199.85..199.86 rows=1 width=8) (actual time=0.120..0.120 rows=1.00 loops=2)
          Buffers: shared hit=82
          ->  Subquery Scan on v  (cost=11.83..199.85 rows=1 width=0) (actual time=0.117..0.118 rows=0.00 loops=2)
                Filter: st_dwithin((v.geom)::geography, (i.geom)::geography, (i.restriction_radius_m)::double precision, true)
                Rows Removed by Filter: 9
                Buffers: shared hit=82
                ->  Unique  (cost=11.83..12.09 rows=15 width=50) (actual time=0.084..0.094 rows=9.00 loops=2)
                      Buffers: shared hit=82
                      ->  Sort  (cost=11.83..11.96 rows=51 width=50) (actual time=0.084..0.087 rows=42.00 loops=2)
                            Sort Key: o.vehicle_id, o.observed_at DESC
                            Sort Method: quicksort  Memory: 27kB
                            Buffers: shared hit=82
                            ->  Index Scan using idx_obs_time on vehicle_observations o  (cost=0.29..10.38 rows=51 width=50) (actual time=0.009..0.030 rows=42.00 loops=2)
                                  Index Cond: ((observed_at <= '2026-10-02 18:30:00+09'::timestamp with time zone) AND (observed_at > ('2026-10-02 18:30:00+09'::timestamp with time zone - '00:02:00'::interval)))
                                  Index Searches: 2
                                  Buffers: shared hit=82
Planning:
  Buffers: shared hit=12
Planning Time: 2.002 ms
Execution Time: 8.256 ms
```

## X-1 · Attractions & events near transit stops

Parameters: `{"stop_number": "38367", "on_date": "2026-10-02", "limit": 50}`

```
Limit  (cost=42.78..42.79 rows=1 width=109) (actual time=0.094..0.095 rows=2.00 loops=1)
  Buffers: shared hit=6
  ->  Sort  (cost=42.78..42.79 rows=1 width=109) (actual time=0.094..0.094 rows=2.00 loops=1)
        Sort Key: ((round((st_distance((s.geom)::geography, (a.geom)::geography, true))::numeric, 1))::double precision), e.start_date
        Sort Method: quicksort  Memory: 25kB
        Buffers: shared hit=6
        ->  Nested Loop Left Join  (cost=0.54..42.77 rows=1 width=109) (actual time=0.076..0.087 rows=2.00 loops=1)
              Join Filter: ((a.attraction_id)::text = (e.attraction_id)::text)
              Rows Removed by Join Filter: 4
              Filter: ((e.event_name_ko IS NULL) OR (('2026-10-02'::date >= e.start_date) AND ('2026-10-02'::date <= e.end_date)))
              Buffers: shared hit=6
              ->  Nested Loop  (cost=0.54..29.09 rows=1 width=131) (actual time=0.042..0.044 rows=1.00 loops=1)
                    Buffers: shared hit=5
                    ->  Index Scan using stops_stop_number_key on stops s  (cost=0.28..8.29 rows=1 width=62) (actual time=0.013..0.013 rows=1.00 loops=1)
                          Index Cond: ((stop_number)::text = '38367'::text)
                          Index Searches: 1
                          Buffers: shared hit=3
                    ->  Index Scan using idx_attractions_geog on attractions a  (cost=0.26..20.78 rows=1 width=69) (actual time=0.021..0.021 rows=1.00 loops=1)
                          Index Cond: ((geom)::geography && _st_expand((s.geom)::geography, '500'::double precision))
                          Filter: st_dwithin((s.geom)::geography, (geom)::geography, '500'::double precision, true)
                          Index Searches: 1
                          Buffers: shared hit=2
              ->  Seq Scan on events e  (cost=0.00..1.07 rows=6 width=65) (actual time=0.012..0.014 rows=6.00 loops=1)
                    Filter: (NOT is_cancelled)
                    Rows Removed by Filter: 1
                    Buffers: shared hit=1
Planning:
  Buffers: shared hit=8
Planning Time: 0.369 ms
Execution Time: 0.130 ms
```

## X-2 · Building devices with outdoor air context

Parameters: `{"building_id": "BLD_G_TOWER"}`

```
Incremental Sort  (cost=323.30..4286.26 rows=78 width=95) (actual time=10.730..10.733 rows=26.00 loops=1)
  Sort Key: d.device_id, m.type_code
  Presorted Key: d.device_id
  Full-sort Groups: 1  Sort Method: quicksort  Average Memory: 27kB  Peak Memory: 27kB
  Buffers: shared hit=421
  ->  Nested Loop Left Join  (cost=259.42..4283.28 rows=78 width=95) (actual time=1.022..10.713 rows=26.00 loops=1)
        Buffers: shared hit=421
        ->  Nested Loop  (cost=8.85..10.62 rows=13 width=91) (actual time=0.219..0.243 rows=13.00 loops=1)
              Buffers: shared hit=5
              ->  Merge Join  (cost=8.85..9.38 rows=13 width=45) (actual time=0.207..0.223 rows=13.00 loops=1)
                    Merge Cond: ((dsa.device_id)::text = (d.device_id)::text)
                    Buffers: shared hit=4
                    ->  Sort  (cost=4.20..4.23 rows=13 width=26) (actual time=0.070..0.072 rows=13.00 loops=1)
                          Sort Key: dsa.device_id
                          Sort Method: quicksort  Memory: 25kB
                          Buffers: shared hit=2
                          ->  Hash Join  (cost=2.07..3.96 rows=13 width=26) (actual time=0.036..0.051 rows=13.00 loops=1)
                                Hash Cond: ((dsa.space_id)::text = (sp.space_id)::text)
                                Buffers: shared hit=2
                                ->  Seq Scan on device_space_assignments dsa  (cost=0.00..1.70 rows=66 width=27) (actual time=0.013..0.020 rows=66.00 loops=1)
                                      Filter: (unassigned_at IS NULL)
                                      Rows Removed by Filter: 4
                                      Buffers: shared hit=1
                                ->  Hash  (cost=1.90..1.90 rows=14 width=24) (actual time=0.019..0.020 rows=14.00 loops=1)
                                      Buckets: 1024  Batches: 1  Memory Usage: 9kB
                                      Buffers: shared hit=1
                                      ->  Seq Scan on spaces sp  (cost=0.00..1.90 rows=14 width=24) (actual time=0.005..0.013 rows=14.00 loops=1)
                                            Filter: ((building_id)::text = 'BLD_G_TOWER'::text)
                                            Rows Removed by Filter: 58
                                            Buffers: shared hit=1
                    ->  Sort  (cost=4.65..4.82 rows=66 width=33) (actual time=0.128..0.130 rows=33.00 loops=1)
                          Sort Key: d.device_id
                          Sort Method: quicksort  Memory: 28kB
                          Buffers: shared hit=2
                          ->  Seq Scan on devices d  (cost=0.00..2.66 rows=66 width=33) (actual time=0.004..0.012 rows=66.00 loops=1)
                                Buffers: shared hit=2
              ->  Materialize  (cost=0.00..1.08 rows=1 width=70) (actual time=0.001..0.001 rows=1.00 loops=13)
                    Storage: Memory  Maximum Storage: 17kB
                    Buffers: shared hit=1
                    ->  Seq Scan on buildings b  (cost=0.00..1.07 rows=1 width=70) (actual time=0.006..0.007 rows=1.00 loops=1)
                          Filter: ((building_id)::text = 'BLD_G_TOWER'::text)
                          Rows Removed by Filter: 5
                          Buffers: shared hit=1
        ->  Nested Loop Left Join  (cost=250.56..253.52 rows=6 width=57) (actual time=0.717..0.793 rows=2.00 loops=13)
              Buffers: shared hit=416
              ->  Limit  (cost=63.60..63.60 rows=1 width=59) (actual time=0.022..0.022 rows=1.00 loops=13)
                    Buffers: shared hit=13
                    ->  Sort  (cost=63.60..63.61 rows=5 width=59) (actual time=0.021..0.021 rows=1.00 loops=13)
                          Sort Key: (st_distance((b.geom)::geography, (s.geom)::geography, true))
                          Sort Method: top-N heapsort  Memory: 25kB
                          Buffers: shared hit=13
                          ->  Seq Scan on stations s  (cost=0.00..63.57 rows=5 width=59) (actual time=0.005..0.016 rows=5.00 loops=13)
                                Buffers: shared hit=13
              ->  Unique  (cost=186.96..189.84 rows=6 width=17) (actual time=0.694..0.769 rows=2.00 loops=13)
                    Buffers: shared hit=403
                    ->  Sort  (cost=186.96..188.40 rows=576 width=17) (actual time=0.693..0.715 rows=576.00 loops=13)
                          Sort Key: m.type_code, m.observed_at DESC
                          Sort Method: quicksort  Memory: 47kB
                          Buffers: shared hit=403
                          ->  Bitmap Heap Scan on measurements m  (cost=30.47..160.55 rows=576 width=17) (actual time=0.046..0.170 rows=576.00 loops=13)
                                Recheck Cond: (((station_id)::text = (s.station_id)::text) AND ((type_code)::text = ANY ('{PM25,PM10}'::text[])))
                                Filter: ((quality_flag)::text = 'GOOD'::text)
                                Heap Blocks: exact=338
                                Buffers: shared hit=403
                                ->  Bitmap Index Scan on uq_measurement  (cost=0.00..30.33 rows=576 width=0) (actual time=0.042..0.042 rows=622.00 loops=13)
                                      Index Cond: (((station_id)::text = (s.station_id)::text) AND ((type_code)::text = ANY ('{PM25,PM10}'::text[])))
                                      Index Searches: 13
                                      Buffers: shared hit=65
Planning:
  Buffers: shared hit=8
Planning Time: 0.752 ms
Execution Time: 10.804 ms
```

## X-3 · Incidents impacting transit & air context

Parameters: `{"limit": 50}`

```
Limit  (cost=145.41..145.41 rows=2 width=167) (actual time=2.189..2.195 rows=50.00 loops=1)
  Buffers: shared hit=190
  ->  Sort  (cost=145.41..145.41 rows=2 width=167) (actual time=2.189..2.191 rows=50.00 loops=1)
        Sort Key: inc.incident_id, ((round((st_distance((inc.geom)::geography, (s.geom)::geography, true))::numeric, 1))::double precision), s.stop_id, al.alert_message
        Sort Method: top-N heapsort  Memory: 46kB
        Buffers: shared hit=190
        ->  Nested Loop Left Join  (cost=64.00..145.40 rows=2 width=167) (actual time=0.109..2.015 rows=107.00 loops=1)
              Join Filter: ((al.station_id)::text = (es.station_id)::text)
              Rows Removed by Join Filter: 124
              Buffers: shared hit=190
              ->  Nested Loop Left Join  (cost=64.00..119.23 rows=1 width=182) (actual time=0.090..1.356 rows=50.00 loops=1)
                    Buffers: shared hit=140
                    ->  Nested Loop  (cost=0.40..55.61 rows=1 width=163) (actual time=0.065..0.456 rows=50.00 loops=1)
                          Join Filter: ((it.type_code)::text = (inc.type_code)::text)
                          Rows Removed by Join Filter: 119
                          Buffers: shared hit=90
                          ->  Nested Loop  (cost=0.40..54.48 rows=1 width=162) (actual time=0.056..0.307 rows=50.00 loops=1)
                                Buffers: shared hit=40
                                ->  Index Scan using idx_incidents_active on incidents inc  (cost=0.13..12.87 rows=2 width=93) (actual time=0.005..0.007 rows=3.00 loops=1)
                                      Filter: (NOT is_drill)
                                      Index Searches: 1
                                      Buffers: shared hit=2
                                ->  Index Scan using idx_stops_geog on stops s  (cost=0.27..20.79 rows=1 width=69) (actual time=0.023..0.095 rows=16.67 loops=3)
                                      Index Cond: ((geom)::geography && _st_expand((inc.geom)::geography, '600'::double precision))
                                      Filter: st_dwithin((inc.geom)::geography, (geom)::geography, '600'::double precision, true)
                                      Rows Removed by Filter: 13
                                      Index Searches: 3
                                      Buffers: shared hit=38
                          ->  Seq Scan on incident_types it  (cost=0.00..1.06 rows=6 width=22) (actual time=0.001..0.001 rows=3.38 loops=50)
                                Buffers: shared hit=50
                    ->  Limit  (cost=63.60..63.60 rows=1 width=27) (actual time=0.018..0.018 rows=1.00 loops=50)
                          Buffers: shared hit=50
                          ->  Sort  (cost=63.60..63.61 rows=5 width=27) (actual time=0.017..0.017 rows=1.00 loops=50)
                                Sort Key: (st_distance((inc.geom)::geography, (es.geom)::geography, true))
                                Sort Method: top-N heapsort  Memory: 25kB
                                Buffers: shared hit=50
                                ->  Seq Scan on stations es  (cost=0.00..63.57 rows=5 width=27) (actual time=0.003..0.014 rows=5.00 loops=50)
                                      Buffers: shared hit=50
              ->  Seq Scan on alerts al  (cost=0.00..1.09 rows=4 width=62) (actual time=0.001..0.002 rows=4.00 loops=50)
                    Filter: ((status)::text = 'ACTIVE'::text)
                    Rows Removed by Filter: 3
                    Buffers: shared hit=50
Planning:
  Buffers: shared hit=4
Planning Time: 0.930 ms
Execution Time: 2.240 ms
```

## X-4 · Place context cards (materialized view)

Parameters: `{"category": ""}`

```
Sort  (cost=1.59..1.63 rows=15 width=159) (actual time=0.094..0.095 rows=15.00 loops=1)
  Sort Key: name_ko
  Sort Method: quicksort  Memory: 27kB
  Buffers: shared hit=1
  ->  Seq Scan on mv_place_context  (cost=0.00..1.30 rows=15 width=159) (actual time=0.020..0.051 rows=15.00 loops=1)
        Buffers: shared hit=1
Planning Time: 0.081 ms
Execution Time: 0.109 ms
```

## X-5 · Facility response statistics (materialized view)

Parameters: `{"from_date": "2026-07-01", "to_date": "2026-10-02", "include_drills": 0}`

```
Sort  (cost=2.55..2.56 rows=5 width=73) (actual time=0.062..0.062 rows=5.00 loops=1)
  Sort Key: ((round((sum((avg_response_s * incidents)) / sum(incidents)), 0))::integer)
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=1
  ->  HashAggregate  (cost=2.30..2.49 rows=5 width=73) (actual time=0.051..0.057 rows=5.00 loops=1)
        Group Key: facility_id, facility_name_ko, facility_type
        Batches: 1  Memory Usage: 32kB
        Buffers: shared hit=1
        ->  Seq Scan on mv_facility_response_stats  (cost=0.00..1.90 rows=20 width=77) (actual time=0.011..0.020 rows=20.00 loops=1)
              Filter: ((NOT is_drill) AND (month <= '2026-10-02'::date) AND (month >= (date_trunc('month'::text, ('2026-07-01'::date)::timestamp with time zone))::date))
              Rows Removed by Filter: 20
              Buffers: shared hit=1
Planning Time: 0.139 ms
Execution Time: 0.089 ms
```

## X-6 · Materialized view freshness

Parameters: `{}`

```
Subquery Scan on v_materialized_view_freshness  (cost=1.05..1.18 rows=3 width=50) (actual time=0.019..0.024 rows=3.00 loops=1)
  Buffers: shared hit=1
  ->  Result  (cost=1.05..1.14 rows=3 width=49) (actual time=0.017..0.020 rows=3.00 loops=1)
        Buffers: shared hit=1
        ->  Unique  (cost=1.05..1.07 rows=3 width=45) (actual time=0.014..0.015 rows=3.00 loops=1)
              Buffers: shared hit=1
              ->  Sort  (cost=1.05..1.06 rows=3 width=45) (actual time=0.013..0.014 rows=3.00 loops=1)
                    Sort Key: l.view_name, l.refreshed_at DESC
                    Sort Method: quicksort  Memory: 25kB
                    Buffers: shared hit=1
                    ->  Seq Scan on refresh_log l  (cost=0.00..1.03 rows=3 width=45) (actual time=0.006..0.007 rows=3.00 loops=1)
                          Buffers: shared hit=1
Planning Time: 0.067 ms
Execution Time: 0.036 ms
```

## Comparison · Stops within 1000 m (starter notebook, module 19)

Planner's choice:

```
Bitmap Heap Scan on stops  (cost=9.15..1495.63 rows=1 width=13) (actual time=0.040..0.230 rows=65.00 loops=1)
  Filter: st_dwithin((geom)::geography, '0101000020E6100000D34D621058A95F408B6CE7FBA9B14240'::geography, '1000'::double precision, true)
  Rows Removed by Filter: 50
  Heap Blocks: exact=7
  Buffers: shared hit=10
  ->  Bitmap Index Scan on idx_stops_geog  (cost=0.00..9.15 rows=118 width=0) (actual time=0.029..0.029 rows=115.00 loops=1)
        Index Cond: ((geom)::geography && _st_expand('0101000020E6100000D34D621058A95F408B6CE7FBA9B14240'::geography, '1000'::double precision))
        Index Searches: 1
        Buffers: shared hit=3
Planning Time: 0.097 ms
Execution Time: 0.245 ms
```

Indexes disabled (`enable_indexscan/bitmapscan/indexonlyscan = off`):

```
Seq Scan on stops  (cost=0.00..6428.91 rows=1 width=13) (actual time=0.011..1.021 rows=65.00 loops=1)
  Filter: st_dwithin((geom)::geography, '0101000020E6100000D34D621058A95F408B6CE7FBA9B14240'::geography, '1000'::double precision, true)
  Rows Removed by Filter: 448
  Buffers: shared hit=10
Planning Time: 0.109 ms
Execution Time: 1.033 ms
```

Sequential scan disabled (shows the index plan is available):

```
Bitmap Heap Scan on stops  (cost=9.15..1495.63 rows=1 width=13) (actual time=0.102..0.375 rows=65.00 loops=1)
  Filter: st_dwithin((geom)::geography, '0101000020E6100000D34D621058A95F408B6CE7FBA9B14240'::geography, '1000'::double precision, true)
  Rows Removed by Filter: 50
  Heap Blocks: exact=7
  Buffers: shared hit=10
  ->  Bitmap Index Scan on idx_stops_geog  (cost=0.00..9.15 rows=118 width=0) (actual time=0.078..0.078 rows=115.00 loops=1)
        Index Cond: ((geom)::geography && _st_expand('0101000020E6100000D34D621058A95F408B6CE7FBA9B14240'::geography, '1000'::double precision))
        Index Searches: 1
        Buffers: shared hit=3
Planning Time: 0.211 ms
Execution Time: 0.403 ms
```

## Comparison · GPS points within 150 m of a point

Planner's choice:

```
Aggregate  (cost=25757.81..25757.82 rows=1 width=8) (actual time=3.721..3.721 rows=1.00 loops=1)
  Buffers: shared hit=360
  ->  Bitmap Heap Scan on vehicle_observations  (cost=83.55..25757.80 rows=2 width=0) (actual time=0.346..3.632 rows=1524.00 loops=1)
        Filter: st_dwithin((geom)::geography, '0101000020E610000077BE9F1A2FA95F408B6CE7FBA9B14240'::geography, '150'::double precision, true)
        Rows Removed by Filter: 566
        Heap Blocks: exact=337
        Buffers: shared hit=360
        ->  Bitmap Index Scan on idx_obs_geog  (cost=0.00..83.55 rows=2020 width=0) (actual time=0.295..0.295 rows=2094.00 loops=1)
              Index Cond: ((geom)::geography && _st_expand('0101000020E610000077BE9F1A2FA95F408B6CE7FBA9B14240'::geography, '150'::double precision))
              Index Searches: 1
              Buffers: shared hit=23
Planning Time: 0.787 ms
Execution Time: 3.748 ms
```

Indexes disabled (`enable_indexscan/bitmapscan/indexonlyscan = off`):

```
Aggregate  (cost=253626.98..253626.99 rows=1 width=8) (actual time=39.620..39.620 rows=1.00 loops=1)
  Buffers: shared hit=399
  ->  Seq Scan on vehicle_observations  (cost=0.00..253626.97 rows=2 width=0) (actual time=0.129..39.524 rows=1524.00 loops=1)
        Filter: st_dwithin((geom)::geography, '0101000020E610000077BE9F1A2FA95F408B6CE7FBA9B14240'::geography, '150'::double precision, true)
        Rows Removed by Filter: 18714
        Buffers: shared hit=399
Planning Time: 0.780 ms
Execution Time: 39.643 ms
```

Sequential scan disabled (shows the index plan is available):

```
Aggregate  (cost=25757.81..25757.82 rows=1 width=8) (actual time=4.530..4.530 rows=1.00 loops=1)
  Buffers: shared hit=360
  ->  Bitmap Heap Scan on vehicle_observations  (cost=83.55..25757.80 rows=2 width=0) (actual time=0.353..4.407 rows=1524.00 loops=1)
        Filter: st_dwithin((geom)::geography, '0101000020E610000077BE9F1A2FA95F408B6CE7FBA9B14240'::geography, '150'::double precision, true)
        Rows Removed by Filter: 566
        Heap Blocks: exact=337
        Buffers: shared hit=360
        ->  Bitmap Index Scan on idx_obs_geog  (cost=0.00..83.55 rows=2020 width=0) (actual time=0.299..0.299 rows=2094.00 loops=1)
              Index Cond: ((geom)::geography && _st_expand('0101000020E610000077BE9F1A2FA95F408B6CE7FBA9B14240'::geography, '150'::double precision))
              Index Searches: 1
              Buffers: shared hit=23
Planning Time: 0.786 ms
Execution Time: 4.561 ms
```

## Comparison · Each vehicle's last position at T (point-in-time replay)

Planner's choice:

```
Nested Loop  (cost=0.29..8.44 rows=16 width=18) (actual time=0.039..0.236 rows=16.00 loops=1)
  Buffers: shared hit=36
  ->  Seq Scan on vehicles v  (cost=0.00..1.16 rows=16 width=10) (actual time=0.009..0.011 rows=16.00 loops=1)
        Buffers: shared hit=1
  ->  Limit  (cost=0.29..0.45 rows=1 width=8) (actual time=0.013..0.013 rows=1.00 loops=16)
        Buffers: shared hit=35
        ->  Index Only Scan Backward using uq_obs_vehicle_time on vehicle_observations x  (cost=0.29..23.17 rows=145 width=8) (actual time=0.013..0.013 rows=1.00 loops=16)
              Index Cond: ((vehicle_id = (v.vehicle_id)::text) AND (observed_at <= '2026-10-02 08:20:00+09'::timestamp with time zone))
              Heap Fetches: 2
              Index Searches: 16
              Buffers: shared hit=35
Planning Time: 0.156 ms
Execution Time: 0.254 ms
```

Indexes disabled (`enable_indexscan/bitmapscan/indexonlyscan = off`):

```
Nested Loop  (cost=703.29..11254.08 rows=16 width=18) (actual time=2.807..42.971 rows=16.00 loops=1)
  Buffers: shared hit=6385
  ->  Seq Scan on vehicles v  (cost=0.00..1.16 rows=16 width=10) (actual time=0.009..0.015 rows=16.00 loops=1)
        Buffers: shared hit=1
  ->  Limit  (cost=703.29..703.30 rows=1 width=8) (actual time=2.682..2.682 rows=1.00 loops=16)
        Buffers: shared hit=6384
        ->  Sort  (cost=703.29..703.66 rows=145 width=8) (actual time=2.679..2.679 rows=1.00 loops=16)
              Sort Key: x.observed_at DESC
              Sort Method: top-N heapsort  Memory: 25kB
              Buffers: shared hit=6384
              ->  Seq Scan on vehicle_observations x  (cost=0.00..702.57 rows=145 width=8) (actual time=0.053..2.656 rows=145.94 loops=16)
                    Filter: ((observed_at <= '2026-10-02 08:20:00+09'::timestamp with time zone) AND ((vehicle_id)::text = (v.vehicle_id)::text))
                    Rows Removed by Filter: 20092
                    Buffers: shared hit=6384
Planning Time: 0.160 ms
Execution Time: 42.996 ms
```

Sequential scan disabled (shows the index plan is available):

```
Nested Loop  (cost=0.42..19.66 rows=16 width=18) (actual time=0.041..0.187 rows=16.00 loops=1)
  Buffers: shared hit=37
  ->  Index Only Scan using vehicles_pkey on vehicles v  (cost=0.14..12.38 rows=16 width=10) (actual time=0.012..0.017 rows=16.00 loops=1)
        Heap Fetches: 16
        Index Searches: 1
        Buffers: shared hit=2
  ->  Limit  (cost=0.29..0.45 rows=1 width=8) (actual time=0.010..0.010 rows=1.00 loops=16)
        Buffers: shared hit=35
        ->  Index Only Scan Backward using uq_obs_vehicle_time on vehicle_observations x  (cost=0.29..23.17 rows=145 width=8) (actual time=0.010..0.010 rows=1.00 loops=16)
              Index Cond: ((vehicle_id = (v.vehicle_id)::text) AND (observed_at <= '2026-10-02 08:20:00+09'::timestamp with time zone))
              Heap Fetches: 2
              Index Searches: 16
              Buffers: shared hit=35
Planning Time: 0.161 ms
Execution Time: 0.202 ms
```

## Comparison · Audit history of one entity

Planner's choice:

```
Sort  (cost=9.91..9.92 rows=1 width=216) (actual time=0.010..0.010 rows=0.00 loops=1)
  Sort Key: audit_id
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=1
  ->  Index Scan using idx_audit_entity on event_audit_log  (cost=0.13..9.90 rows=1 width=216) (actual time=0.008..0.008 rows=0.00 loops=1)
        Index Cond: (((domain_schema)::text = 'safety'::text) AND ((table_name)::text = 'incidents'::text) AND ((entity_id)::text = 'INC-20261002-0003'::text))
        Index Searches: 1
        Buffers: shared hit=1
Planning Time: 0.064 ms
Execution Time: 0.022 ms
```

Indexes disabled (`enable_indexscan/bitmapscan/indexonlyscan = off`):

```
Sort  (cost=23.10..23.10 rows=1 width=216) (actual time=0.016..0.016 rows=0.00 loops=1)
  Sort Key: audit_id
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=23
  ->  Seq Scan on event_audit_log  (cost=0.00..23.09 rows=1 width=216) (actual time=0.013..0.013 rows=0.00 loops=1)
        Filter: (((domain_schema)::text = 'safety'::text) AND ((table_name)::text = 'incidents'::text) AND ((entity_id)::text = 'INC-20261002-0003'::text))
        Rows Removed by Filter: 5
        Buffers: shared hit=23
Planning Time: 0.060 ms
Execution Time: 0.026 ms
```

Sequential scan disabled (shows the index plan is available):

```
Sort  (cost=9.91..9.92 rows=1 width=216) (actual time=0.018..0.019 rows=0.00 loops=1)
  Sort Key: audit_id
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=1
  ->  Index Scan using idx_audit_entity on event_audit_log  (cost=0.13..9.90 rows=1 width=216) (actual time=0.016..0.016 rows=0.00 loops=1)
        Index Cond: (((domain_schema)::text = 'safety'::text) AND ((table_name)::text = 'incidents'::text) AND ((entity_id)::text = 'INC-20261002-0003'::text))
        Index Searches: 1
        Buffers: shared hit=1
Planning Time: 0.060 ms
Execution Time: 0.029 ms
```

## Comparison · Status history of one incident

Planner's choice:

```
Sort  (cost=23.28..23.29 rows=5 width=114) (actual time=0.025..0.025 rows=3.00 loops=1)
  Sort Key: changed_at
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=5
  ->  Bitmap Heap Scan on incident_status_history  (cost=4.45..23.22 rows=5 width=114) (actual time=0.020..0.021 rows=3.00 loops=1)
        Recheck Cond: ((incident_id)::text = 'INC-20261002-0003'::text)
        Heap Blocks: exact=2
        Buffers: shared hit=5
        ->  Bitmap Index Scan on idx_history_incident_time  (cost=0.00..4.45 rows=5 width=0) (actual time=0.012..0.012 rows=4.00 loops=1)
              Index Cond: ((incident_id)::text = 'INC-20261002-0003'::text)
              Index Searches: 1
              Buffers: shared hit=3
Planning Time: 0.078 ms
Execution Time: 0.038 ms
```

Indexes disabled (`enable_indexscan/bitmapscan/indexonlyscan = off`):

```
Sort  (cost=1286.88..1286.90 rows=5 width=114) (actual time=5.714..5.715 rows=3.00 loops=1)
  Sort Key: changed_at
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=669
  ->  Seq Scan on incident_status_history  (cost=0.00..1286.83 rows=5 width=114) (actual time=5.702..5.704 rows=3.00 loops=1)
        Filter: ((incident_id)::text = 'INC-20261002-0003'::text)
        Rows Removed by Filter: 49423
        Buffers: shared hit=669
Planning Time: 0.124 ms
Execution Time: 5.739 ms
```

Sequential scan disabled (shows the index plan is available):

```
Sort  (cost=23.28..23.29 rows=5 width=114) (actual time=0.050..0.051 rows=3.00 loops=1)
  Sort Key: changed_at
  Sort Method: quicksort  Memory: 25kB
  Buffers: shared hit=5
  ->  Bitmap Heap Scan on incident_status_history  (cost=4.45..23.22 rows=5 width=114) (actual time=0.042..0.044 rows=3.00 loops=1)
        Recheck Cond: ((incident_id)::text = 'INC-20261002-0003'::text)
        Heap Blocks: exact=2
        Buffers: shared hit=5
        ->  Bitmap Index Scan on idx_history_incident_time  (cost=0.00..4.45 rows=5 width=0) (actual time=0.028..0.028 rows=4.00 loops=1)
              Index Cond: ((incident_id)::text = 'INC-20261002-0003'::text)
              Index Searches: 1
              Buffers: shared hit=3
Planning Time: 0.128 ms
Execution Time: 0.073 ms
```

## Scale benchmark · 1,000,000 synthetic GPS rows

Table and indexes built in 13.0 s. Same column types and index definitions as mobility.vehicle_observations.

### Radius search: GPS fixes within 150 m of a point

With indexes:

```
Finalize Aggregate  (cost=19898.47..19898.48 rows=1 width=8) (actual time=37.332..41.775 rows=1.00 loops=1)
  Buffers: shared hit=1775
  ->  Gather  (cost=19898.25..19898.46 rows=2 width=8) (actual time=6.955..41.766 rows=2.00 loops=1)
        Workers Planned: 1
        Workers Launched: 1
        Buffers: shared hit=1775
        ->  Partial Aggregate  (cost=18898.25..18898.26 rows=1 width=8) (actual time=3.410..3.411 rows=1.00 loops=2)
              Buffers: shared hit=1775
              ->  Parallel Bitmap Heap Scan on vehicle_observations  (cost=82.81..18898.11 rows=59 width=0) (actual time=0.546..3.351 rows=562.50 loops=2)
                    Filter: st_dwithin((geom)::geography, '0101000020E610000077BE9F1A2FA95F408B6CE7FBA9B14240'::geography, '150'::double precision, true)
                    Rows Removed by Filter: 383
                    Heap Blocks: exact=1716
                    Buffers: shared hit=1775
                    ->  Bitmap Index Scan on vehicle_observations_geom_idx  (cost=0.00..82.79 rows=1917 width=0) (actual time=0.737..0.737 rows=1891.00 loops=1)
                          Index Cond: ((geom)::geography && _st_expand('0101000020E610000077BE9F1A2FA95F408B6CE7FBA9B14240'::geography, '150'::double precision))
                          Index Searches: 1
                          Buffers: shared hit=39
Planning Time: 0.676 ms
Execution Time: 41.831 ms
```

Indexes disabled:

```
Finalize Aggregate  (cost=5223887.99..5223888.00 rows=1 width=8) (actual time=862.324..873.405 rows=1.00 loops=1)
  Buffers: shared hit=9942 read=1622
  ->  Gather  (cost=5223887.77..5223887.98 rows=2 width=8) (actual time=862.203..873.397 rows=3.00 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=9942 read=1622
        ->  Partial Aggregate  (cost=5222887.77..5222887.78 rows=1 width=8) (actual time=808.125..808.125 rows=1.00 loops=3)
              Buffers: shared hit=9942 read=1622
              ->  Parallel Seq Scan on vehicle_observations  (cost=0.00..5222887.67 rows=42 width=0) (actual time=14.846..807.967 rows=375.00 loops=3)
                    Filter: st_dwithin((geom)::geography, '0101000020E610000077BE9F1A2FA95F408B6CE7FBA9B14240'::geography, '150'::double precision, true)
                    Rows Removed by Filter: 332958
                    Buffers: shared hit=9942 read=1622
Planning Time: 0.836 ms
Execution Time: 873.447 ms
```

### Point-in-time replay: last fix of 50 vehicles at T

With indexes:

```
Nested Loop  (cost=0.43..216.80 rows=50 width=12) (actual time=0.035..0.438 rows=50.00 loops=1)
  Buffers: shared hit=200
  ->  Function Scan on generate_series v  (cost=0.00..0.50 rows=50 width=4) (actual time=0.008..0.010 rows=50.00 loops=1)
  ->  Limit  (cost=0.42..4.32 rows=1 width=8) (actual time=0.008..0.008 rows=1.00 loops=50)
        Buffers: shared hit=200
        ->  Index Only Scan Backward using vehicle_observations_vehicle_id_observed_at_idx on vehicle_observations x  (cost=0.42..2774.68 rows=713 width=8) (actual time=0.008..0.008 rows=1.00 loops=50)
              Index Cond: ((vehicle_id = v.v) AND (observed_at <= '2026-10-02 12:00:00+09'::timestamp with time zone))
              Heap Fetches: 50
              Index Searches: 50
              Buffers: shared hit=200
Planning Time: 0.137 ms
Execution Time: 0.459 ms
```

Indexes disabled:

```
Nested Loop  (cost=24349.57..1217479.38 rows=50 width=12) (actual time=90.393..4475.312 rows=50.00 loops=1)
  Buffers: shared hit=414400 read=52900
  ->  Function Scan on generate_series v  (cost=0.00..0.50 rows=50 width=4) (actual time=0.006..0.070 rows=50.00 loops=1)
  ->  Limit  (cost=24349.56..24349.57 rows=1 width=8) (actual time=89.500..89.501 rows=1.00 loops=50)
        Buffers: shared hit=414400 read=52900
        ->  Sort  (cost=24349.56..24351.35 rows=713 width=8) (actual time=89.494..89.494 rows=1.00 loops=50)
              Sort Key: x.observed_at DESC
              Sort Method: top-N heapsort  Memory: 25kB
              Buffers: shared hit=414400 read=52900
              ->  Seq Scan on vehicle_observations x  (cost=0.00..24346.00 rows=713 width=8) (actual time=0.026..89.385 rows=721.00 loops=50)
                    Filter: ((observed_at <= '2026-10-02 12:00:00+09'::timestamp with time zone) AND (vehicle_id = v.v))
                    Rows Removed by Filter: 999279
                    Buffers: shared hit=414400 read=52900
Planning Time: 0.137 ms
Execution Time: 4475.367 ms
```

### Time window: fixes in a 5-minute interval

With indexes:

```
Aggregate  (cost=179.54..179.55 rows=1 width=8) (actual time=1.181..1.181 rows=1.00 loops=1)
  Buffers: shared hit=55
  ->  Index Only Scan using vehicle_observations_observed_at_idx on vehicle_observations  (cost=0.42..167.31 rows=4894 width=0) (actual time=0.020..0.928 rows=5000.00 loops=1)
        Index Cond: ((observed_at >= '2026-10-02 12:00:00+09'::timestamp with time zone) AND (observed_at < '2026-10-02 12:05:00+09'::timestamp with time zone))
        Heap Fetches: 5000
        Index Searches: 1
        Buffers: shared hit=55
Planning Time: 0.072 ms
Execution Time: 1.195 ms
```

Indexes disabled:

```
Finalize Aggregate  (cost=16601.31..16601.32 rows=1 width=8) (actual time=133.553..137.836 rows=1.00 loops=1)
  Buffers: shared hit=8664 read=682
  ->  Gather  (cost=16601.10..16601.31 rows=2 width=8) (actual time=55.951..137.824 rows=3.00 loops=1)
        Workers Planned: 2
        Workers Launched: 2
        Buffers: shared hit=8664 read=682
        ->  Partial Aggregate  (cost=15601.10..15601.11 rows=1 width=8) (actual time=28.823..28.824 rows=1.00 loops=3)
              Buffers: shared hit=8664 read=682
              ->  Parallel Seq Scan on vehicle_observations  (cost=0.00..15596.00 rows=2039 width=0) (actual time=9.592..28.747 rows=1666.67 loops=3)
                    Filter: ((observed_at >= '2026-10-02 12:00:00+09'::timestamp with time zone) AND (observed_at < '2026-10-02 12:05:00+09'::timestamp with time zone))
                    Rows Removed by Filter: 331667
                    Buffers: shared hit=8664 read=682
Planning Time: 0.155 ms
Execution Time: 137.870 ms
```

