# GovMap REST Proxy

This workspace now contains a small Python backend for local GovMap REST fallback calls. It injects the approved GovMap browser headers from `govmaps.md` while keeping browser-native iframe calls gated to approved origins.

## Run

```bash
python3 -m govmap_proxy.server --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/` for the local proxy test page.

## API

All GovMap calls go through your backend:

```bash
curl -X POST http://localhost:8000/api/govmap/search \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "/your/govmap/rest/path",
    "method": "GET",
    "params": {
      "q": "Jerusalem"
    }
  }'
```

Available endpoints:

- `POST /api/govmap/search`
- `POST /api/govmap/spatial`
- `POST /api/govmap/lookup`
- `POST /api/govmap/proxy`
- `GET /api/govmap/config`
- `GET /health`

`search`, `spatial`, and `lookup` can use default upstream paths from `.env.example`, or callers can pass a GovMap relative `path` in the JSON body. The proxy only allows HTTPS requests to configured GovMap hosts.

## Configuration

The service reads `govmaps.md` by default:

```text
apk_key=...
domain=horizonscanninglab.org
```

The backend sends:

```text
Origin: https://horizonscanninglab.org
Referer: https://horizonscanninglab.org/
```

Use environment variables from `.env.example` to override paths, hosts, key placement, and the staging iframe origin list.

If your frontend runs on a separate dev server, add that origin to `APP_CORS_ORIGINS`.

## Iframe API

The REST proxy does not make `localhost` a valid GovMap iframe origin. Use browser-native `window.govmap.createMap(...)` only from production or from a GovMap-registered HTTPS staging subdomain such as `https://dev.horizonscanninglab.org`.

## Tests

```bash
python3 -m unittest
```
