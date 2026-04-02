from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.ra_bill import RABill
from app.models.project import Project
from app.models.contractor import Contractor
from app.models.owner import OwnerTransaction
from app.schemas.ra_bill import RABillCreate, RABillUpdate, RABillOut
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/ra-bills", tags=["RA Bills"])


# -------------------------
# CREATE
# -------------------------
@router.post("", response_model=RABillOut)
async def create_ra_bill(
    payload: RABillCreate,
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, payload.project_id)
    contractor = await db.get(Contractor, payload.contractor_id)

    if not project:
        raise NotFoundError("Project not found")

    if not contractor:
        raise NotFoundError("Contractor not found")

    gross = payload.quantity * payload.rate
    net = gross - payload.deductions
    gst_amount = (net * payload.gst_percent) / 100
    total = net + gst_amount

    obj = RABill(
        **payload.model_dump(),
        gross_amount=gross,
        net_amount=net,
        total_amount=total,
    )

    db.add(obj)
    await db.flush()

    # ✅ OWNER LEDGER (DEBIT)
    owner_txn = OwnerTransaction(
        owner_id=project.owner_id,
        project_id=project.id,
        type="debit",
        amount=total,
        reference_type="ra_bill",
        reference_id=obj.id,
        description="Contractor RA Bill",
    )

    db.add(owner_txn)

    await db.commit()
    await db.refresh(obj)

    return RABillOut.model_validate(obj)


# -------------------------
# LIST
# -------------------------
@router.get("", response_model=list[RABillOut])
async def list_ra_bills(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(RABill))
    rows = result.scalars().all()
    return [RABillOut.model_validate(r) for r in rows]


# -------------------------
# GET
# -------------------------
@router.get("/{id}", response_model=RABillOut)
async def get_ra_bill(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(RABill, id)

    if not obj:
        raise NotFoundError("RA Bill not found")

    return RABillOut.model_validate(obj)


# -------------------------
# UPDATE
# -------------------------
@router.put("/{id}", response_model=RABillOut)
async def update_ra_bill(
    id: int,
    payload: RABillUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(RABill, id)

    if not obj:
        raise NotFoundError("RA Bill not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    # recalc
    gross = obj.quantity * obj.rate
    net = gross - (obj.deductions or 0)
    gst_amount = (net * (obj.gst_percent or 0)) / 100
    total = net + gst_amount

    obj.gross_amount = gross
    obj.net_amount = net
    obj.total_amount = total

    await db.commit()
    await db.refresh(obj)

    return RABillOut.model_validate(obj)


# -------------------------
# DELETE
# -------------------------
@router.delete("/{id}", status_code=204)
async def delete_ra_bill(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(RABill, id)

    if not obj:
        raise NotFoundError("RA Bill not found")

    await db.delete(obj)
    await db.commit()

    return None


# -------------------------
# BY CONTRACTOR
# -------------------------
@router.get("/contractor/{contractor_id}")
async def bills_by_contractor(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(RABill).where(RABill.contractor_id == contractor_id)
    )
    rows = result.scalars().all()

    return [RABillOut.model_validate(r) for r in rows]