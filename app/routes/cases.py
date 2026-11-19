"""GET /cases/{id}: the case file JSON the Brain sees.

/cases/{id} accepts either admin basic auth or the case's own session.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.access import optional_case_id, require_admin
from app.brain.casefile import CaseNotFound, build_case_file

router = APIRouter()
_basic_optional = HTTPBasic(auto_error=False)


def case_or_admin(
    case_id: UUID,
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic_optional)],
) -> None:
    if optional_case_id(request) == str(case_id):
        return
    if credentials is None:
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    require_admin(credentials)


@router.get("/cases/{case_id}", dependencies=[Depends(case_or_admin)])
def get_case_file(case_id: UUID) -> dict:
    try:
        return build_case_file(case_id)
    except CaseNotFound:
        raise HTTPException(404, "case not found") from None
