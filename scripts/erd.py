"""Generate the ER diagrams of the database from its live catalog.

    python scripts/erd.py              # writes docs/erd/ (Markdown + Mermaid) and renders PDFs
    python scripts/erd.py --no-render  # Mermaid and Markdown only

Everything is read from pg_catalog, so the diagrams always match the schema that is actually
deployed: tables, columns, types, primary keys, foreign keys (with cardinality), unique and
exclusion constraints, generated columns, and which tables each cross-domain view reads.

Output (docs/erd/):
  README.md            all diagrams (GitHub renders the Mermaid blocks) + keys and constraints
  *.mmd                one Mermaid file per diagram
  *.pdf                vector renderings for the LaTeX handbook (needs Chrome/Edge + internet
                       for mermaid.js from the jsDelivr CDN)
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from api.config import settings  # noqa: E402

OUT = ROOT / "docs" / "erd"
MERMAID_JS = "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js"

DOMAINS = [
    ("core", "Core contracts", "Source registry, canonical places, study area and the ingestion pipeline "
     "(batches, staging, quarantine, append-only audit ledger)."),
    ("mobility", "DB1 Mobility", "Stops, routes with ordered stop patterns, vehicles, trips, stop events and GPS history."),
    ("building", "DB2 Smart Building", "Buildings, the space tree, devices with valid-time locations, telemetry and maintenance."),
    ("tourism", "DB3 Tourism", "Attractions, weekly opening hours, events, and visitor counts at two grains."),
    ("environment", "DB4 Environment", "Monitoring stations, pollutant types, measurements, hourly rollup, alert rules and episodes."),
    ("safety", "DB5 Safety & Incident", "Facilities, incident types and severities, incidents, their status history and drills."),
]
SCHEMAS = [d[0] for d in DOMAINS] + ["cross_domain"]


@dataclass
class Column:
    name: str
    type: str
    not_null: bool
    generated: bool


@dataclass
class Table:
    schema: str
    name: str
    comment: str
    columns: list[Column] = field(default_factory=list)
    pk: list[str] = field(default_factory=list)
    uniques: list[tuple[str, list[str]]] = field(default_factory=list)
    exclusions: list[tuple[str, str]] = field(default_factory=list)
    checks: int = 0
    rows: int = 0

    @property
    def fq(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def node(self) -> str:
        return f"{self.schema}__{self.name}"


@dataclass
class ForeignKey:
    name: str
    child: str
    child_cols: list[str]
    parent: str
    parent_cols: list[str]


# --------------------------------------------------------------------------- catalog
def load_catalog(conn: psycopg.Connection) -> tuple[dict[str, Table], list[ForeignKey], dict[str, list[str]], list]:
    tables: dict[str, Table] = {}
    for schema, name, comment, rows in conn.execute(
        """SELECT n.nspname, c.relname, COALESCE(obj_description(c.oid, 'pg_class'), ''), c.reltuples::bigint
           FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(%s)
           ORDER BY n.nspname, c.relname""", (SCHEMAS,)):
        tables[f"{schema}.{name}"] = Table(schema, name, comment, rows=max(rows, 0))

    for schema, rel, col, typ, not_null, generated in conn.execute(
        """SELECT n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
                  a.attgenerated <> ''
           FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE c.relkind IN ('r', 'p') AND n.nspname = ANY(%s) AND a.attnum > 0 AND NOT a.attisdropped
           ORDER BY n.nspname, c.relname, a.attnum""", (SCHEMAS,)):
        tables[f"{schema}.{rel}"].columns.append(Column(col, typ, not_null, generated))

    fks: list[ForeignKey] = []
    for con, kind, child, cols, parent, pcols, definition in conn.execute(
        """SELECT con.conname, con.contype, con.conrelid::regclass::text,
                  ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i)
                        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i),
                  con.confrelid::regclass::text,
                  ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i)
                        JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i),
                  pg_get_constraintdef(con.oid)
           FROM pg_constraint con JOIN pg_namespace n ON n.oid = con.connamespace
           WHERE n.nspname = ANY(%s) AND con.contype IN ('p', 'u', 'f', 'x', 'c')
           ORDER BY con.conname""", (SCHEMAS,)):
        t = tables.get(child)
        if t is None:
            continue
        if kind == "p":
            t.pk = list(cols)
        elif kind == "u":
            t.uniques.append((con, list(cols)))
        elif kind == "x":
            t.exclusions.append((con, definition))
        elif kind == "c":
            t.checks += 1
        elif kind == "f":
            fks.append(ForeignKey(con, child, list(cols), parent, list(pcols)))

    lineage: dict[str, list[str]] = defaultdict(list)
    for view, kind, source in conn.execute(
        """SELECT DISTINCT v.relname, v.relkind, t.relnamespace::regnamespace::text || '.' || t.relname
           FROM pg_class v
           JOIN pg_rewrite r ON r.ev_class = v.oid
           JOIN pg_depend d ON d.objid = r.oid AND d.classid = 'pg_rewrite'::regclass
           JOIN pg_class t ON t.oid = d.refobjid
           WHERE v.relnamespace = 'cross_domain'::regnamespace AND v.relkind IN ('v', 'm')
             AND t.oid <> v.oid AND t.relkind IN ('r', 'p', 'v', 'm')
           ORDER BY 1, 3"""):
        lineage[f"{view}|{kind}"].append(source)

    triggers = conn.execute(
        """SELECT tgrelid::regclass::text, tgname FROM pg_trigger
           WHERE NOT tgisinternal AND tgrelid::regclass::text LIKE ANY(%s) ORDER BY 1, 2""",
        ([s + ".%" for s in SCHEMAS],)).fetchall()
    return tables, fks, lineage, triggers


# --------------------------------------------------------------------------- Mermaid
def short_type(t: str) -> str:
    t = (t.replace("character varying", "varchar").replace("timestamp with time zone", "timestamptz")
          .replace("timestamp without time zone", "timestamp").replace("time without time zone", "time")
          .replace("double precision", "float8").replace("boolean", "bool").replace("integer", "int"))
    if "(" in t and "," in t:          # numeric(10,4), geometry(Point,4326): Mermaid types cannot contain commas
        t = t.split("(")[0]
    return re.sub(r"[^A-Za-z0-9_()\[\]]", "_", t)


def fk_columns(fks: list[ForeignKey], table: str) -> set[str]:
    return {c for fk in fks if fk.child == table for c in fk.child_cols}


def entity_block(t: Table, fks: list[ForeignKey], full: bool) -> list[str]:
    if not full:
        keys = [c for c in t.columns if c.name in t.pk] or t.columns[:1]
        return [f'    {t.node}["{t.fq}"] {{'] + [f"        {short_type(c.type)} {c.name} PK" for c in keys] + ["    }"]
    fkc = fk_columns(fks, t.fq)
    uqc = {c for _, cols in t.uniques if len(cols) == 1 for c in cols}
    lines = [f'    {t.node}["{t.fq}"] {{']
    for c in t.columns:
        keys = [k for k, hit in (("PK", c.name in t.pk), ("FK", c.name in fkc), ("UK", c.name in uqc)) if hit]
        notes = []
        if c.generated:
            notes.append("generated")
        if not c.not_null and c.name not in t.pk:
            notes.append("nullable")
        line = f"        {short_type(c.type)} {c.name}"
        if keys:
            line += " " + ", ".join(keys)
        if notes:
            line += f' "{", ".join(notes)}"'
        lines.append(line)
    lines.append("    }")
    return lines


def relationship(fk: ForeignKey, tables: dict[str, Table]) -> str:
    child, parent = tables[fk.child], tables[fk.parent]
    cols = {c.name: c for c in child.columns}
    mandatory = all(cols[c].not_null for c in fk.child_cols)
    unique = sorted(fk.child_cols) == sorted(child.pk) or any(sorted(u) == sorted(fk.child_cols) for _, u in child.uniques)
    left = "||" if mandatory else "|o"
    right = "o|" if unique else "o{"
    return f'    {parent.node} {left}--{right} {child.node} : "{", ".join(fk.child_cols)}"'


def domain_diagram(schema: str, tables: dict[str, Table], fks: list[ForeignKey]) -> str:
    own = [t for t in tables.values() if t.schema == schema]
    own_names = {t.fq for t in own}
    rels = [fk for fk in fks if fk.child in own_names or (fk.parent in own_names and fk.child in own_names)]
    external = sorted({fk.parent for fk in rels if fk.parent not in own_names})
    lines = ["erDiagram"]
    for t in own:
        lines += entity_block(t, fks, full=True)
    for name in external:
        lines += entity_block(tables[name], fks, full=False)
    lines += [relationship(fk, tables) for fk in rels]
    return "\n".join(lines) + "\n"


def overview_diagram(tables: dict[str, Table], fks: list[ForeignKey]) -> str:
    """Schema map: one box per schema with its tables; only links that cross schemas are drawn.

    Links inside a schema are in that schema's own diagram. Every domain table references
    core.source_registry, so that is drawn once per schema as a dashed line.
    """
    titles = {s: t.replace("&", "and") for s, t, _ in DOMAINS} | {"cross_domain": "Cross-domain"}
    lines = ["flowchart LR"]
    for schema in SCHEMAS:
        own = [t for t in tables.values() if t.schema == schema]
        if not own:
            continue
        lines += [f'    subgraph sg_{schema}["{titles[schema]} ({schema})"]', "        direction TB"]
        lines += [f'        {t.node}["{t.name}"]' for t in own]
        lines.append("    end")
    seen = set()
    for fk in fks:
        cs, ps = fk.child.split(".")[0], fk.parent.split(".")[0]
        if cs == ps:
            continue
        if fk.parent == "core.source_registry":
            if cs not in seen:
                lines.append(f'    sg_{cs} -. "source_id" .-> {tables[fk.parent].node}')
                seen.add(cs)
            continue
        lines.append(f'    {tables[fk.child].node} -- "{", ".join(fk.child_cols)}" --> {tables[fk.parent].node}')
    return "\n".join(lines) + "\n"


def lineage_diagram(lineage: dict[str, list[str]]) -> str:
    def nid(s: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", s)
    lines = ["flowchart LR"]
    sources = sorted({s for srcs in lineage.values() for s in srcs})
    by_schema: dict[str, list[str]] = defaultdict(list)
    for s in sources:
        by_schema[s.split(".")[0]].append(s)
    for schema, items in by_schema.items():
        if schema == "cross_domain":
            continue                     # drawn inside the views box below
        lines.append(f'    subgraph {nid("sg_" + schema)}["{schema}"]')
        lines += [f'        {nid(s)}["{s.split(".", 1)[1]}"]' for s in items]
        lines.append("    end")
    lines.append('    subgraph sg_views["cross_domain"]')
    lines += [f'        {nid(s)}["{s.split(".", 1)[1]}"]' for s in by_schema.get("cross_domain", [])]
    for key in lineage:
        view, kind = key.split("|")
        shape = f'{nid("v_" + view)}[["{view}"]]' if kind == "m" else f'{nid("v_" + view)}(["{view}"])'
        lines.append(f"        {shape}")
    lines.append("    end")
    for key, srcs in lineage.items():
        view = key.split("|")[0]
        lines += [f"    {nid(s)} --> {nid('v_' + view)}" for s in srcs]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- Markdown
def constraints_table(schema: str, tables: dict[str, Table], fks: list[ForeignKey], triggers: list) -> list[str]:
    out = ["| Table | Purpose | Primary key | Foreign keys | Unique / exclusion | CHECK |",
           "|---|---|---|---|---|---|"]
    for t in (t for t in tables.values() if t.schema == schema):
        fk_txt = "<br>".join(f"`{', '.join(f.child_cols)}` → `{f.parent}`" for f in fks if f.child == t.fq) or "—"
        uq = [f"UNIQUE `({', '.join(c)})`" for _, c in t.uniques]
        ex = [f"EXCLUDE `{name}`" for name, _ in t.exclusions]
        trg = [f"trigger `{name}`" for rel, name in triggers if rel == t.fq]
        out.append(f"| `{t.name}` | {t.comment or '—'} | `{', '.join(t.pk) or '—'}` | {fk_txt} | "
                   f"{'<br>'.join(uq + ex + trg) or '—'} | {t.checks or '—'} |")
    return out


def write_markdown(diagrams: dict[str, str], tables, fks, lineage, triggers) -> None:
    n_tables = len(tables)
    md = [
        "# Entity-relationship diagrams",
        "",
        "Generated by `python scripts/erd.py` from the live PostgreSQL catalog, so they match the deployed schema.",
        f"{n_tables} tables, {len(fks)} foreign keys, "
        f"{sum(len(t.exclusions) for t in tables.values())} exclusion constraints, "
        f"{sum(len(t.uniques) for t in tables.values())} unique constraints, "
        f"{sum(t.checks for t in tables.values())} CHECK constraints.",
        "",
        "**How to read them** (crow's-foot notation):",
        "",
        "| Symbol | Meaning |",
        "|---|---|",
        "| `\\|\\|` on the parent side | every child row must have a parent (FK is NOT NULL) |",
        "| `\\|o` on the parent side | the parent is optional (FK is nullable) |",
        "| `o{` on the child side | a parent has zero or more children |",
        "| `o\\|` on the child side | a parent has at most one child (the FK is also unique) |",
        "| `PK`, `FK`, `UK` | primary key, foreign key, single-column unique key |",
        "| label on a line | the foreign-key column(s) in the child table |",
        "",
        "Tables from another schema appear with their key only. Every domain table also references",
        "`core.source_registry` through `source_id` (provenance).",
        "",
        "## Overview: schema map",
        "",
        "Every table, grouped by schema. Only the links that cross schemas are drawn here (the dashed line",
        "stands for the `source_id` reference of every table in that schema); the links inside a schema are",
        "in that schema's own diagram below.",
        "",
        "```mermaid",
        diagrams["overview"].rstrip(),
        "```",
        "",
    ]
    for schema, title, blurb in DOMAINS:
        md += [f"## {title} (`{schema}`)", "", blurb, "", "```mermaid", diagrams[schema].rstrip(), "```", ""]
        md += constraints_table(schema, tables, fks, triggers) + [""]
    md += ["## Cross-domain views (`cross_domain`)", "",
           "Views (rounded) and materialized views (double border) and the tables they read. They join the domains",
           "through space and time; no data is copied between schemas. `cross_domain.refresh_log` records every refresh.",
           "", "```mermaid", diagrams["cross_domain"].rstrip(), "```", ""]
    (OUT / "README.md").write_text("\n".join(md), encoding="utf-8")


# --------------------------------------------------------------------------- rendering
def find_browser() -> str | None:
    candidates = [shutil.which(n) for n in ("chrome", "google-chrome", "chromium", "msedge")] + [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    return next((c for c in candidates if c and Path(c).exists()), None)


HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;padding:0;background:#fff;overflow:hidden} #d{display:inline-block;margin:0;padding:16px}</style>
<script src="__JS__"></script></head><body><pre class="mermaid" id="d">__SRC__</pre>
<script>
mermaid.initialize({startOnLoad:false, theme:'neutral', securityLevel:'loose', maxTextSize:500000,
  er:{useMaxWidth:false, layoutDirection:'TB', minEntityWidth:80, fontSize:13},
  flowchart:{useMaxWidth:false, htmlLabels:false}, fontFamily:'Helvetica, Arial, sans-serif'});
mermaid.run({querySelector:'#d'}).then(() => {
  const b = document.querySelector('#d').getBoundingClientRect();
  const w = Math.ceil(b.width) + 40, h = Math.ceil(b.height) + 40;   // generous: a page 1 px short spills onto page 2
  const st = document.createElement('style');
  st.textContent = '@page{size:' + w + 'px ' + h + 'px;margin:0}';
  document.head.appendChild(st);
});
</script></body></html>"""


def render(diagrams: dict[str, str]) -> list[str]:
    browser = find_browser()
    if not browser:
        return ["no Chrome/Edge found: PDFs not rendered"]
    notes = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, src in diagrams.items():
            page = Path(tmp) / f"{name}.html"
            esc = src.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            page.write_text(HTML.replace("__JS__", MERMAID_JS).replace("__SRC__", esc), encoding="utf-8")
            pdf = OUT / f"{name}.pdf"
            pdf.unlink(missing_ok=True)
            subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                            f"--user-data-dir={Path(tmp) / ('prof-' + name)}", "--virtual-time-budget=20000",
                            f"--print-to-pdf={pdf}", page.as_uri()],
                           capture_output=True, timeout=180)
            notes.append(f"{pdf.name}: {'ok, %d KB' % (pdf.stat().st_size // 1024) if pdf.exists() else 'FAILED'}")
    return notes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-render", action="store_true", help="skip the PDF rendering")
    args = parser.parse_args()

    with psycopg.connect(settings.conninfo()) as conn:
        tables, fks, lineage, triggers = load_catalog(conn)
    if not tables:
        sys.exit("No project tables found: build the database first (python scripts/setup_db.py)")

    OUT.mkdir(parents=True, exist_ok=True)
    diagrams = {"overview": overview_diagram(tables, fks)}
    for schema, _, _ in DOMAINS:
        diagrams[schema] = domain_diagram(schema, tables, fks)
    diagrams["cross_domain"] = lineage_diagram(lineage)
    for name, src in diagrams.items():
        (OUT / f"{name}.mmd").write_text(src, encoding="utf-8")
    write_markdown(diagrams, tables, fks, lineage, triggers)
    print(f"{len(tables)} tables, {len(fks)} foreign keys, {len(lineage)} cross-domain views -> {OUT}")
    if not args.no_render:
        for note in render(diagrams):
            print("  " + note)


if __name__ == "__main__":
    main()
