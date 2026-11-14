"""POST /approvals/{id}/approve|reject (session must own the case). Slice 7."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/approvals/{action_id}/approve")
def approve(action_id: str) -> None:
    raise NotImplementedError("slice 7")


@router.post("/approvals/{action_id}/reject")
def reject(action_id: str) -> None:
    raise NotImplementedError("slice 7")
