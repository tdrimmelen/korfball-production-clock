# Korfball Production Clock

Browser-based scoreboard display for korfball matches. Shows the current time, match time, shot clock, and score on a full-screen grid.

## Architecture

```
Browser (klokken.html)
    └── http://192.168.2.147:8080/
            ├── GET /              → serves klokken.html
            ├── GET /proxy?url=…   → reverse proxy with CORS + 100ms cache
            └── GET /health        → health check
                    │
                    ├── http://192.168.2.147/scoreboard/…   (scoreboard device)
                    └── http://192.168.2.147/shotclock/…    (shot clock device)
```

The proxy is required because the embedded scoreboard devices do not send CORS headers, which would cause the browser to block direct `fetch()` calls.

## Setup

### 1. Configure device addresses

Edit the configuration block at the top of the `<script>` section in `klokken.html`:

```javascript
var scoreboard_address = "192.168.2.147"   // IP of the scoreboard device
var shotclock_address  = "192.168.2.147"   // IP of the shot clock device
var proxy_base         = "http://192.168.2.147:8080/proxy?url="  // IP of the machine running the proxy
```

### 2. Configure the proxy allowlist

Edit `proxy/docker-compose.yml` and add any upstream host IPs to `ALLOWED_UPSTREAM_HOSTS`:

```yaml
environment:
  - ALLOWED_UPSTREAM_HOSTS=192.168.2.147,10.12.0.62,10.12.0.61
```

Only hosts on this list can be proxied. This prevents the proxy from being used as an open relay.

### 3. Start the proxy

```bash
cd proxy
docker compose up --build
```

The proxy starts on port `8080`. The scoreboard is then accessible at:

```
http://<proxy-host>:8080/
```

## Proxy environment variables

| Variable | Default | Description |
|---|---|---|
| `CACHE_TTL_SECONDS` | `0.1` | How long to cache upstream responses (seconds) |
| `ALLOWED_UPSTREAM_HOSTS` | *(see docker-compose.yml)* | Comma-separated list of upstream hosts the proxy may forward to |

## API endpoints polled by the scoreboard

| Endpoint | Device | Data |
|---|---|---|
| `/scoreboard/time/formatted` | Scoreboard | Match time |
| `/scoreboard/score` | Scoreboard | Home / guest score |
| `/shotclock/time/formatted` | Shot clock | Shot clock time |
