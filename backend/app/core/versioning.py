"""URL versioning for the public API.

Every route in this app is currently declared under the unversioned `/api`
prefix (or `/auth`), and the frontend calls those paths directly. There is
today no external consumer that would ever need a *stable* contract while
the backend keeps evolving underneath it -- but that changes the moment a
mobile app, a partner integration, or any client that can't be redeployed
in lockstep with this backend shows up, since at that point a breaking
response-shape change on `/api/...` breaks them immediately.

Rather than duplicate every route declaration under both `/api/...` and
`/api/v1/...` (invasive, and easy to let drift out of sync), this rewrites
`/api/v1/*` to `/api/*` at the ASGI layer, before routing -- so every
existing and future `/api` route is automatically available at both paths
with zero duplicate declarations. `/api/...` (unversioned) keeps meaning
"whatever this backend currently does" for the frontend, which needn't
migrate. `/api/v1/...` is the pinned contract for any future external
consumer.

When a genuinely breaking v2 becomes necessary, that's the point to stop
rewriting v1 straight onto the live `/api` routes and instead fork: keep
v1 pointed at the old behaviour (e.g. by snapshotting the routes it needs)
while `/api` and a new `/api/v2` move forward. Nothing about that future
split requires undoing this middleware.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

_VERSIONED_PREFIX = "/api/v1"
_UNVERSIONED_PREFIX = "/api"


class ApiVersionRewriteMiddleware:
    """Pure ASGI middleware: rewrites an `/api/v1/...` (or exactly
    `/api/v1`) request path to `/api/...` before the router sees it.
    Non-matching paths (including `/api/...` itself and `/auth/...`) pass
    through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == _VERSIONED_PREFIX or path.startswith(_VERSIONED_PREFIX + "/"):
                scope = dict(scope)
                scope["path"] = _UNVERSIONED_PREFIX + path[len(_VERSIONED_PREFIX):]
                if scope.get("raw_path") is not None:
                    scope["raw_path"] = scope["path"].encode("utf-8")

        await self.app(scope, receive, send)
