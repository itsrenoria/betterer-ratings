# betterer-ratings

Continuously discovers ratings through TMDB, IMDb, and MDBList, stores them in
SQLite, and submits due ratings, mappings, and episode ratings to PublicMetaDB.

This is personal automation software. Use your own credentials, respect provider
terms, and keep request rates appropriate for your plans.

## Run with Docker

```bash
cp config.example.toml config.toml
```

Add your API keys to `config.toml`, review the source and rate-limit settings,
then start the service:

```bash
docker compose pull
docker compose up -d
```

The dashboard is available at <http://localhost:8087>. Compose pulls the public
`ghcr.io/itsrenoria/betterer-ratings:latest` image for `linux/amd64` or
`linux/arm64`.

After the first successful image publication, the repository owner must confirm
that the GHCR package visibility is **Public** in GitHub Package Settings before
anonymous Compose pulls will work. This is a one-time setup step.

By default, runtime state stays on the host:

- `config.toml` is mounted read-only at `/config/config.toml`.
- `data/db` contains the SQLite database.
- `data/imdb` contains IMDb archives and indexes.
- `data/temp` contains temporary indexes.

Set `BETTERER_DATA_DB`, `BETTERER_DATA_IMDB`, or `BETTERER_DATA_TEMP` to
override these host paths.

Do not delete `data/db` unless you intend to reset the service.

## Configuration

`config.example.toml` documents every public option. The repository defaults
include:

- seven-day title rating refreshes
- daily episode rating refreshes
- daily IMDb archive refresh at 13:00 UTC
- TMDB at 40 requests per second
- MDBList batches of 200 IDs (supported range: 1–200)
- 16 submission workers

MDBList batch size and daily plan quota are separate limits.

## Update or Roll Back

```bash
docker compose pull
docker compose up -d
```

For an exact rollback, copy the published manifest digest from the workflow
summary, set this in a Compose `.env` file, and recreate the service:

```text
BETTERER_IMAGE=ghcr.io/itsrenoria/betterer-ratings@sha256:<manifest-digest>
```

```bash
docker compose pull
docker compose up -d
```

To follow `latest` again, remove `BETTERER_IMAGE`, then run the two commands
above again.

## Development

```bash
python3 -m pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

Run locally with `betterer-ratings --config config.toml`, or build a local image:

```bash
docker build -t betterer-ratings:local .
```

Logs are written as structured JSON to stdout. The supplied Compose service
rotates Docker logs.

## License

MIT. See `LICENSE`.
