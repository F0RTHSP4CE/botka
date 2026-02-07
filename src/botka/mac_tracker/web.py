from __future__ import annotations

from datetime import datetime, timezone
from html import escape as html_escape
import ipaddress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import async_sessionmaker

from botka.config import Settings
from botka.db.models import UserTier
from botka.services.mac_tracker_service import MacTrackerService, MikrotikDhcpClient
from botka.services.user_service import UserService


def build_mac_tracker_app(
    settings: Settings, sessionmaker: async_sessionmaker
) -> FastAPI:
    app = FastAPI(title="Botka MAC Tracker")
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/mac/{token}")
    async def mac_form(token: str, request: Request) -> HTMLResponse:
        title = "F0 MAC Address Tracker"
        ip_address = _get_client_ip(request)
        if ip_address is None:
            return HTMLResponse(
                _page("Error", "<p>Cannot determine IP.</p>"), status_code=400
            )
        subnet_allowed = _is_ip_allowed(settings, ip_address)
        subnet_warning = _build_subnet_warning(settings, ip_address)
        register_form = ""
        if subnet_allowed:
            register_form = """
                <form method="post" action="/mac/{token}/confirm">
                    <button type="submit">Register device</button>
                </form>
                """
        async with sessionmaker() as session:
            service = MacTrackerService(session, settings, MikrotikDhcpClient(settings))
            user_id = service.get_token_user_id(token)
            if user_id is None:
                return HTMLResponse(
                    _page(
                        "Link expired",
                        "<p>Get a new one with <b>/mac</b> command.</p>",
                    ),
                    status_code=404,
                )
            user_service = UserService(session, settings)
            user = await user_service.get_user_by_id(user_id)
            if user and user.username:
                user_label = f"@{html_escape(user.username)}"
            elif user:
                user_label = html_escape(str(user.telegram_id))
            else:
                user_label = html_escape(str(user_id))

        return HTMLResponse(
            _page(
                title,
                """
                <p>Tap the button below to register your device in the space.</p>
                <p>User: <strong>{user}</strong></p>
                <p>IP: <strong>{ip}</strong></p>
                {subnet_warning}
                {register_form}
                <br>
                <hr>
                <div class="examples">
                    <p><span style="color: orange">⚠️</span> <strong>Please disable MAC randomization</strong> in WiFi settings! Otherwise mac tracker would not work.</p>
                    <div class="example-grid">
                        <figure>
                            <img src="/static/mac_examples/windows.png" alt="Windows Wi-Fi settings" />
                            <figcaption>Windows</figcaption>
                        </figure>
                        <figure>
                            <img src="/static/mac_examples/ios.jpg" alt="iOS Wi-Fi settings" />
                            <figcaption>iOS</figcaption>
                        </figure>
                        <figure>
                            <img src="/static/mac_examples/nm.png" alt="Network Manager settings" />
                            <figcaption>Linux</figcaption>
                        </figure>
                    </div>
                </div>
                """.format(
                    user=user_label,
                    ip=html_escape(ip_address),
                    register_form=(
                        register_form.format(token=html_escape(token))
                        if register_form
                        else ""
                    ),
                    subnet_warning=subnet_warning,
                    token=html_escape(token),
                ),
            )
        )

    @app.post("/mac/{token}/confirm")
    async def mac_confirm(token: str, request: Request) -> HTMLResponse:
        ip_address = _get_client_ip(request)
        if ip_address is None:
            return HTMLResponse(
                _page("Error", "<p>Cannot determine IP.</p>"), status_code=400
            )
        subnet_warning = _build_subnet_warning(settings, ip_address)
        async with sessionmaker() as session:
            mikrotik = MikrotikDhcpClient(settings)
            service = MacTrackerService(session, settings, mikrotik)
            user_id = service.get_token_user_id(token)
            if user_id is None:
                return HTMLResponse(
                    _page(
                        "Link expired",
                        "<p>Get a new one with <b>/mac</b> command.</p>",
                    ),
                    status_code=404,
                )
            user_service = UserService(session, settings)
            user = await user_service.get_user_by_id(user_id)
            if user is None or user.tier not in (UserTier.resident, UserTier.member):
                return HTMLResponse(
                    _page(
                        "Not allowed",
                        "<p>Only residents and members can register devices.</p>",
                    ),
                    status_code=403,
                )
            lease = await service.resolve_link(token, ip_address)
        if lease is None:
            return HTMLResponse(
                _page(
                    "Not found",
                    "<p>Could not match your IP {ip} to an active DHCP lease.</p>".format(
                        ip=html_escape(ip_address)
                    ),
                ),
                status_code=404,
            )
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return HTMLResponse(
            _page(
                "Registered",
                """
                <p>Device registered. MAC: <strong>{mac}</strong></p>
                <p>{time}</p>
                {subnet_warning}
                """.format(
                    mac=html_escape(lease.mac_address),
                    time=html_escape(now),
                    subnet_warning=subnet_warning,
                ),
            )
        )

    return app


def _get_client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is None:
        return None
    return request.client.host


def _build_subnet_warning(settings: Settings, ip_address: str) -> str:
    if _is_ip_allowed(settings, ip_address):
        return ""
    warning_text = (settings.mac_tracker_subnet_warning_text or "").strip()
    if not warning_text:
        warning_text = "Note: your IP is outside the allowed MAC tracker subnets."
    return (
        '<p style="color: #fff; background-color: #ff0000; padding: 0.5rem">'
        f"{warning_text}"
        "</p>"
    )


def _is_ip_allowed(settings: Settings, ip_address: str) -> bool:
    subnets = settings.mac_tracker_allowed_subnets
    if not subnets:
        return True
    try:
        ip_obj = ipaddress.ip_address(ip_address)
    except ValueError:
        return False
    for subnet in subnets:
        try:
            network = ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            continue
        if ip_obj in network:
            return True
    return False


def _page(title: str, body: str) -> str:
    return f"""
    <!doctype html>
    <html lang=\"en\">
      <head>
        <meta charset=\"utf-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <title>{html_escape(title)}</title>
        <style>
                    body {{ font-family: Arial, sans-serif; margin: 2rem; }}
                    button {{ padding: 0.6rem 1rem; font-size: 1rem; }}
                    .examples {{ margin: 1.5rem 0; }}
                    .example-grid {{
                        display: grid;
                        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                        gap: 1rem;
                        align-items: start;
                    }}
                    .example-grid figure:first-child {{
                        grid-column: span 2;
                    }}
                    .example-grid figure {{
                        margin: 0;
                        background: #ffffff;
                        border: 1px solid #e5e7eb;
                        border-radius: 12px;
                        overflow: hidden;
                        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
                    }}
                    .example-grid img {{
                        width: 100%;
                        height: auto;
                        display: block;
                    }}
                    .example-grid figcaption {{
                        padding: 0.5rem 0.75rem 0.75rem;
                        font-size: 0.9rem;
                        color: #4b5563;
                        text-align: center;
                    }}
                    @media (max-width: 600px) {{
                        body {{ margin: 1rem; }}
                        button {{ width: 100%; }}
                        .example-grid figure:first-child {{
                            grid-column: auto;
                        }}
                    }}
        </style>
      </head>
      <body>
        <h1>{html_escape(title)}</h1>
        {body}
      </body>
    </html>
    """


async def run_mac_tracker_server(
    settings: Settings, sessionmaker: async_sessionmaker
) -> None:
    import uvicorn

    app = build_mac_tracker_app(settings, sessionmaker)
    config = uvicorn.Config(
        app,
        host=settings.mac_tracker_bind_host,
        port=settings.mac_tracker_bind_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()
