# PDF Ebook Translator Bot

Python 3.11+ Telegram bot and Redis Streams workers for queued PDF ebook translation.

## Components

- `bot.py`: Telegram upload/language/status/cancel UX and completion delivery.
- `ingest_worker.py`: PyMuPDF style extraction and paragraph chunking.
- `translate_worker.py`: provider-key rotation with Redis cooldowns.
- `reassemble_worker.py`: Mode A in-place reconstruction and Mode B reflow.
- Redis Streams: `ingest:pending`, `translate:pending`, `reassemble:pending`.
- Redis Pub/Sub: `job:done:{job_id}`.
- SQLite: job/chunk metadata at `DB_PATH`.

## Deployment

Run `scripts/install.sh` as root, then fill:

- `/var/www/ebook-translator/.env`
- `/var/www/ebook-translator/providers.yaml`

Start services:

```bash
systemctl enable --now ebook-bot ebook-ingest ebook-reassemble
systemctl enable --now ebook-translate@1 ebook-translate@2 ebook-translate@3
```

Provider keys are read from comma-separated environment variables referenced by `providers.yaml`, or from explicit `keys` arrays if you choose to keep that file private.

