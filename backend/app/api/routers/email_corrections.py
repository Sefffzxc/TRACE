"""Scoped administrator initiation; public ownership proof of the new inbox."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, limiter
from app.db.session import get_db
from app.models.staff import ROLE_ADMIN, ROLE_SUPER_ADMIN, Staff
from app.schemas.email_corrections import EmailCorrectionRequest, EmailCorrectionVerify
from app.services.email_corrections import request_email_correction, verify_email_correction


router = APIRouter(tags=["Email corrections"])
limit_initiation = limiter("email-correction-request", 30, 60)
limit_verification = limiter("email-correction-verify", 60, 60)


@router.post("/api/staff/{staff_id}/email-correction", status_code=202,
             dependencies=[Depends(limit_initiation)])
async def correct_teacher_email(
    staff_id: UUID, payload: EmailCorrectionRequest, request: Request,
    current: Staff = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    if current.role != ROLE_ADMIN or current.school_id is None:
        raise HTTPException(403, detail="Only a school's principal can correct teacher emails.")
    return await request_email_correction(db, current.id, staff_id, payload, request)


@router.post("/api/schools/{school_id}/principal-email-correction", status_code=202,
             dependencies=[Depends(limit_initiation)])
async def correct_principal_email(
    school_id: int, payload: EmailCorrectionRequest, request: Request,
    current: Staff = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    if current.role != ROLE_SUPER_ADMIN or current.school_id is not None:
        raise HTTPException(403, detail="Only the system operator can correct principal emails.")
    principals = (await db.scalars(select(Staff.id).where(
        Staff.school_id == school_id, Staff.role == ROLE_ADMIN,
    ))).all()
    if not principals:
        raise HTTPException(404, detail="Principal not found for this school.")
    if len(principals) != 1:
        raise HTTPException(409, detail={"code": "principal_ambiguous", "message": "This school has more than one principal account. Resolve the accounts before correcting its email."})
    return await request_email_correction(db, current.id, principals[0], payload, request, school_id=school_id)


@router.post("/api/auth/email-correction/verify", dependencies=[Depends(limit_verification)])
async def confirm_email_correction(
    payload: EmailCorrectionVerify, request: Request, db: AsyncSession = Depends(get_db),
):
    return await verify_email_correction(db, payload, request)

