# Agent Setup Guide

This project is a minimal GovMap REST proxy. It lets local browser code call your backend, while the backend calls GovMap with the approved `Origin` and `Referer` headers from `govmaps.md`.

## Requirements

- Python 3.10 or newer
- No Python packages are required
- A `govmaps.md` file in the project root

`govmaps.md` must contain:

```text
apk_key=YOUR_GOVMAP_API_KEY
domain=horizonscanninglab.org
```

Treat `govmaps.md` as sensitive because it contains the GovMap API key.

## Start The Server

From the project root:

```bash
python3 -m govmap_proxy.server --host 0.0.0.0 --port 8000
```

Open the local test page:

```text
http://localhost:8000/
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "ok": true
}
```

The Python module does not auto-load `.env`. Use the startup scripts on Unix-like systems. On Windows, parse only `KEY=value` lines into the process environment before launching the configured Python 3.10-or-newer executable. Ensure only one proxy process is bound to port 8000; duplicate listeners can make prefixed requests alternate between `200` and `404`.

## Use The Proxy

The browser or frontend should call this backend, not GovMap directly, for REST fallback calls.

Available local endpoints:

```text
GET  /health
GET  /api/govmap/config
POST /api/govmap/search
POST /api/govmap/spatial
POST /api/govmap/lookup
POST /api/govmap/proxy
GET  /api/govmap/iframe-config
```

Example search request:

```bash
curl -X POST http://127.0.0.1:8000/api/govmap/search \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "/api/search-service/autocomplete",
    "method": "POST",
    "json": {
      "searchText": "Jerusalem",
      "language": "en",
      "maxResults": 5,
      "isAccurate": false
    }
  }'
```

Example lookup request:

```bash
curl -X POST http://127.0.0.1:8000/api/govmap/lookup \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "/api/search-service/getTypes",
    "method": "POST",
    "json": {
      "language": "en"
    }
  }'
```

Example spatial metadata request:

```bash
curl -X POST http://127.0.0.1:8000/api/govmap/spatial \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "/api/spatial-analysis/layer/PARCEL_ALL/metadata-fields",
    "method": "GET",
    "params": {
      "layerName": "PARCEL_ALL"
    }
  }'
```

## Configuration

The server reads `govmaps.md` by default. Optional environment variables:

```bash
export GOVMAP_CONFIG_PATH=govmaps.md
export GOVMAP_BASE_URL=https://www.govmap.gov.il
export GOVMAP_ALLOWED_HOSTS=www.govmap.gov.il,es.govmap.gov.il
export GOVMAP_SEARCH_PATH=/api/search-service/autocomplete
export GOVMAP_SPATIAL_PATH=/api/spatial-analysis/layer/PARCEL_ALL/metadata-fields
export GOVMAP_LOOKUP_PATH=/api/search-service/getTypes
export APP_CORS_ORIGINS=http://localhost:8000,http://localhost:5173,https://horizonscanninglab.org,https://dev.horizonscanninglab.org
export GOVMAP_IFRAME_SCRIPT_URL=https://www.govmap.gov.il/govmap/api/govmap.api.js
export APP_PUBLIC_PATH_PREFIX=/govmap-local
export APP_REVERSE_PROXY_SECRET=generate-a-long-random-value
```

If a default path is not set, callers must pass `"path"` in the JSON request body.

## Native Browser Iframe

Use `/iframe.html` from an origin GovMap has approved, such as production or an approved HTTPS staging domain. The page loads the official GovMap API script from `https://www.govmap.gov.il/govmap/api/govmap.api.js`, fetches `/api/govmap/iframe-config`, and calls `govmap.createMap("map", ...)` with the configured token.

`/api/govmap/iframe-config` returns `403` for unapproved origins and `Cache-Control: no-store` for all responses because successful responses include the token. If the server is behind a reverse proxy, forward `Host` or `X-Forwarded-Host` plus `X-Forwarded-Proto: https` so the server can determine the effective browser origin.

When mounting the app below a production path, set `APP_PUBLIC_PATH_PREFIX`, for example `/govmap-local`. When exposing the local server through a public tunnel, set `APP_REVERSE_PROXY_SECRET` and configure the reverse proxy to send the same value in `X-Govmap-Proxy-Secret`; direct tunnel requests without this header are rejected.

This proxy still does not make `localhost` a valid GovMap iframe origin. Localhost can verify the page shell only; real native iframe authentication must be tested from the approved HTTPS domain.

For tunnel-based native iframe testing, follow `TUNNEL_SETUP.md`.

## Production CloudFront Route

The production iframe URL is:

```text
https://horizonscanninglab.org/govmap-local/iframe.html
```

Distribution `EJFB19ZLRNP4Y` routes `govmap-local/*` to origin `GovMapLocalTunnel`. Preserve the default S3 origin and configure the GovMap origin with:

```text
Origin protocol: HTTPS only
X-Forwarded-Proto: https
X-Forwarded-Host: horizonscanninglab.org
X-Govmap-Proxy-Secret: <same value as APP_REVERSE_PROXY_SECRET>
TTL: 0
Allowed methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
Cached methods: GET, HEAD
```

CloudFront requires the seven-method set to enable POST. The application itself only implements its documented GET, POST, and OPTIONS handlers.

Quick Tunnel hostnames are temporary. `1033` indicates no live connector; `1016` or `NXDOMAIN` indicates a stale hostname. Update only `GovMapLocalTunnel` when the hostname changes, wait for `Deployed`, and verify health, iframe config without printing its token, and a real POST lookup.

The server process needs outbound HTTPS access. `/health` may return `200` while proxy POSTs return `502` if the process cannot reach GovMap.

Treat `.env`, `govmaps.md`, `.reverse_proxy_secret`, AWS credentials, and Cloudflare credentials as sensitive. Rotate any credential exposed in chat or logs.

## Tests

Run unit tests:

```bash
python3 -m unittest
```

If tests create `__pycache__` folders, they are ignored by `.gitignore`.
