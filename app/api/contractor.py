from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from decimal import Decimal
from datetime import date
from app.core.dependencies import get_current_active_user, require_roles
from app.models.user import User, UserRole
from app.db.session import get_db_session
from app.models.contractor import Contractor, ContractorProject
from app.models.project import Project
from app.models.expense import Expense
from app.models.owner import OwnerTransaction
from app.schemas.contractor import ContractorCreate, ContractorUpdate, ContractorOut
from app.models.invoice import Invoice


router = APIRouter(prefix="/contractors", tags=["Contractors"])


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
        payment_pending=(contractor.total_work_assigned or 0)
        - (contractor.payment_given or 0),
    )


@router.post("", response_model=ContractorOut)
async def create_contractor(
    data: ContractorCreate, db: AsyncSession = Depends(get_db_session)
):
    contractor = Contractor(**data.model_dump())

    db.add(contractor)
    await db.commit()
    await db.refresh(contractor)

    return build_response(contractor)


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

@router.get("", response_model=list[ContractorOut])
async def list_contractors(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Contractor))
    contractors = result.scalars().all()

    return [build_response(c) for c in contractors]


@router.get("/{contractor_id}", response_model=ContractorOut)
async def get_contractor(
    contractor_id: int, db: AsyncSession = Depends(get_db_session)
):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    return build_response(contractor)


@router.put("/{contractor_id}", response_model=ContractorOut)
async def update_contractor(
    contractor_id: int,
    data: ContractorUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    update_data = data.model_dump(exclude_unset=True)

    if "payment_given" in update_data:
        update_data.pop("payment_given")

    for key, value in update_data.items():
        setattr(contractor, key, value)

    await db.commit()
    await db.refresh(contractor)

    return build_response(contractor)


@router.delete("/{contractor_id}")
async def delete_contractor(
    contractor_id: int, db: AsyncSession = Depends(get_db_session)
):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    if (contractor.payment_given or 0) > 0:
        raise HTTPException(
            status_code=400, detail="Cannot delete contractor with payment history"
        )

    await db.delete(contractor)
    await db.commit()

    return {"message": "Deleted successfully"}


@router.post("/{contractor_id}/assign-project/{project_id}")
async def assign_project(
    contractor_id: int, project_id: int, db: AsyncSession = Depends(get_db_session)
):
    contractor = await db.get(Contractor, contractor_id)
    project = await db.get(Project, project_id)

    if not contractor or not project:
        raise HTTPException(status_code=404, detail="Invalid contractor or project")

    result = await db.execute(
        select(ContractorProject).where(
            ContractorProject.contractor_id == contractor_id,
            ContractorProject.project_id == project_id,
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
async def contractor_payments(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    total = float(contractor.total_work_assigned or 0)
    paid = float(contractor.payment_given or 0)
    pending = total - paid

    return {
        "contractor_id": contractor.id,
        "total_work": total,
        "payment_given": paid,
        "payment_pending": pending,
    }



@router.post("/{contractor_id}/pay")
async def pay_contractor(
    contractor_id: int,
    project_id: int,
    amount: Decimal,
    db: AsyncSession = Depends(get_db_session),
):
    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    total_work = Decimal(contractor.total_work_assigned or 0)
    paid = Decimal(contractor.payment_given or 0)

    if paid + amount > total_work:
        raise HTTPException(status_code=400, detail="Payment exceeds total work amount")

    contractor.payment_given = paid + amount

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    expense = Expense(
        project_id=project_id,
        category="Contractor",
        description=f"Payment to contractor - {contractor.name}",
        amount=amount,
        expense_date=date.today(),
        payment_mode="bank",
    )
    db.add(expense)
    await db.flush()

    owner_txn = OwnerTransaction(
        owner_id=project.owner_id,
        project_id=project_id,
        type="debit",
        amount=amount,
        reference_type="contractor",
        reference_id=expense.id,
        description=f"Contractor payment - {contractor.name}",
    )
    db.add(owner_txn)

    await db.commit()

    return {
        "message": "Payment recorded successfully",
        "paid_total": float(contractor.payment_given),
    }


@router.get("/{contractor_id}/projects")
async def contractor_projects(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    result = await db.execute(
        select(Project)
        .join(ContractorProject, ContractorProject.project_id == Project.id)
        .where(ContractorProject.contractor_id == contractor_id)
    )

    projects = result.scalars().all()

    return [
        {
            "project_id": p.id,
            "project_name": p.project_name,
            "status": p.status,
        }
        for p in projects
    ]


@router.get("/{contractor_id}/invoices")
async def contractor_invoices(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        raise HTTPException(status_code=404, detail="Contractor not found")

    result = await db.execute(
        select(Invoice).where(
            Invoice.type == "contractor",
            Invoice.reference_id == contractor_id,
        )
    )

    invoices = result.scalars().all()

    return [
        {
            "invoice_id": inv.id,
            "amount": float(inv.total_amount),
            "status": inv.status,
            "created_at": inv.created_at,
        }
        for inv in invoices
    ]