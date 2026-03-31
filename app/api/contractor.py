from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.dependencies import get_current_active_user, require_roles
from app.models.user import User, UserRole

from app.db.session import get_db_session
from app.models.contractor import Contractor, ContractorProject
from app.models.project import Project
from app.schemas.contractor import ContractorCreate, ContractorUpdate, ContractorOut


router = APIRouter(prefix="/api/v1/contractors", tags=["Contractors"])


def build_response(contractor: Contractor) -> ContractorOut:
    return ContractorOut(
        id=contractor.id,
        contractor_id=contractor.contractor_id,
        name=contractor.name,
        work_type=contractor.work_type,
        contact_number=contractor.contact_number,
        gst_number=contractor.gst_number,
        rate_type=contractor.rate_type,
        total_work_assigned=contractor.total_work_assigned,
        payment_given=contractor.payment_given,
        bank_details=contractor.bank_details,
        payment_pending=(contractor.total_work_assigned or 0) - (contractor.payment_given or 0),
    )


@router.post("", response_model=ContractorOut)
async def create_contractor(data: ContractorCreate, db: AsyncSession = Depends(get_db_session)):
    contractor = Contractor(**data.dict())

    db.add(contractor)
    await db.commit()
    await db.refresh(contractor)

    return build_response(contractor)


@router.get("", response_model=list[ContractorOut])
async def list_contractors(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Contractor))
    contractors = result.scalars().all()

    return [build_response(c) for c in contractors]


@router.get("/{contractor_id}", response_model=ContractorOut)
async def get_contractor(contractor_id: int, db: AsyncSession = Depends(get_db_session)):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    return build_response(contractor)


@router.put("/{contractor_id}", response_model=ContractorOut)
async def update_contractor(contractor_id: int, data: ContractorUpdate, db: AsyncSession = Depends(get_db_session)):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    for key, value in data.dict(exclude_unset=True).items():
        setattr(contractor, key, value)

    await db.commit()
    await db.refresh(contractor)

    return build_response(contractor)


@router.delete("/{contractor_id}")
async def delete_contractor(contractor_id: int, db: AsyncSession = Depends(get_db_session)):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    await db.delete(contractor)
    await db.commit()

    return {"message": "Deleted successfully"}


@router.post("/{contractor_id}/assign-project/{project_id}")
async def assign_project(contractor_id: int, project_id: int, db: AsyncSession = Depends(get_db_session)):
    contractor = await db.get(Contractor, contractor_id)
    project = await db.get(Project, project_id)

    if not contractor or not project:
        raise HTTPException(status_code=404, detail="Invalid contractor or project")

    # check duplicate
    result = await db.execute(
        select(ContractorProject).where(
            ContractorProject.contractor_id == contractor_id,
            ContractorProject.project_id == project_id
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(status_code=400, detail="Already assigned")

    mapping = ContractorProject(contractor_id=contractor_id, project_id=project_id)
    db.add(mapping)
    await db.commit()

    return {"message": "Assigned successfully"}


@router.get("/{contractor_id}/payments")
async def contractor_payments(contractor_id: int, db: AsyncSession = Depends(get_db_session)):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    return {
        "contractor_id": contractor.id,
        "name": contractor.name,
        "total_work": contractor.total_work_assigned,
        "paid": contractor.payment_given,
        "pending": (contractor.total_work_assigned or 0) - (contractor.payment_given or 0),
    }


@router.get("/pending-report")
async def pending_report(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Contractor))
    contractors = result.scalars().all()

    return [
        {
            "id": c.id,
            "name": c.name,
            "pending": (c.total_work_assigned or 0) - (c.payment_given or 0),
        }
        for c in contractors
        if (c.total_work_assigned or 0) - (c.payment_given or 0) > 0
    ]