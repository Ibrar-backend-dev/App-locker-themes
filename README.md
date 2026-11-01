# Wallpaper API

Django + DRF + Postgres backend serving public wallpaper-category and artwork feeds, with media on
Cloudflare R2 and Django admin as the only content-management surface.

## Endpoints

The public API is a GET under `/api/v1/`, returning JSON.

| # | Endpoint | Notes |
|---|---|---|
| 1 | `GET /api/v1/wallpapers/categories` | Active categories, newest first (descending `id`) |
| 2 | `GET /api/v1/wallpapers?category_id=N` | Published artwork in the requested active category |

The response uses this envelope:

```json
{ "data": [ ... ], "meta": { "page": 1, "page_size": 20, "total": 1, "has_next": false } }
```

### Category row

`id, name, description, position, thumbnail_path, artwork_count` — in that order.
`description` is `null` when blank; `artwork_count` counts published wallpapers only.
`thumbnail_path` is the category's absolute cover-image URL, or `null` when no cover is set.
Set the optional category cover image in Django admin.

### Artwork row

`id, category_id, title, media_type, is_premium, original_filename, file_path, created_at`
— in that order. `title` is derived from the uploaded original filename with its extension
removed (for example, `wallpaper.mp4` becomes `wallpaper`). `file_path` is an absolute,
directly loadable URL. `created_at` is an ISO 8601 timestamp in UTC.

### Query parameters

| Param | Default | Behaviour |
|---|---|---|
| `page` | 1 | 1-based. Past the end returns an empty list, not an error. |
| `page_size` | 20 | Max 100. Over the cap is a **400**, not a silent clamp. |
| `category_id` | Required for artwork | Positive category ID; unpublished artwork and inactive categories are not returned. |

Errors come back as `{"detail": "..."}`.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows; use .venv/bin/pip elsewhere

cp .env.example .env                              # then edit it

.venv/Scripts/python manage.py migrate
.venv/Scripts/python manage.py createsuperuser
.venv/Scripts/python manage.py runserver 0.0.0.0:8000
```

Add a category at `/admin/`, then upload files to it — up to 10 per submit, one
wallpaper per file, each published at priority 0 and titled after its filename. The
categories endpoint reports active categories and their published wallpaper counts.

## Deploying to Railway

`railway.json` builds with Nixpacks, runs `collectstatic` at build time, then starts
`migrate` + gunicorn. The health check points at `/api/v1/wallpapers/categories`.

Railway injects `RAILWAY_PUBLIC_DOMAIN` and `PORT` - do not set those yourself. The
domain and Railway's `healthcheck.railway.app` host are appended to `ALLOWED_HOSTS`
automatically, and the domain is added to `CSRF_TRUSTED_ORIGINS`, so a deploy works
before the final domain is known.

### Variables to set on the service

| Variable | Value |
|---|---|
| `SECRET_KEY` | fresh random string, never the local one |
| `DEBUG` | `False` |
| `DATABASE_URL` | `${{ Postgres.DATABASE_URL }}` - a reference, not a pasted literal |
| `USE_LOCAL_MEDIA` | `False` |
| `R2_ACCOUNT_ID` | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | R2 S3 API credentials |
| `R2_BUCKET_NAME` | R2 bucket name |
| `R2_ENDPOINT_URL` | `https://<account-id>.r2.cloudflarestorage.com` |
| `R2_PUBLIC_BASE_URL` | Public HTTPS bucket URL, such as its `r2.dev` domain |
| `ALLOWED_HOSTS` | only if you add a custom domain |
| `CSRF_TRUSTED_ORIGINS` | only for a custom domain, with scheme: `https://example.com` |

**`USE_LOCAL_MEDIA` must be `False` in production.** Railway's filesystem is ephemeral,
so anything uploaded through the admin in local-media mode disappears on the next deploy
and every `artwork` url 404s.

Set `DATABASE_URL` to a reachable database URL. For Railway Postgres, use the service
reference `${{ Postgres.DATABASE_URL }}` rather than copying a credential-bearing URL.

Two hardening settings stay opt-in because both can bite on first deploy:
`SECURE_SSL_REDIRECT` (Railway's health check can fail on the 301) and
`SECURE_HSTS_SECONDS` (irreversible for its max-age). Turn them on once a custom domain
is settled. Secure session and CSRF cookies are already on whenever `DEBUG` is `False`.

### Steps

```bash
railway init                 # or connect the repo on railway.app
railway add --database postgres
# set the variables above under Service -> Variables
railway up
railway run python manage.py createsuperuser
```

Then open `https://<your-domain>/admin/` to add categories and upload files.

## Storage

`USE_LOCAL_MEDIA=True` (the default) keeps files in `./media` and builds `artwork` from
the request host, so the API runs with no object-storage credentials.

Set it to `False` and fill the `R2_*` values to switch to Cloudflare R2, which is driven
through its S3-compatible API. `R2_ENDPOINT_URL` is the S3 API endpoint;
`R2_PUBLIC_BASE_URL` must be the HTTPS public URL enabled for the bucket. R2 takes
precedence when its environment values are configured. Legacy `B2_*` values are still
supported when no R2 values are configured. The bucket must be **public**: responses
carry unsigned, cacheable URLs through the public base URL (or the B2 CDN domain for a
legacy B2 setup). Nothing else changes — uploads keep the same
`wallpaper/<category>/assets/<YYYY>/<MM>/<uuid>.<ext>` layout.

## Tests

`wallpapers/tests/test_contract.py` asserts the category and artwork response contracts and
verifies that the unfiltered wallpaper-list URL returns 404.

```bash
.venv/Scripts/python manage.py test
```

It uses SQLite by default. To test against PostgreSQL instead, set `DATABASE_URL` to a
reachable PostgreSQL URL first. Media goes to a temp directory during the run; remote
object storage is never touched.
