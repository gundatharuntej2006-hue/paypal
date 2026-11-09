# Render service handoffs

The gateway and intelligence directories are pending on `ritika`.

- **Vivek** — Provide `gateway/go.mod` and a runnable Go main package. The Blueprint assumes the package is at the module root and builds it with `mkdir -p bin && go build -o bin/gateway .`. If the main package is under `cmd/gateway`, update the build target to `./cmd/gateway`. The binary must bind to `0.0.0.0:$PORT` and return HTTP 200 from `GET /health`. Confirm the gateway environment names and mandate key formats before filling credentials. Use `http://` with the Render private `INTELLIGENCE_HOSTPORT` value. Mount Atul's handler at `POST /webhooks/paypal` and enable CORS for the deployed dashboard origin.
- **Atul** — Provide `intelligence/requirements.txt` with FastAPI and Uvicorn and expose `app` in `intelligence/app/main.py`. The build runs `pip install -r requirements.txt`. Uvicorn binds to `0.0.0.0:$PORT` and the app must return HTTP 200 from `GET /health`. Coordinate the PayPal webhook handler with Vivek so it is reachable on the gateway URL.
