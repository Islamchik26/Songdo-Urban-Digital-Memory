"""Copy the numbers of the latest evidence run (outputs/evidence/) into the handbook (main.tex).

    python scripts/evidence.py        # produces new numbers (timings change on every run)
    python scripts/sync_handbook.py   # writes them into main.tex; prints what changed

Only measured values are touched: build and test times, test count, API cases, backup and restore
times, the index benchmarks and the latency summary. Each value sits in a sentence or table row the
script finds by its wording, so a reworded handbook line is reported instead of silently skipped.
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EV = ROOT / "outputs" / "evidence"
TEX = ROOT / "main.tex"


def rows(name: str) -> list[dict[str, str]]:
    with open(EV / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def x(v: float) -> str:
    """Speed-up factor as the handbook prints it: 2.2, 23, 5,656."""
    return f"{v:.1f}" if v < 10 else f"{v:,.0f}"


def ms(v: float, digits: int = 2) -> str:
    return f"{v:,.{digits}f}"


def main() -> None:
    readme = (EV / "README.md").read_text(encoding="utf-8")
    tex = TEX.read_text(encoding="utf-8")

    boot = float(re.search(r"Boot / DDL / seed \| .*? \| PASS \| 9 files, exit 0, ([\d.]+) s", readme).group(1))
    tests, test_s = re.search(r"`(\d+)` tests on the clean database: OK \(([\d.]+) s\)", readme).groups()
    api_ok, api_all = re.search(r"\| API \| .*? \| PASS \| (\d+)/(\d+) cases", readme).groups()
    dump_mb, dump_s, restore_s = re.search(
        r"`pg_dump -Fc` \(([\d.]+) MB, ([\d.]+) s\) then `pg_restore --exit-on-error` into `songdo_evidence_restore` \(([\d.]+) s\)",
        readme).groups()
    paths = re.search(r"\| OpenAPI document \| .*? \| (\d+) paths documented", readme).group(1)

    scale = {r["query"].split(":")[0]: r for r in rows("scale_benchmark.csv")}
    fixture = rows("index_comparison.csv")
    perf = {r["task"]: float(r["median_ms"]) for r in rows("performance.csv")}
    latency = [float(r["p50_ms"]) for r in rows("api_latency.csv")]
    nearby = next(float(r["p50_ms"]) for r in rows("api_latency.csv") if "stops/nearby" in r["endpoint"])
    slowest = max(perf, key=perf.get)

    def bench(r: dict[str, str], fixture: bool = False) -> str:
        """Table cells: ms with the index, ms without (2 decimals on the small fixture tables), speed-up."""
        with_ix = float(r.get("median_ms_with_index") or r["median_ms_planner_choice"])
        without = float(r.get("median_ms_without_index") or r["median_ms_indexes_disabled"])
        return rf"& {ms(with_ix)} & {ms(without, 2 if fixture else 1)} & {x(without / with_ix)}$\times$ \\"

    fx = {r["query"]: r for r in fixture}
    def fixture_row(start: str) -> dict[str, str]:
        return next(r for q, r in fx.items() if q.startswith(start))

    # (pattern with one group per value to replace, new text) - the surrounding wording anchors each value
    rules: list[tuple[str, str]] = [
        (r"(9 files with \\cmd\{psql -v ON_ERROR_STOP=1\}, all exit 0, )[\d.]+( s)", rf"\g<1>{boot:.1f}\g<2>"),
        (r"(\d+) of (\d+) contract cases \(2xx", rf"{api_ok} of {api_all} contract cases (2xx"),
        (r"\d+ tests, OK, [\d.]+ s", rf"{tests} tests, OK, {float(test_s):.1f} s"),
        (r"(the platform: )\d+( automated tests)", rf"\g<1>{tests}\g<2>"),
        (r"(tests/test_platform.py +)\d+( tests)", rf"\g<1>{tests}\g<2>"),
        (r"runs \d+ tests in about \d+ seconds", rf"runs {tests} tests in about {round(float(test_s))} seconds"),
        (r"with \d+ documented paths", rf"with {paths} documented paths"),
        (r"\(OpenAPI, \d+ paths\)", rf"(OpenAPI, {paths} paths)"),
        (r"the dump took [\d.]+ s \([\d.]+ MB\) and the restore [\d.]+ s\.",
         rf"the dump took {float(dump_s):.1f} s ({dump_mb} MB) and the restore {float(restore_s):.1f} s."),
        (r"The evidence run measured [\d.]+ s for all migrations and the seed, plus\n[\d.]+ s for the \d+ tests: about \d+ s in total\.",
         rf"The evidence run measured {boot:.1f} s for all migrations and the seed, plus" "\n"
         rf"{float(test_s):.1f} s for the {tests} tests: about {round(boot + float(test_s))} s in total."),
        (r"(Radius search: fixes within 150 m of a point \(GiST on \\code\{geography\}\) )&[^\\]*\$\\times\$ \\\\",
         r"\g<1>" + bench(scale["Radius search"]).replace("\\", "\\\\")),
        (r"(Point-in-time replay: last fix of 50 vehicles at \$T\$ \(B-tree \\cmd\{\(vehicle_id, observed_at\)\}\) )&[^\\]*\$\\times\$ \\\\",
         r"\g<1>" + bench(scale["Point-in-time replay"]).replace("\\", "\\\\")),
        (r"(Time window: fixes in a 5-minute interval \(B-tree \\code\{observed_at\}\) )&[^\\]*\$\\times\$ \\\\",
         r"\g<1>" + bench(scale["Time window"]).replace("\\", "\\\\")),
        (r"(Stops within 1,000 m \(513 stops, \\code\{idx_stops_geog\}\) )&[^\\]*\$\\times\$ \\\\",
         r"\g<1>" + bench(fixture_row("Stops within"), True).replace("\\", "\\\\")),
        (r"(GPS points within 150 m \(20,238 rows, \\code\{idx_obs_geog\}\) )&[^\\]*\$\\times\$ \\\\",
         r"\g<1>" + bench(fixture_row("GPS points"), True).replace("\\", "\\\\")),
        (r"(Last position of each vehicle at \$T\$ )&[^\\]*\$\\times\$ \\\\",
         r"\g<1>" + bench(fixture_row("Each vehicle"), True).replace("\\", "\\\\")),
        (r"(Status history of one incident \(49,421 rows\) )&[^\\]*\$\\times\$ \\\\",
         r"\g<1>" + bench(fixture_row("Status history"), True).replace("\\", "\\\\")),
        (r"(Audit history of one entity \([^)]*\) )&[^\\]*\$\\times\$ \\\\",
         r"\g<1>" + bench(fixture_row("Audit history"), True).replace("\\", "\\\\")),
        (r"All 36 tasks have a median below \d+ ms on the fixture data\. The slowest is [A-Z0-9-]+\n\((.+?), \d+ ms\)\. In total, (\d+) tasks run in under 20 ms and (\d+) in\nunder 10 ms\.",
         rf"All 36 tasks have a median below {int(perf[slowest] // 5 * 5 + 5)} ms on the fixture data. The slowest is {slowest}" "\n"
         rf"(\g<1>, {perf[slowest]:.0f} ms). In total, {sum(v < 20 for v in perf.values())} tasks run in under 20 ms and "
         rf"{sum(v < 10 for v in perf.values())} in" "\n" "under 10 ms."),
        (r"between [\d.]+ and [\d.]+ ms\.", rf"between {min(latency):.0f} and {max(latency):.0f} ms."),
        (r"nearby stops\) take [\d.]+--[\d.]+ ms, below the 20 ms target\.",
         rf"nearby stops) take {min(perf['CORE-1'], perf['DB1-5']):.1f}--{max(perf['CORE-1'], perf['DB1-5'], nearby):.1f} ms, "
         r"below the 20 ms target."),
    ]
    missing = []
    for pattern, new in rules:
        tex, n = re.subn(pattern, new, tex)
        if n == 0:
            missing.append(pattern[:70])
    TEX.write_text(tex, encoding="utf-8", newline="\n")
    print(f"main.tex updated from the evidence run ({tests} tests, {api_ok}/{api_all} API cases, build {boot:.1f} s)")
    if missing:
        print("not found (check the wording in main.tex):", *missing, sep="\n  ")
        sys.exit(1)


if __name__ == "__main__":
    main()
