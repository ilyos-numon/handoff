"""Invite acceptance, current principal and logout routes."""

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from handoff.rbac.deps import current_principal, get_auth
from handoff.rbac.tokens import HOME_PATH, InvalidInvite, Role, read_invite

AUTH_DISABLED = "auth disabled"
INVALID_INVITE = "invalid invite"

router = APIRouter(prefix="/auth")


class MeResponse(BaseModel):
    auth: bool
    role: Role | None
    leaver: str | None


@router.get("/accept")
def accept(token: str, request: Request) -> RedirectResponse:
    auth = get_auth(request)
    if auth is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=AUTH_DISABLED)
    try:
        principal = read_invite(auth.secret, token, auth.invite_ttl_s)
    except InvalidInvite:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_INVITE) from None
    request.session.clear()
    request.session.update({"role": principal.role, "leaver": principal.leaver})
    return RedirectResponse(HOME_PATH[principal.role], status_code=status.HTTP_303_SEE_OTHER)


@router.get("/me")
def me(request: Request) -> MeResponse:
    if get_auth(request) is None:
        return MeResponse(auth=False, role=None, leaver=None)
    principal = current_principal(request)
    if principal is None:
        return MeResponse(auth=True, role=None, leaver=None)
    return MeResponse(auth=True, role=principal.role, leaver=principal.leaver)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request) -> Response:
    if get_auth(request) is not None and "session" in request.scope:
        request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
