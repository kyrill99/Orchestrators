"""Who is calling. Mount with:

    app.include_router(build_router())

In production oauth2-proxy (pass_user_headers = true) puts the signed-in
Google account in X-Forwarded-Email / X-Forwarded-User. The backend only
listens on 127.0.0.1 behind the proxy, so the headers can be trusted there.
Locally there is no proxy: DEV_USER_EMAIL (default local@localhost) is used.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Request
from pydantic import BaseModel


class User(BaseModel):
    email: str
    name: str


def current_user(request: Request) -> User:
    email = (
        request.headers.get("x-forwarded-email")
        or os.environ.get("DEV_USER_EMAIL")
        or "local@localhost"
    ).strip().lower()
    name = request.headers.get("x-forwarded-preferred-username") or email.split("@")[0]
    return User(email=email, name=name)


def build_router() -> APIRouter:
    r = APIRouter(prefix="/api", tags=["identity"])

    @r.get("/me", response_model=User)
    def me(request: Request):
        return current_user(request)

    return r
