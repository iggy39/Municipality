# GovMap Production Tunnel Setup

Native GovMap iframe calls must come from an HTTPS origin approved for the token. Production uses the approved browser origin `https://horizonscanninglab.org` and mounts this local service below `/govmap-local` through CloudFront.

```text
Browser /govmap-local/*
  -> CloudFront EJFB19ZLRNP4Y
  -> GovMapLocalTunnel
  -> current tunnel hostname
  -> http://127.0.0.1:8000
```

`http://localhost:8000` can verify the shell and REST server, but it is not a valid native iframe origin.

## 1. Prepare The Environment

Set these values in the local `.env` without committing or sharing their values:

```text
APP_PUBLIC_PATH_PREFIX=/govmap-local
APP_REVERSE_PROXY_SECRET=<cryptographically random secret>
GOVMAP_ALLOWED_IFRAME_ORIGINS=https://horizonscanninglab.org,https://dev.horizonscanninglab.org
```

The Python server does not auto-load `.env`. The Bash reverse-proxy script loads it:

```bash
./scripts/start-reverse-proxy-local.sh
```

On Windows PowerShell, load only `KEY=value` lines and run the configured interpreter:

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
  }
}

& 'C:\path\to\python.exe' -m govmap_proxy.server --host 0.0.0.0 --port 8000
```

The server process needs outbound HTTPS access to GovMap. A sandboxed process may serve `/health` while returning `502` for REST proxy calls.

Verify the guarded prefixed API route without printing the secret:

```bash
curl -H "X-Govmap-Proxy-Secret: $APP_REVERSE_PROXY_SECRET" \
  http://127.0.0.1:8000/govmap-local/api/govmap/config
```

The endpoint should return public configuration JSON. `/health` intentionally remains unguarded and should return:

```json
{
  "ok": true
}
```

## 2. Start The HTTPS Tunnel

For a temporary Cloudflare Quick Tunnel:

```bash
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000
```

Record the generated hostname without `https://`, for example:

```text
example-name.trycloudflare.com
```

Verify it before changing AWS:

```bash
curl -H "X-Govmap-Proxy-Secret: $APP_REVERSE_PROXY_SECRET" \
  https://example-name.trycloudflare.com/govmap-local/health
```

A Quick Tunnel is temporary. Its hostname stops working when `cloudflared` stops, and a later run normally generates a different hostname. Use a named tunnel running as a service for a durable deployment.

## 3. Configure CloudFront

Update only distribution `EJFB19ZLRNP4Y` and preserve the default S3 website origin.

Origin `GovMapLocalTunnel`:

```text
Domain name: <current tunnel hostname, without https://>
Origin protocol: HTTPS only
X-Forwarded-Proto: https
X-Forwarded-Host: horizonscanninglab.org
X-Govmap-Proxy-Secret: <same value as APP_REVERSE_PROXY_SECRET>
```

Cache behavior:

```text
Path pattern: govmap-local/*
Target origin: GovMapLocalTunnel
Viewer protocol: redirect HTTP to HTTPS
Allowed methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
Cached methods: GET, HEAD
Minimum TTL: 0
Default TTL: 0
Maximum TTL: 0
Forward query strings: yes
Forward cookies: none
```

CloudFront offers method sets rather than an arbitrary list. Select the seven-method set to allow the application's POST endpoints; unsupported methods are still rejected by the Python server.

Wait until the distribution status is `Deployed` before testing.

The proxy server itself does not use AWS or Cloudflare management credentials. Routing automation may use `AWS_ZONE`, `AWS_PUBLIC_KEY`, `AWS_SECRET_KEY`, `CLOUDFLARE_API_TOKEN`, and `CLOUDFLARE_ACCOUNT_ID`. Prefer an AWS profile, managed identity, or OS secret store over long-lived keys in `.env`. If local credentials are unavoidable, keep them uncommitted and least-privileged.

## 4. Verify Production

Health and page shell:

```bash
curl -i https://horizonscanninglab.org/govmap-local/health
curl -I https://horizonscanninglab.org/govmap-local/iframe.html
```

Inspect iframe config without printing the token:

```bash
curl -sS https://horizonscanninglab.org/govmap-local/api/govmap/iframe-config \
  | python3 -c 'import json,sys; p=json.load(sys.stdin); print({"origin": p.get("origin"), "tokenPresent": bool(p.get("token"))})'
```

Confirm the response header contains:

```text
Cache-Control: no-store
```

Verify POST routing with a real lookup:

```bash
curl -X POST https://horizonscanninglab.org/govmap-local/api/govmap/lookup \
  -H 'Content-Type: application/json' \
  -d '{"path":"/api/search-service/getTypes","method":"POST","json":{"language":"en"}}'
```

Then open:

```text
https://horizonscanninglab.org/govmap-local/iframe.html
```

Browser checks:

- `window.govmap` exists after the official script loads.
- `govmap.createMap("map", ...)` creates the map iframe.
- The map fires `onLoad`.
- Clicking the map logs `onClick`.
- The parcel layer buttons can toggle `PARCEL_ALL`.

## Troubleshooting

| Symptom | Meaning and action |
| --- | --- |
| Cloudflare `1033` | The configured tunnel has no live connector. Start `cloudflared` or replace the CloudFront origin with the new tunnel hostname. |
| Cloudflare `1016` or tunnel hostname `NXDOMAIN` | The Quick Tunnel hostname expired. Create a new tunnel and update `GovMapLocalTunnel`. |
| HTTP `530` with `X-Cache: Error from cloudfront` | CloudFront selected the GovMap behavior, but its Cloudflare origin failed. Inspect the Cloudflare error code. |
| Prefixed route returns `404` | The server did not load `APP_PUBLIC_PATH_PREFIX=/govmap-local`, or requests are reaching an older duplicate server. |
| Iframe config returns `403` | The proxy secret differs, or `X-Forwarded-Host`/`X-Forwarded-Proto` does not reconstruct an allowed origin. |
| Health is `200`, POST is `502` | The Python process cannot reach GovMap upstream, or GovMap is unavailable. Verify outbound HTTPS from the server process. |
| Results alternate between `200` and `404` | Multiple Python processes are listening on port 8000. Use `netstat -ano | findstr :8000` on Windows and stop only confirmed duplicate proxy processes. |
| GovMap rejects an otherwise healthy iframe | Confirm GovMap approved the exact scheme and hostname shown in the browser address bar. |

## Security

- Never commit or publish `.env`, `govmaps.md`, or `.reverse_proxy_secret`.
- Never print or log the successful iframe-config body because it contains the GovMap token.
- Rotate AWS, Cloudflare, GovMap, and proxy credentials immediately if they are pasted into chat, logs, tickets, or screenshots.
- Keep AWS and Cloudflare credentials least-privileged and use them only for the routing resources in scope.
