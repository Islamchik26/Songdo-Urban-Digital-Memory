# Deploying the live demo

The platform runs as one public web service: the FastAPI API also serves the web console.

| Part | Service | Free tier |
|---|---|---|
| PostgreSQL 16 + PostGIS | [Neon](https://neon.tech) | Doesn't expire. Pauses when idle and wakes in about 1 second |
| API + console | [Render](https://render.com) | Sleeps after 15 min idle. The first request after that takes about 1 minute |

**You don't need Docker on your computer.** Render builds the `Dockerfile` on its own servers.
You load the database once from your laptop with the setup script you already use.

Total time: about 30–45 minutes the first time.

---

## 1. Create the database on Neon (5 min)

1. Sign up at https://neon.tech (signing in with GitHub is fine).
2. **Create project**:
   - Postgres version: **16** (the handbook baseline)
   - Region: **AWS Asia Pacific (Singapore)**
3. On the project dashboard, click **Connect**:
   - Turn **Connection pooling OFF**. The direct connection is more reliable with this app.
   - Copy the connection string. It looks like
     `postgresql://neondb_owner:XXXX@ep-something-123456.ap-southeast-1.aws.neon.tech/neondb?sslmode=require`

Keep this string private. It contains the database password. Never commit it.

## 2. Load the schema and data from your laptop (5 min)

In the project folder, with the virtual environment active (PowerShell):

```powershell
$env:DATABASE_URL = "postgresql://neondb_owner:XXXX@ep-....neon.tech/neondb?sslmode=require"
python scripts/setup_db.py
Remove-Item Env:DATABASE_URL      # back to your local database
```

The first line of the output must name the **neon.tech** host. If it says `127.0.0.1`, the
variable was not set. The script ends with `Done. PostGIS 3.x, ... sources, ... places`.

Setting `DATABASE_URL` only in the terminal, not in `.env`, keeps your local setup and tests
pointing at your local database.

To rebuild the hosted database from zero later: `python scripts/setup_db.py --reset` with the
same variable set.

## 3. Push the code to GitHub

Render deploys from the GitHub repository, so `Dockerfile`, `.dockerignore` and `render.yaml`
must be committed and pushed to `main`.

## 4. Create the web service on Render (10 min, then about 5 min build)

1. Sign up at https://render.com with **GitHub** and allow access to this repository.
2. **New → Blueprint** → choose the repository. Render reads `render.yaml`.
3. When asked for **DATABASE_URL**, paste the Neon string from step 1.
4. **Apply**. The first build installs Node and Python packages and takes about 5 minutes.
   You can watch it under **Logs**.
5. When it shows **Live**, open the URL, for example `https://songdo-urban-memory.onrender.com`.
   - `/` is the console
   - `/docs` is the interactive API documentation
   - `/health` should show `"status": "ok"` with the PostgreSQL and PostGIS versions

After that, every push to `main` redeploys automatically.

### Write endpoints

Render generates a random `INGEST_API_TOKEN`; the public demo does **not** use
`songdo-dev-token`. To see it, open the service → **Environment** → reveal
`INGEST_API_TOKEN`. Use it in the console or in the `X-API-Token` header for anything that
writes data. Share it only with the team.

## 5. On presentation day

- **Open the URL 5 minutes before you present.** The free service sleeps after 15 minutes
  without visitors and needs about a minute to wake up. Keep the tab open afterwards.
- **Keep the local version ready as a backup.** If the room's Wi-Fi fails:
  `cd ui && npm run build && cd ..` then `uvicorn api.main:app --port 8000` and open
  http://127.0.0.1:8000. That needs no internet.
- To reset demo data that someone changed during the presentation, rerun step 2 with `--reset`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Render deploy fails at **health check** | `/health` returns 503 when the database is unreachable. Check `DATABASE_URL` in Render → Environment. It must end with `?sslmode=require` |
| Site loads but every task fails with "relation does not exist" | The database is empty. Run step 2 |
| `setup_db.py` prints `127.0.0.1` | `DATABASE_URL` was not set in that terminal window |
| `permission denied to create extension` | Use the owner role from Neon's **Connect** dialog (`neondb_owner`), not another role |
| Build fails in `npm ci` | `ui/package-lock.json` is out of date. Run `npm install` in `ui/`, commit the lock file, push |
| First page load takes about 1 minute | Normal on the free tier: the service was asleep |

## What is different from the local setup

- The database is configured by one `DATABASE_URL` instead of the `DB_*` values (`api/config.py`).
- The connection pool checks each connection before using it, because Neon closes idle
  connections when it pauses (`api/database.py`).
- The container does **not** build the database on start-up. That is done once in step 2, so
  restarts are fast and never touch the data.
- The **Backup** check on the verification page shows a warning on the hosted copy, because
  `.backup` files stay on your laptop. That is expected.
