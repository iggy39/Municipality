# GovMap REST Proxy

This workspace now contains a small Python backend for local GovMap REST fallback calls. It injects the approved GovMap browser headers from `govmaps.md` while keeping browser-native iframe calls gated to approved origins.

## Run

```bash
python3 -m govmap_proxy.server --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/` for the local proxy test page.

The Python process does not load `.env` automatically. On Unix-like systems, use `scripts/start-local.sh` or `scripts/start-reverse-proxy-local.sh`. On Windows, load only `KEY=value` lines before starting the configured Python interpreter:

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
  }
}

& 'C:\path\to\python.exe' -m govmap_proxy.server --host 0.0.0.0 --port 8000
```

Use a Python 3.10-or-newer executable that exists on the machine. Ensure only one proxy process is listening on port 8000.

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
- `GET /api/govmap/iframe-config`
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

## Native Iframe API

Open `/iframe.html` from an approved HTTPS origin to use GovMap's native browser iframe API. The page loads the official script directly from:

```html
<script src="https://www.govmap.gov.il/govmap/api/govmap.api.js"></script>
```

The page then calls `/api/govmap/iframe-config` to receive the token and default map settings, and creates the map with `govmap.createMap("map", ...)`. This config endpoint returns `403` unless the effective browser origin is in `GOVMAP_ALLOWED_IFRAME_ORIGINS`.

For reverse proxy hosting, forward these headers to the Python server:

```text
Host or X-Forwarded-Host
X-Forwarded-Proto: https
```

If the app is mounted below a production path such as `/govmap-local`, start the server with:

```bash
APP_PUBLIC_PATH_PREFIX=/govmap-local python3 -m govmap_proxy.server --host 0.0.0.0 --port 8000
```

For a public tunnel behind CloudFront, also set `APP_REVERSE_PROXY_SECRET` and configure CloudFront to send the same value in `X-Govmap-Proxy-Secret`. This blocks direct tunnel requests from reaching protected endpoints. `/health` intentionally remains available without the secret.

The production route is:

```text
https://horizonscanninglab.org/govmap-local/*
  -> CloudFront distribution EJFB19ZLRNP4Y
  -> GovMapLocalTunnel origin
  -> current HTTPS tunnel hostname
  -> http://127.0.0.1:8000
```

CloudFront must use TTL `0`, target `GovMapLocalTunnel` for `govmap-local/*`, and inject:

```text
X-Forwarded-Proto: https
X-Forwarded-Host: horizonscanninglab.org
X-Govmap-Proxy-Secret: <same value as APP_REVERSE_PROXY_SECRET>
```

To support the documented POST endpoints, select CloudFront's seven-method set: `DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT`. The Python server itself only implements the methods documented in this README.

A Quick Tunnel hostname is temporary. If `cloudflared` stops or creates a new hostname, the CloudFront origin must be updated again. Error `1033` means the tunnel connector is unavailable; `1016` means the configured tunnel hostname no longer resolves.

The REST proxy does not make `localhost` a valid GovMap iframe origin. Localhost can verify the page shell, but real native iframe validation must happen from `https://horizonscanninglab.org` or a GovMap-approved HTTPS staging subdomain such as `https://dev.horizonscanninglab.org`.

For local development with a public HTTPS tunnel, see `TUNNEL_SETUP.md`.

## Tests

```bash
python3 -m unittest
```
