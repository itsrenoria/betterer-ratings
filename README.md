# betterer-ratings

Docker-first worker for keeping a local ratings database in sync with configured
catalog, ratings, archive, and destination services.

The project is intended for personal automation. Bring your own service
credentials, keep request rates conservative, and make sure your usage matches
the terms of the services you configure.

## What It Does

The worker runs continuously and coordinates three jobs:

- discover and refresh title metadata from configured catalog sources
- normalize ratings and external identifiers into one local SQLite database
- submit due rating, mapping, and episode-rating work to the configured destination

There are no one-off discovery modes. Source scans, archive ingestion, episode
rating ingestion, stale-title refreshes, and submission retries all run inside
one long-lived process.

## Quick Start

```bash
cp config.example.toml config.toml
```

Edit `config.toml` and replace the placeholder values in `[api_keys]` with your
own credentials. Review the scan intervals, source lists, rate limits, and batch
size before starting the worker.

```bash
docker compose up -d --build
```

The dashboard and API are exposed on port `8087` by default:

```text
http://localhost:8087
```

## Runtime Model

The service has one mode: start, run forever, and stop gracefully on
`SIGTERM` or `SIGINT`.

At startup it validates configuration, opens the SQLite database, recovers
expired in-flight queue rows, starts the dashboard API, and runs the harvester
and submitter until the container stops.

The harvester loop:

- processes episode rating archives first
- refreshes failed local titles, stale titles, and new local rows
- runs configured catalog source scans on the configured interval
- ingests archive-backed title candidates during source scans
- enriches candidates and queues destination writes

The submitter loop claims the oldest due mapping, title-rating, or
episode-rating work across all queues and retries failed work after the
configured delay.

## Storage

The compose file mounts local runtime state into the container:

- `./config.toml` -> `/config/config.toml`
- `./data/...` -> container data directories

The main database inside the container is:

```text
/data/db/betterer_ratings.sqlite3
```

Archive files, indexes, and temporary state are stored under the local `data/`
directory. Local runtime data is ignored by Git.

## Configuration

Use `config.example.toml` as the schema reference. Public configuration covers:

- API credentials
- log level
- source scan interval
- title and episode refresh windows
- source lists
- archive filters
- provider rate limits
- MDBList ratings lookup batch size

Runtime internals such as container database paths, archive paths, submitter
worker count, retry counts, and provider timeouts are intentionally fixed in
the application.

Default behavior:

- title/movie/series ratings refresh after 7 days
- episode ratings refresh after 1 day
- archive refresh runs daily at 13:00 UTC
- TMDB requests are limited to 40 per second
- MDBList ratings lookup batch size is 200 (supported range: 1..200 IDs)
- MDBList daily plan quotas are separate from batch size and may still limit total requests
- submitter worker count is 16

## Local Development

```bash
python3 -m pip install -e ".[dev]"
betterer-ratings --config config.toml
```

The CLI intentionally has no subcommands. It is the same worker entry point used
by Docker.

## Logs

Logs are structured JSON on stdout. Docker or your host logging stack should
handle collection, retention, and rotation.
