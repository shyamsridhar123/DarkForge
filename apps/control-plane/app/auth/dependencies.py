"""FastAPI dependencies for authentication and authorization."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from pydantic import BaseModel

from app.auth.jwt_validator import validate_token
from app.exceptions import InvalidTokenError


class UserClaims(BaseModel):
    """Validated claims extracted from the Entra bearer token."""

    oid: str
    upn: str
    tid: str
    scp: str = ""
    roles: list[str] = []
    _raw_token: str = ""

    model_config = {"populate_by_name": True}

    @classmethod
    def from_claims(cls, claims: dict, raw_token: str) -> "UserClaims":
        obj = cls(
            oid=claims["oid"],
            upn=claims.get("upn") or claims.get("preferred_username") or claims.get("email", ""),
            tid=claims.get("tid", ""),
            scp=claims.get("scp", ""),
            roles=claims.get("roles", []),
        )
        obj._raw_token = raw_token
        return obj

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scp.split()


async def require_user(
    authorization: Annotated[str, Header(alias="Authorization")],
) -> UserClaims:
    """
    Extract and validate the Bearer token from the Authorization header.

    Returns UserClaims on success.
    Raises InvalidTokenError (→ 401) on any validation failure.
    """
    if not authorization.startswith("Bearer "):
        raise InvalidTokenError("Authorization header must be 'Bearer <token>'")

    token = authorization[len("Bearer "):]
    claims = await validate_token(token)
    return UserClaims.from_claims(claims, raw_token=token)


# Convenience type alias for router signatures
CurrentUser = Annotated[UserClaims, Depends(require_user)]
