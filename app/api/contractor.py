from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from decimal import Decimal
from datetime import date
from app.core.dependencies import get_current_active_user, require_roles
from app.models.user import User, UserRole
from app.db.session import get_db_session
from app.models.contractor import Contractor, ContractorProject
from app.models.project import Project, ProjectMember
from app.models.expense import Expense
from app.models.owner import OwnerTransaction
from app.schemas.contractor import ContractorCreate, ContractorUpdate, ContractorOut
from app.models.invoice import Invoice
from app.core.logger import logger
from app.utils.common import assert_project_access, generate_business_id , validate_contractor_access
from app.utils.helpers import NotFoundError, PermissionDeniedError
from app.utils.pagination import PaginationParams
from sqlalchemy.exc import IntegrityError


CONTRACTOR_CREATE_ROLES = [UserRole.ADMIN, UserRole.PROJECT_MANAGER]

CONTRACTOR_READ_ROLES = [
    UserRole.ADMIN,
    UserRole.PROJECT_MANAGER,
    UserRole.ACCOUNTANT,
]

CONTRACTOR_PAYMENT_ROLES = [
    UserRole.ADMIN,
    UserRole.ACCOUNTANT,
]

CONTRACTOR_DELETE_ROLES = [UserRole.ADMIN]

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
        payment_pending=(
            Decimal(contractor.total_work_assigned or 0)
            - Decimal(contractor.payment_given or 0)
        ),
    )


@router.post("", response_model=ContractorOut)
async def create_contractor(
    data: ContractorCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_CREATE_ROLES)),
):
    logger.info(f"Creating contractor name={data.name}")

    payload = data.model_dump()

    for _ in range(3):  # retry for race condition safety
        try:
            payload["contractor_id"] = await generate_business_id(
                db, Contractor, "contractor_id", "CNT"
            )

            contractor = Contractor(**payload)

            db.add(contractor)
            await db.flush()
            await db.refresh(contractor)

            return build_response(contractor)

        except IntegrityError:
            await db.rollback()

    raise Exception("Failed to create contractor with unique ID")


@router.get("/pending-report")
async def pending_report(
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    params = PaginationParams(limit=limit, offset=offset).normalized()

    query = select(Contractor)

    if current_user.role != UserRole.ADMIN:
        query = (
            query.join(ContractorProject)
            .join(ProjectMember)
            .where(ProjectMember.user_id == current_user.id)
            .distinct()
        )

    query = query.limit(params.limit).offset(params.offset)

    result = await db.execute(query)
    contractors = result.scalars().all()

    output = []

    for c in contractors:
        pending = (c.total_work_assigned or 0) - (c.payment_given or 0)

        if pending > 0:
            output.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "pending": pending,
                }
            )

    return output


@router.get("", response_model=list[ContractorOut])
async def list_contractors(
    limit: int = 20,
    offset: int = 0,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    params = PaginationParams(limit=limit, offset=offset, search=search).normalized()

    query = select(Contractor)

    if current_user.role != UserRole.ADMIN:
        query = (
            query.join(ContractorProject)
            .join(ProjectMember)
            .where(ProjectMember.user_id == current_user.id)
            .distinct()
        )

    if params.search:
        query = query.where(Contractor.name.ilike(f"%{params.search}%"))

    query = query.limit(params.limit).offset(params.offset)

    result = await db.execute(query)
    contractors = result.scalars().all()

    return [build_response(c) for c in contractors]


@router.get("/{contractor_id}", response_model=ContractorOut)
async def get_contractor(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        logger.warning(f"Contractor not found id={contractor_id}")
        raise NotFoundError("Contractor not found")

    await validate_contractor_access(db, contractor_id, current_user)

    return build_response(contractor)


@router.put("/{contractor_id}", response_model=ContractorOut)
async def update_contractor(
    contractor_id: int,
    data: ContractorUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User =Depends(require_roles(CONTRACTOR_CREATE_ROLES)),
):
    logger.info(f"Updating contractor id={contractor_id}")

    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        logger.warning(f"Contractor not found for update id={contractor_id}")
        raise NotFoundError("Contractor not found")

    update_data = data.model_dump(exclude_unset=True)

    if not update_data:
        logger.warning(f"No fields provided for update contractor_id={contractor_id}")

    for key, value in update_data.items():
        setattr(contractor, key, value)

    try:
        await db.flush()
    except Exception:
        await db.rollback()
        logger.exception(f"Contractor update failed id={contractor_id}")
        raise

    await db.refresh(contractor)

    logger.info(f"Contractor updated id={contractor_id}")

    return build_response(contractor)


@router.delete("/{contractor_id}")
async def delete_contractor(
    contractor_id: int, db: AsyncSession = Depends(get_db_session),
    current_user: User =Depends(require_roles(CONTRACTOR_DELETE_ROLES)),
):
    logger.info(f"Deleting contractor id={contractor_id}")

    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        logger.warning(f"Contractor not found for delete id={contractor_id}")
        raise NotFoundError("Contractor not found")

    if (contractor.payment_given or 0) > 0:
        logger.warning(
            f"Delete blocked: contractor has payment history id={contractor_id}"
        )
        raise HTTPException(
            status_code=400, detail="Cannot delete contractor with payment history"
        )

    await db.delete(contractor)
    await db.flush()

    logger.info(f"Contractor deleted id={contractor_id}")

    return {"message": "Deleted successfully"}


@router.post("/{contractor_id}/assign-project/{project_id}")
async def assign_project(
    contractor_id: int,
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_CREATE_ROLES)),
):
    logger.info(f"Assigning contractor={contractor_id} to project={project_id}")

    contractor = await db.get(Contractor, contractor_id)
    project = await db.get(Project, project_id)

    if not contractor or not project:
        logger.warning(
            f"Invalid contractor/project contractor_id={contractor_id} project_id={project_id}"
        )
        raise NotFoundError("Contractor/Project not found")
    
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    result = await db.execute(
        select(ContractorProject).where(
            ContractorProject.contractor_id == contractor_id,
            ContractorProject.project_id == project_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        logger.warning(
            f"Contractor already assigned contractor_id={contractor_id} project_id={project_id}"
        )
        raise HTTPException(status_code=400, detail="Already assigned")

    mapping = ContractorProject(contractor_id=contractor_id, project_id=project_id)
    db.add(mapping)
    await db.flush()

    logger.info(
        f"Contractor assigned contractor_id={contractor_id} project_id={project_id}"
    )

    return {"message": "Assigned successfully"}


@router.get("/{contractor_id}/payments")
async def contractor_payments(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    contractor = await db.get(Contractor, contractor_id)

    if not contractor:
        logger.warning(f"Contractor not found for delete id={contractor_id}")
        raise NotFoundError("Contractor not found")

    await validate_contractor_access(db, contractor_id, current_user)

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
    current_user: User = Depends(require_roles(CONTRACTOR_PAYMENT_ROLES)),
):
    logger.info(
        f"Contractor payment initiated contractor_id={contractor_id} amount={amount}"
    )

    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        logger.warning(f"Contractor not found for payment id={contractor_id}")
        raise NotFoundError("Contractor not found")

    await validate_contractor_access(db, contractor_id, current_user)
    await assert_project_access(db, project_id=project_id, current_user=current_user)

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")

    total_work = Decimal(contractor.total_work_assigned or 0)
    paid = Decimal(contractor.payment_given or 0)

    if paid + amount > total_work:
        logger.warning(f"Payment exceeds total work contractor_id={contractor_id}")
        raise HTTPException(status_code=400, detail="Payment exceeds total work amount")

    contractor.payment_given = paid + amount

    project = await db.get(Project, project_id)
    if not project:
        logger.warning(
            f"Project not found for contractor payment project_id={project_id}"
        )
        raise NotFoundError("Project not found")

    expense = Expense(
        project_id=project_id,
        category="Contractor",
        description=f"Payment to contractor - {contractor.id}",
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

    await db.flush()

    logger.info(
        f"Contractor payment completed contractor_id={contractor_id} amount={amount}"
    )

    return {
        "message": "Payment recorded successfully",
        "paid_total": float(contractor.payment_given),
    }

@router.get("/{contractor_id}/projects")
async def contractor_projects(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        raise NotFoundError("Contractor not found")

    await validate_contractor_access(db, contractor_id, current_user)

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

@router.get("/{contractor_id}/bills")
async def contractor_bills(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        raise NotFoundError("Contractor not found")

    await validate_contractor_access(db, contractor_id, current_user)

    result = await db.execute(
        select(Invoice).where(
            Invoice.type == "contractor",
            Invoice.reference_id == contractor_id,
        )
    )

    invoices = result.scalars().all()

    return [
        {
            "bill_id": inv.id,
            "amount": float(inv.total_amount),
            "status": inv.status,
            "date": inv.created_at,
        }
        for inv in invoices
    ]

@router.get("/{contractor_id}/performance")
async def contractor_performance(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        raise NotFoundError("Contractor not found")

    await validate_contractor_access(db, contractor_id, current_user)

    total_work = float(contractor.total_work_assigned or 0)
    paid = float(contractor.payment_given or 0)

    efficiency = (paid / total_work * 100) if total_work > 0 else 0

    return {
        "contractor_id": contractor.id,
        "total_work": total_work,
        "payment_given": paid,
        "efficiency_percent": round(efficiency, 2),
        "status": (
            "Good" if efficiency > 70 else "Average" if efficiency > 40 else "Low"
        ),
    }


@router.get("/{contractor_id}/ledger")
async def contractor_ledger(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        raise NotFoundError("Contractor not found")

    await validate_contractor_access(db, contractor_id, current_user)

    expenses = await db.execute(
        select(Expense).where(
            Expense.category == "Contractor",
            Expense.description.contains(contractor.contractor_id),
        )
    )

    invoices = await db.execute(
        select(Invoice).where(
            Invoice.type == "contractor",
            Invoice.reference_id == contractor_id,
        )
    )

    ledger = []

    for exp in expenses.scalars():
        ledger.append(
            {
                "type": "DEBIT",
                "amount": float(exp.amount),
                "date": exp.expense_date,
                "description": exp.description,
            }
        )

    for inv in invoices.scalars():
        ledger.append(
            {
                "type": "CREDIT",
                "amount": float(inv.total_amount),
                "date": inv.created_at,
                "description": "Contractor Bill",
            }
        )

    ledger.sort(key=lambda x: x["date"])

    return ledger

@router.get("/{contractor_id}/work-summary")
async def contractor_work_summary(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    contractor = await db.get(Contractor, contractor_id)
    if not contractor:
        raise NotFoundError("Contractor not found")

    await validate_contractor_access(db, contractor_id, current_user)

    total_work = float(contractor.total_work_assigned or 0)
    paid = float(contractor.payment_given or 0)

    completion = (paid / total_work * 100) if total_work > 0 else 0

    return {
        "contractor_id": contractor.id,
        "total_work_assigned": total_work,
        "work_completed_percent": round(completion, 2),
        "remaining_work_percent": round(100 - completion, 2),
    }


@router.get("/{contractor_id}/dashboard")
async def contractor_dashboard(
    contractor_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(CONTRACTOR_READ_ROLES)),
):
    from app.models.billing import RABill
    from app.utils.common import validate_contractor_access
    from sqlalchemy import func

    #  access control
    await validate_contractor_access(db, contractor_id, current_user)

    # ======================
    # AMOUNTS
    # ======================
    total = await db.scalar(
        select(func.sum(RABill.total_amount)).where(
            RABill.contractor_id == contractor_id
        )
    ) or 0

    pending = await db.scalar(
        select(func.sum(RABill.total_amount)).where(
            RABill.contractor_id == contractor_id,
            RABill.status.in_(["Draft", "Submitted"]),
        )
    ) or 0

    approved = await db.scalar(
        select(func.sum(RABill.total_amount)).where(
            RABill.contractor_id == contractor_id,
            RABill.status == "Approved",
        )
    ) or 0

    paid = await db.scalar(
        select(func.sum(RABill.total_amount)).where(
            RABill.contractor_id == contractor_id,
            RABill.status == "Paid",
        )
    ) or 0

    # ======================
    #  NUMBER OF BILLS
    # ======================
    total_bills = await db.scalar(
        select(func.count()).where(
            RABill.contractor_id == contractor_id
        )
    ) or 0

    pending_bills = await db.scalar(
        select(func.count()).where(
            RABill.contractor_id == contractor_id,
            RABill.status.in_(["Draft", "Submitted"]),
        )
    ) or 0

    approved_bills = await db.scalar(
        select(func.count()).where(
            RABill.contractor_id == contractor_id,
            RABill.status == "Approved",
        )
    ) or 0

    paid_bills = await db.scalar(
        select(func.count()).where(
            RABill.contractor_id == contractor_id,
            RABill.status == "Paid",
        )
    ) or 0

    # ======================
    # LAST PAYMENT DATE
    # ======================
    last_payment_date = await db.scalar(
        select(func.max(RABill.bill_date)).where(
            RABill.contractor_id == contractor_id,
            RABill.status == "Paid",
        )
    )

    # ======================
    #  OVERDUE BILLS
    # ======================
    from datetime import date, timedelta

    overdue_threshold = date.today() - timedelta(days=30)

    overdue_amount = await db.scalar(
        select(func.sum(RABill.total_amount)).where(
            RABill.contractor_id == contractor_id,
            RABill.status.in_(["Submitted", "Approved"]),
            RABill.bill_date < overdue_threshold,
        )
    ) or 0

    overdue_count = await db.scalar(
        select(func.count()).where(
            RABill.contractor_id == contractor_id,
            RABill.status.in_(["Submitted", "Approved"]),
            RABill.bill_date < overdue_threshold,
        )
    ) or 0

    return {
        # 🔹 basic
        "contractor_id": contractor_id,

        # amounts
        "total_amount": float(total),
        "pending_amount": float(pending),
        "approved_amount": float(approved),
        "paid_amount": float(paid),

        #  counts
        "total_bills": total_bills,
        "pending_bills": pending_bills,
        "approved_bills": approved_bills,
        "paid_bills": paid_bills,

        #  last payment
        "last_payment_date": last_payment_date,

        #  overdue
        "overdue_amount": float(overdue_amount),
        "overdue_count": overdue_count,
    }