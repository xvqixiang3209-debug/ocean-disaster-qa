"""Production entry point for running the Flask application on Windows."""

from __future__ import annotations

import os

os.environ.setdefault('MARINE_FORECAST_WORKER', 'true')

from waitress import serve

from server import app, forecast_runtime


def _integer_setting(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def main() -> None:
    host = os.getenv("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _integer_setting("APP_PORT", 8766, 1, 65535)
    threads = _integer_setting("APP_THREADS", 8, 1, 64)
    if os.getenv('MARINE_WARNING_WORKER','true').lower()=='true':
        forecast_runtime.start_warning_worker()
    print(f"Production server listening on http://{host}:{port}")
    print("Health check: /api/typhoon/status")
    if host in {"0.0.0.0", "::"}:
        print("[Security Notice] The application is reachable on network interfaces; use a firewall and trusted reverse proxy.")
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        ident="OceanTyphoonAgent",
        expose_tracebacks=False,
    )


if __name__ == "__main__":
    main()
