# IngestArr

A media intake tool that sits in front of your *arr stack and does the tedious part for you.

You drop a CSV of stuff you want - books, movies, TV shows, music, audiobooks - and IngestArr figures out where each item needs to go. Movies get handed to Radarr. TV shows go to Sonarr. Music to Lidarr. For books and audiobooks, where the *arr apps are frankly terrible, it searches Prowlarr directly, uses an LLM to pick the right result from a pile of garbage release names, and kicks the download to SABnzbd or qBittorrent.

## Why this exists

I got tired of manually adding things one at a time across five different apps. Especially books - Readarr barely works, and even when it does, it picks the wrong edition half the time. I wanted to hand a list to something smarter than a regex and have it just... deal with it.

## How it works

There are two modes, and IngestArr picks the right one automatically based on the media type:

**Hand-off mode** (movies, TV, music) - The *arr apps are good at these. IngestArr uses an LLM to resolve any ambiguity in what you asked for ("Blade Runner" → which one?), then adds it to the right *arr app with monitoring enabled. The *arr app handles searching, downloading, and quality from there.

**Direct mode** (books, audiobooks) - No *arr app does these well. IngestArr generates search queries, hits Prowlarr, pre-filters the results by size and format, then sends the shortlist to an LLM for classification. The best match gets pushed to your download client. Uncertain matches go to a review queue instead of being grabbed blindly.

## The CSV

```csv
Type,Title,Creator,Year,Season,Notes
book,The Name of the Wind,Patrick Rothfuss,2007,,
movie,Blade Runner 2049,Denis Villeneuve,2017,,prefer 4k director's cut
tv,Breaking Bad,,2008,,all seasons
tv,The Wire,,2002,1-3,
music,OK Computer,Radiohead,1997,,prefer FLAC
audiobook,Project Hail Mary,Andy Weir,2021,,
```

`Type` and `Title` are required. Everything else is optional but helps. The `Notes` column is freeform - the LLM reads it and factors it into decisions (resolution preferences, format preferences, specific editions, whatever).

## Setup

1. Copy `.env.example` to `.env` and fill in your API keys
2. You need Prowlarr and at least one download client (SABnzbd or qBittorrent)
3. *Arr apps are optional - only configure the ones you use
4. An LLM endpoint is required (Ollama locally, or OpenRouter for cloud)

```bash
cp .env.example .env
# edit .env with your keys
docker compose up -d
```

Drop CSVs into `data/input/` and IngestArr picks them up automatically.

## Running it

IngestArr runs as a service by default, watching a folder for new CSVs. But you can also use it as a one-shot tool:

```bash
# Service mode (default, watches for new CSVs)
docker compose up -d

# Process a specific file
docker exec ingestarr python -m ingestarr process mylist.csv

# Check what's happening with your items
docker exec ingestarr python -m ingestarr status

# Validate a CSV without actually doing anything
docker exec ingestarr python -m ingestarr validate mylist.csv

# Dry run - shows what it would do
docker exec ingestarr python -m ingestarr process mylist.csv --dry-run
```

## Requirements

- Docker (Linux host - the file watcher uses inotify, which is Linux-only)
- Prowlarr (indexer aggregator)
- At least one download client (SABnzbd or qBittorrent)
- An LLM (Ollama on your network, or OpenRouter)
- Whatever *arr apps you want for hand-off mode (Radarr, Sonarr, Lidarr)

## Status

Early. It works, but it hasn't been battle-tested yet. Expect rough edges.

## License

GPL-3.0. See [LICENSE](LICENSE).
