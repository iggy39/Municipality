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
```

If a default path is not set, callers must pass `"path"` in the JSON request body.

## Browser Iframe Note

This proxy helps REST calls only. It does not make `localhost` a valid GovMap iframe origin. Use browser-native `window.govmap.createMap(...)` only from an origin GovMap has approved, such as production or an approved HTTPS staging domain.

## Tests

Run unit tests:

```bash
python3 -m unittest
```

If tests create `__pycache__` folders, they are ignored by `.gitignore`.
