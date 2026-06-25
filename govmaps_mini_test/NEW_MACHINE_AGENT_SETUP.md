# New Machine Agent Setup

This zip is a minimal GovMap proxy + native iframe test package for a coding agent on a fresh machine. It intentionally excludes real secrets such as `.env` and `govmaps.md`; use the included examples and copy the real values separately.

## Contents

- `govmap_proxy/`: Python stdlib server and GovMap REST proxy client
- `public/index.html`: REST proxy tester
- `public/iframe.html`: native GovMap iframe API page
- `tests/`: unit tests
- `scripts/start-local.sh`: normal local server
- `scripts/start-reverse-proxy-local.sh`: local server for `/govmap-local` production reverse-proxy mode
- `govmaps.example.md`: GovMap key template
- `.env.example`: optional environment template
- `README.md`, `AGENTS.md`, `TUNNEL_SETUP.md`: reference docs

## Requirements

- Python 3.10 or newer
- No Python packages are required
- `curl` for checks
- Optional: `cloudflared` or another tunnel tool
- Optional: AWS CLI permissions if updating CloudFront
- On Windows, a PowerShell terminal and the full path to a working Python 3.10-or-newer interpreter

## First Run

Unzip and enter the folder:

```bash
unzip govmap_proxy_minimal_setup.zip
cd govmap_proxy_minimal_setup
```

Create the sensitive config file:

```bash
cp govmaps.example.md govmaps.md
```

Edit `govmaps.md`:

```text
apk_key=YOUR_GOVMAP_API_KEY
domain=horizonscanninglab.org
```

Optional environment setup:

```bash
cp .env.example .env
```

If you use `.env`, remember that the Python server does not auto-load it unless you start through the scripts.

Keep every non-comment `.env` entry in `KEY=value` form. A bare secret line will be interpreted as a shell command when the Bash startup scripts source the file.

Run tests:

```bash
python3 -m unittest
```

Start normal local mode:

```bash
./scripts/start-local.sh
```

Check health:

```bash
curl http://127.0.0.1:8000/health
```

Open the REST tester:

```text
http://127.0.0.1:8000/
```

## Local Native Iframe Limitation

Opening this URL locally can verify the page shell only:

```text
http://127.0.0.1:8000/iframe.html
```

GovMap native iframe auth will not work from localhost unless GovMap explicitly whitelists the exact localhost origin. For real iframe validation, the browser address bar must show an approved HTTPS origin such as:

```text
https://horizonscanninglab.org/govmap-local/iframe.html
```

## Production Reverse Proxy Mode

Use this when `https://horizonscanninglab.org/govmap-local/*` should proxy to the local server through a public HTTPS tunnel.

Start the guarded local server:

```bash
./scripts/start-reverse-proxy-local.sh
```

The script prints a private `X-Govmap-Proxy-Secret` value. Configure the reverse proxy or CloudFront origin custom header with the same value. Direct tunnel requests to protected endpoints without that header return `403`; `/health` intentionally remains public.

On Windows, load `.env` explicitly before starting the server:

```powershell
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
  }
}

& 'C:\path\to\python.exe' -m govmap_proxy.server --host 0.0.0.0 --port 8000
```

Create a public tunnel to the local server, for example with Cloudflare Quick Tunnel:

```bash
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000
```

This prints a temporary hostname like:

```text
https://example-name.trycloudflare.com
```

CloudFront production routing used in this project:

```text
Distribution: EJFB19ZLRNP4Y
Viewer path: /govmap-local/*
Origin domain: the current tunnel hostname, without https://
Origin protocol: HTTPS only
Path behavior target origin: GovMapLocalTunnel
Cache: disabled or TTL 0
Allowed methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
Cached methods: GET, HEAD
Custom headers:
  X-Forwarded-Proto: https
  X-Forwarded-Host: horizonscanninglab.org
  X-Govmap-Proxy-Secret: <the generated secret>
```

CloudFront's seven-method set is required to allow POST. The Python application still rejects methods it does not implement.

Wait for distribution status `Deployed`. A Quick Tunnel is temporary: if `cloudflared` stops, expect error `1033`; if its old hostname expires, expect `1016` or `NXDOMAIN`. Start a new tunnel and replace only the `GovMapLocalTunnel` domain.

Optional routing automation may use these names without documenting their values:

```text
AWS_ZONE
AWS_PUBLIC_KEY
AWS_SECRET_KEY
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID
```

The application does not require these management credentials at runtime. Prefer an AWS profile, managed identity, or OS secret store; otherwise keep them only in the ignored local `.env` with least-privileged permissions.

After updating CloudFront, test without printing the token:

```bash
curl -sS https://horizonscanninglab.org/govmap-local/health
curl -sS https://horizonscanninglab.org/govmap-local/api/govmap/iframe-config \
  | python3 -c 'import json,sys; p=json.load(sys.stdin); print({"origin": p.get("origin"), "token": "token" in p})'
curl -sS -X POST https://horizonscanninglab.org/govmap-local/api/govmap/lookup \
  -H 'Content-Type: application/json' \
  -d '{"path":"/api/search-service/getTypes","method":"POST","json":{"language":"en"}}'
```

Then open:

```text
https://horizonscanninglab.org/govmap-local/iframe.html
```

## Rollback

If the reverse proxy should be removed, update CloudFront distribution `EJFB19ZLRNP4Y` and delete only:

- the `govmap-local/*` cache behavior
- the `GovMapLocalTunnel` origin

Leave the default production S3 origin and homepage behavior untouched.

## Security Notes

- Do not commit or publish `govmaps.md`, `.env`, or `.reverse_proxy_secret`.
- Successful `/api/govmap/iframe-config` responses contain the GovMap token and are served with `Cache-Control: no-store`.
- The REST proxy helps backend REST calls; it does not make localhost a valid GovMap native iframe origin.
- Rotate any AWS, Cloudflare, GovMap, or proxy credential that appears in chat, logs, tickets, or screenshots.
- A health response can succeed even when outbound GovMap calls are blocked; include a real POST lookup in readiness checks.
