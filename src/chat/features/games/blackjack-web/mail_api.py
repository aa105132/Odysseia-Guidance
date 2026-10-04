"""Authenticated inbox router; there is deliberately no HTTP send endpoint."""

from importlib import import_module
import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request


_mail = import_module("src.chat.features.games.blackjack-web.mail_service")
MailService = _mail.MailService
MailError = _mail.MailError


def _integer(value):
    # HTTP IDs are canonical decimal strings; service IDs remain strict integers.
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value):
        value = int(value)
    return _mail.positive_id(value)


def create_mail_router(get_profile, get_db_path):
    router = APIRouter(prefix="/api/mail", tags=["mail"])

    async def owner(profile=Depends(get_profile)):
        try:
            return _integer(profile.get("user_id") if isinstance(profile, dict) else None)
        except MailError as exc:
            raise HTTPException(status_code=401, detail="Authentication required") from exc

    async def inputs(request: Request):
        allowed = {"limit", "before_id"} if request.method == "GET" else set()
        keys = list(request.query_params.keys())
        if any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in keys):
            raise HTTPException(status_code=422, detail="Unexpected or duplicate query parameter")
        # Reject even an empty JSON object: these endpoints never consume a body.
        async for chunk in request.stream():
            if chunk:
                raise HTTPException(status_code=422, detail="Request body is not accepted")

    async def invoke(method, user_id, *args):
        try:
            service = MailService(get_db_path())
            return await getattr(service, method)(user_id, *[_integer(arg) if arg is not None else None for arg in args])
        except MailError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    @router.get("", dependencies=[Depends(inputs)])
    async def inbox(
        limit: Annotated[str, Query(pattern=r"^[1-9][0-9]?$")] = "20",
        before_id: Annotated[str | None, Query(pattern=r"^[1-9][0-9]{0,18}$")] = None,
        user_id=Depends(owner),
    ):
        return await invoke("list_messages", user_id, limit, before_id)

    @router.post("/{id}/read", dependencies=[Depends(inputs)])
    async def read(
        id: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
        user_id=Depends(owner),
    ):
        return await invoke("read", user_id, id)

    @router.post("/{id}/claim", dependencies=[Depends(inputs)])
    async def claim(
        id: Annotated[str, Path(pattern=r"^[1-9][0-9]{0,18}$")],
        user_id=Depends(owner),
    ):
        return await invoke("claim", user_id, id)

    return router
