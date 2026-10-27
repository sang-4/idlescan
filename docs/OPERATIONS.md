# idlescan Operations

## Deployment

### Docker

```bash
docker build -t idlescan .
docker run --rm -p 127.0.0.1:8787:8787 -v idlescan-data:/data idlescan
```

The container binds to `127.0.0.1` inside the container by default. Map the port to `127.0.0.1` on
the host to avoid exposing to the network. Mount a volume at `/data` for the SQLite database.

### Docker Compose

```bash
docker compose up -d
```

Compose binds to `127.0.0.1:8787` by default. Put a TLS reverse proxy in front for remote access.

### Systemd

```bash
sudo cp deploy/idlescan.service /etc/systemd/system/idlescan.service
sudo mkdir -p /var/lib/idlescan /var/log/idlescan
sudo chown idlescan:idlescan /var/lib/idlescan /var/log/idlescan
sudo systemctl daemon-reload
sudo systemctl enable --now idlescan
```

The unit uses `DynamicUser`, `ProtectSystem=strict`, `NoNewPrivileges`, and other hardening
options. Configuration goes in `/etc/default/idlescan`.

## First Run

1. Start the server: `idlescan serve`
2. Open http://127.0.0.1:8787
3. Add wallets: `idlescan wallets add 0x... --label "cold"`
4. Run a scan: `idlescan scan 0x...` or click "Scan now" in the UI
5. View results in the UI or export: `idlescan export --format json`

## Database

The SQLite database lives at `IDLESCAN_DB` (default `data/idlescan.db`).

### Schema

Run `idlescan db status` to check schema version and row counts. Run `idlescan db migrate` to
apply pending migrations (a pre-migration backup is written automatically).

```bash
idlescan db status
idlescan db migrate
idlescan db check
idlescan db prune --yes   # actually delete rows outside retention windows
```

### Backups

```bash
idlescan backup                    # write to data/backups/
idlescan backup --out /safe/place/ # custom destination
idlescan backup --out /dir/        # writes to directory with rotation
```

Backups written to a directory are rotated to the newest `IDLESCAN_BACKUP_KEEP` (default 5) files.

## Upgrades

1. Stop the server
2. Back up the database: `idlescan backup`
3. Install the new version: `pip install -U idlescan`
4. Migrate: `idlescan db migrate` (pre-migration backup written automatically)
5. Start the server

A database written by a newer version of idlescan is refused with an actionable message.

## Reverse Proxy (Caddy)

```caddy
{
    admin off
}

idlescan.example.org {
    reverse_proxy localhost:8787
    header /api/* Authorization Bearer $IDLESCAN_AUTH_TOKEN
}
```

Set `IDLESCAN_HOST=0.0.0.0` and `IDLESCAN_AUTH_TOKEN` when behind a reverse proxy. Never expose
the instance to the network without authentication.

## Monitoring

```bash
idlescan doctor --offline          # configuration, database, imports
idlescan doctor --online           # also probes RPC endpoints
curl -s http://127.0.0.1:8787/api/health   # health with DB, RPC, request stats
curl -s http://127.0.0.1:8787/api/metrics   # request counters
```

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| `refusing to start ... without IDLESCAN_AUTH_TOKEN` | Non-loopback bind without token | Set token or bind to 127.0.0.1 |
| `schema v1 needs migration` | Old database | Run `idlescan db migrate` |
| `schema v3 is newer than this release` | Newer DB than code | Upgrade idlescan or restore backup |
| `missing dataset file(s)` | Dataset not on disk | Add file or disable the drop |
| Scan never completes | RPC endpoint down | Check `IDLESCAN_RPC_<CHAIN>` overrides; doctor --online |
| 401 on /api/* | Missing/invalid bearer token | Set `IDLESCAN_AUTH_TOKEN` and send `Authorization: Bearer <token>` |

## Logs

Process logs (startup, HTTP requests, background failures) go to stderr. Format and level are
controlled by `IDLESCAN_LOG_FORMAT` and `IDLESCAN_LOG_LEVEL`. Durable audit records live in the
SQLite `audit` table.
