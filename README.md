# podimo-dl

### Setup

You need [Python](https://www.python.org/) to run this program. Depending on your installation the program may be invoced as either `python` or `python3`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
python podimo-dl.py
```

You will be prompted for your Podimo credentials and the podcast name to search for.

Optionally, create a `config.py` file using the format provided in `config.sample.py` to skip the credential prompts:

```bash
python podimo-dl.py --config -p "Podcast Name or ID" --download
```

By default, downloads include both audio and video (when available per episode).  
Use `--audio` or `--video` to force a strict single-media download mode.

### Multiple show names in config

You can define a list of shows in `config.py` and run without `-p/--podcast`.

```python
email = "you@example.com"
password = "..."
is_creator = False
podcast_searches = [
    "Show Name A",
    "Show Name B",
    # or UUIDs
]
```

Resolution order:
- If `-p/--podcast` is provided, that single value is used.
- Else, if `podcast_searches` exists in `config.py`, each entry is processed.
- Else, the script prompts for one podcast name interactively.

### Options

| Flag | Description |
|------|-------------|
| `-p / --podcast` | Podcast name to search for or Podcast ID |
| `-c / --config` | Load credentials from `config.py` |
| `-d / --download` | Download without prompting |
| `--audio` | Download audio only (strict mode) |
| `--video` | Download video only (strict mode) |
| `--premium-only` | Only download premium episodes |
| `--overwrite` | Re-download and overwrite files that already exist |
| `--debug` | Print debug information (resolved URLs and ffmpeg commands) |
| `-o / --output` | Output directory for downloaded files |

## Docker + cron timer

This repository now includes `Dockerfile` + `docker-compose.yml` to run downloads on a cron schedule.

1. Create `config.py` (for credentials and optional `podcast_searches`) in the project root.
2. Start the service:

```bash
docker compose up -d --build
```

Configure schedule/behavior in `docker-compose.yml` via environment variables:

- `CRON_SCHEDULE`: cron expression (default: `"0 3 * * *"`).
- `PODIMO_ARGS`: CLI args passed to the downloader (default: `"--config --download -o /downloads"`).
- `RUN_ON_STARTUP`: `"true"` to run once immediately when the container starts.
- `TZ`: timezone for cron execution.
