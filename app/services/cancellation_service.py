import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, Any, Tuple

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models.cancellation import OTPCancellationRequest
from app.models.material import (
    PurchaseOrder,
    Material,
    MaterialTransaction,
    MaterialLedger,
)
from app.models.inventory import (
    GoodsReceiptNote,
    GoodsReceiptNoteItem,
    StockBatch,
    MaterialIssue,
    MaterialIssueAllocation,
    ProjectMaterialValuation,
)
from app.models.daily_stock import DailyStockReconciliation
from app.models.purchase_order import PurchaseOrderItem
from app.core.enums import TransactionType
from app.utils.helpers import AppError, NotFoundError


def _get_otp_secret() -> str:
    """Returns configured cryptographic secret for OTP HMAC hashing."""
    secret = getattr(settings, "SECRET_KEY", None) or getattr(settings, "JWT_SECRET", None)
    if not secret or not str(secret).strip():
        raise AppError(status_code=500, message="OTP cryptographic secret is not configured.")
    return str(secret)


async def _ensure_po_cancellable(db: AsyncSession, po: PurchaseOrder) -> None:
    """Blocks PO cancellation if already cancelled/received or if any posted GRN / received qty exists."""
    if po.status in ("CANCELLED", "RECEIVED", "PARTIALLY_RECEIVED"):
        raise AppError(
            status_code=400,
            message=f"Cannot cancel PO in status '{po.status}'. Reverse/cancel related inward GRN(s) first.",
        )

    posted_grn_stmt = (
        select(GoodsReceiptNote.id)
        .where(
            and_(
                GoodsReceiptNote.purchase_order_id == po.id,
                GoodsReceiptNote.status == "POSTED",
            )
        )
        .limit(1)
    )
    if (await db.execute(posted_grn_stmt)).scalar_one_or_none() is not None:
        raise AppError(
            status_code=400,
            message="Cannot cancel PO with a POSTED GRN. Cancel/reverse the related GRN(s) first.",
        )

    for item in (po.items or []):
        if not item.is_deleted and (item.received_quantity or Decimal("0.000")) > Decimal("0.000"):
            raise AppError(
                status_code=400,
                message="Cannot cancel PO with received quantities. Cancel/reverse the related GRN(s) first.",
            )


async def _ensure_grn_batches_intact(db: AsyncSession, grn: GoodsReceiptNote, lock: bool = False) -> list:
    """Ensures batches created by a posted GRN have not been partially/wholly consumed or transferred."""
    batch_stmt = select(StockBatch).where(StockBatch.grn_id == grn.id)
    if lock:
        batch_stmt = batch_stmt.with_for_update()
    batches = (await db.execute(batch_stmt)).scalars().all()
    for b in batches:
        if (b.current_balance or Decimal("0.000")) < (b.initial_quantity or Decimal("0.000")):
            raise AppError(
                status_code=400,
                message=(
                    f"Cannot cancel GRN {grn.grn_number}: stock has already been consumed "
                    f"from batch {b.batch_number} (remaining: {b.current_balance}, initial: {b.initial_quantity})."
                ),
            )
    return batches


def _hash_otp(otp: str, secret_key: str) -> str:
    """Computes a keyed HMAC-SHA256 hash of the OTP."""
    return hmac.new(secret_key.encode("utf-8"), otp.encode("utf-8"), hashlib.sha256).hexdigest()


async def request_cancellation_otp(
    db: AsyncSession,
    company_id: int,
    user_id: int,
    document_type: str,  # PO, GRN, MIN, DAILY_STOCK
    document_id: int,
    reason: str,
) -> Tuple[OTPCancellationRequest, str]:
    """
    Maker Step: Requests a cancellation OTP for a specific document.
    Returns (cancellation_request, raw_6_digit_otp).
    In a live production environment, the raw OTP is dispatched via SMS/Email to the authorized checker.
    """
    # 1. Validate Document Existence and Status
    doc_number = ""
    if document_type == "PO":
        po_stmt = (
            select(PurchaseOrder)
            .options(selectinload(PurchaseOrder.items))
            .where(
                and_(
                    PurchaseOrder.id == document_id,
                    PurchaseOrder.company_id == company_id,
                )
            )
        )
        po = (await db.execute(po_stmt)).scalar_one_or_none()
        if not po:
            raise NotFoundError(f"Purchase Order {document_id} not found")
        await _ensure_po_cancellable(db, po)
        doc_number = po.po_number or f"PO-{po.id}"

    elif document_type == "GRN":
        grn_stmt = (
            select(GoodsReceiptNote)
            .options(selectinload(GoodsReceiptNote.items))
            .where(GoodsReceiptNote.id == document_id)
        )
        grn = (await db.execute(grn_stmt)).scalar_one_or_none()
        if not grn:
            raise NotFoundError(f"Goods Receipt Note {document_id} not found")
        if grn.status == "CANCELLED":
            raise AppError(status_code=400, message="GRN is already cancelled")
        # Area C: Guard against negative stock and batch depletion before initiating cancellation
        if grn.status == "POSTED":
            for item in grn.items:
                if item.accepted_quantity > Decimal("0.000"):
                    mat = await db.get(Material, item.material_id)
                    if mat and mat.remaining_stock < item.accepted_quantity:
                        raise AppError(
                            status_code=400,
                            message=f"Cannot cancel GRN {grn.grn_number}: stock has already been consumed (remaining: {mat.remaining_stock}, required for reversal: {item.accepted_quantity})",
                        )
            await _ensure_grn_batches_intact(db, grn, lock=False)
        doc_number = grn.grn_number

    elif document_type == "MIN":
        issue = await db.get(MaterialIssue, document_id)
        if not issue:
            raise NotFoundError(f"Material Issue {document_id} not found")
        if issue.status == "CANCELLED":
            raise AppError(status_code=400, message="Material Issue is already cancelled")
        doc_number = issue.issue_number

    elif document_type == "DAILY_STOCK":
        dsr = await db.get(DailyStockReconciliation, document_id)
        if not dsr:
            raise NotFoundError(f"Daily Stock Reconciliation {document_id} not found")
        if dsr.status == "CANCELLED":
            raise AppError(status_code=400, message="Daily Stock Reconciliation is already cancelled")
        doc_number = dsr.reconciliation_number

    else:
        raise AppError(status_code=400, message=f"Invalid document type '{document_type}'")

    # 2. Check existing active pending request
    stmt_pending = select(OTPCancellationRequest).where(
        and_(
            OTPCancellationRequest.company_id == company_id,
            OTPCancellationRequest.document_type == document_type,
            OTPCancellationRequest.document_id == document_id,
            OTPCancellationRequest.status == "PENDING",
            OTPCancellationRequest.expires_at > datetime.utcnow(),
        )
    )
    existing_req = (await db.execute(stmt_pending)).scalar_one_or_none()
    if existing_req:
        existing_req.status = "EXPIRED"

    # 3. Generate Cryptographically Secure 6-digit OTP
    raw_otp = f"{secrets.randbelow(900000) + 100000}"
    secret = _get_otp_secret()
    otp_hash = _hash_otp(raw_otp, secret)

    expires_at = datetime.utcnow() + timedelta(minutes=15)

    req = OTPCancellationRequest(
        company_id=company_id,
        document_type=document_type,
        document_id=document_id,
        document_number=doc_number,
        requested_by_id=user_id,
        reason=reason,
        otp_hash=otp_hash,
        expires_at=expires_at,
        attempts=0,
        max_attempts=3,
        status="PENDING",
    )
    db.add(req)
    await db.flush()

    # Area B: Dispatch notification to company stakeholders (strictly excluding maker from receiving OTP)
    try:
        from app.services.notification_service import notify_company_stakeholders, create_notification
        # 1. Independent checkers receive the authorization OTP
        await notify_company_stakeholders(
            db=db,
            company_id=company_id,
            title=f"Cancellation OTP Requested: {document_type} {doc_number}",
            message=f"Cancellation OTP requested for {document_type} {doc_number} by user {user_id}. Reason: {reason}. Authorization OTP: {raw_otp} (valid for 15 minutes, 3 attempts max).",
            type="warning",
            link=f"/cancellations/requests/{req.id}",
            actor_id=user_id,
            exclude_actor=True,
        )
        # 2. Requester (maker) receives a sanitized confirmation without the OTP
        await create_notification(
            db=db,
            user_id=user_id,
            title=f"Cancellation Requested: {document_type} {doc_number}",
            message=f"Cancellation request for {document_type} {doc_number} has been submitted for approval. An authorization OTP was sent to independent checkers.",
            type="info",
            link=f"/cancellations/requests/{req.id}",
        )
    except Exception:
        pass

    await db.commit()
    await db.refresh(req)

    return req, raw_otp


async def verify_and_execute_cancellation(
    db: AsyncSession,
    company_id: int,
    user_id: int,
    request_id: int,
    otp: str,
) -> OTPCancellationRequest:
    """
    Checker Step: Verifies OTP and executes atomic document cancellation and stock reversal.
    Strict 2-Person rule enforced: user_id (checker) != requested_by_id (maker).
    """
    stmt = (
        select(OTPCancellationRequest)
        .where(
            and_(
                OTPCancellationRequest.id == request_id,
                OTPCancellationRequest.company_id == company_id,
            )
        )
        .with_for_update()
    )
    req = (await db.execute(stmt)).scalar_one_or_none()
    if not req:
        raise NotFoundError(f"Cancellation request {request_id} not found")

    # 1. 2-Person Maker-Checker Rule
    if req.requested_by_id == user_id:
        raise AppError(status_code=403, message="Security Violation: Requester (Maker) cannot approve (Checker) their own cancellation")

    if req.status != "PENDING":
        raise AppError(status_code=400, message=f"Cancellation request is no longer pending (status: {req.status})")

    if datetime.utcnow() > req.expires_at:
        req.status = "EXPIRED"
        await db.commit()
        raise AppError(status_code=400, message="Cancellation OTP has expired. Please request a new OTP.")

    # 2. Check Attempts
    req.attempts += 1
    secret = _get_otp_secret()
    expected_hash = _hash_otp(otp.strip(), secret)

    if not hmac.compare_digest(req.otp_hash, expected_hash):
        if req.attempts >= req.max_attempts:
            req.status = "FAILED"
            await db.commit()
            raise AppError(status_code=400, message="Maximum OTP attempts exceeded. Request invalidated.")
        await db.commit()
        raise AppError(status_code=400, message=f"Invalid OTP. {req.max_attempts - req.attempts} attempt(s) remaining.")

    # 3. OTP is Valid -> Execute Document Cancellation and Stock Reversal
    if req.document_type == "PO":
        po_stmt = (
            select(PurchaseOrder)
            .options(selectinload(PurchaseOrder.items))
            .where(
                and_(
                    PurchaseOrder.id == req.document_id,
                    PurchaseOrder.company_id == req.company_id,
                )
            )
            .with_for_update()
        )
        po = (await db.execute(po_stmt)).scalar_one_or_none()
        if not po:
            raise NotFoundError(f"Purchase Order {req.document_id} not found")
        await _ensure_po_cancellable(db, po)
        po.status = "CANCELLED"
        for item in po.items:
            item.status = "CANCELLED"

    elif req.document_type == "GRN":
        grn_stmt = (
            select(GoodsReceiptNote)
            .options(selectinload(GoodsReceiptNote.items))
            .where(GoodsReceiptNote.id == req.document_id)
            .with_for_update()
        )
        grn = (await db.execute(grn_stmt)).scalar_one_or_none()
        if grn:
            if grn.status == "POSTED":
                batches = await _ensure_grn_batches_intact(db, grn, lock=True)
                for item in grn.items:
                    if item.accepted_quantity > Decimal("0.000"):
                        # Decrement material stock with negative stock guard
                        mat_stmt = select(Material).where(Material.id == item.material_id).with_for_update()
                        mat = (await db.execute(mat_stmt)).scalar_one_or_none()
                        if mat:
                            if mat.remaining_stock < item.accepted_quantity:
                                raise AppError(
                                    status_code=400,
                                    message=f"Cannot cancel GRN: material {mat.id} remaining stock {mat.remaining_stock} is less than accepted quantity {item.accepted_quantity}",
                                )
                            mat.remaining_stock -= item.accepted_quantity
                            mat.quantity_purchased -= item.accepted_quantity

                            # Compensating ledger and transaction
                            rev_amount = (item.accepted_quantity * item.unit_rate).quantize(Decimal("0.01"))
                            mat_tx = MaterialTransaction(
                                material_id=mat.id,
                                type=TransactionType.ADJUSTMENT,
                                quantity=-item.accepted_quantity,
                                rate=item.unit_rate,
                                total_amount=rev_amount,
                            )
                            db.add(mat_tx)

                            ledger = MaterialLedger(
                                material_id=mat.id,
                                type=TransactionType.ADJUSTMENT,
                                quantity=-item.accepted_quantity,
                                rate=item.unit_rate,
                                total_amount=rev_amount,
                            )
                            db.add(ledger)

                        # Deplete batches created by this GRN
                        for b in batches:
                            b.current_balance = Decimal("0.000")
                            b.status = "DEPLETED"

                        # Revert ProjectMaterialValuation (Area C)
                        val_stmt = (
                            select(ProjectMaterialValuation)
                            .where(
                                and_(
                                    ProjectMaterialValuation.project_id == grn.project_id,
                                    ProjectMaterialValuation.material_master_id == item.material_master_id,
                                )
                            )
                            .with_for_update()
                        )
                        val = (await db.execute(val_stmt)).scalar_one_or_none()
                        if val:
                            val.total_purchased_quantity = max(Decimal("0.000"), val.total_purchased_quantity - item.accepted_quantity)
                            val.total_purchased_amount = max(Decimal("0.00"), val.total_purchased_amount - rev_amount)
                            if val.total_purchased_quantity <= Decimal("0.000"):
                                val.total_purchased_quantity = Decimal("0.000")
                                val.total_purchased_amount = Decimal("0.00")
                                val.current_moving_weighted_average_rate = Decimal("0.00")
                            else:
                                val.current_moving_weighted_average_rate = (val.total_purchased_amount / val.total_purchased_quantity).quantize(Decimal("0.01"))
                            val.last_recalculated_at = datetime.utcnow()

                        # Revert PO item received quantity
                        if item.po_item_id:
                            po_item = await db.get(PurchaseOrderItem, item.po_item_id)
                            if po_item:
                                po_item.received_quantity = max(
                                    Decimal("0.000"),
                                    (po_item.received_quantity or Decimal("0.000")) - item.accepted_quantity,
                                )
                                if po_item.received_quantity <= Decimal("0.000"):
                                    po_item.status = "PENDING"
                                elif po_item.received_quantity < po_item.quantity:
                                    po_item.status = "PARTIALLY_RECEIVED"
                                else:
                                    po_item.status = "RECEIVED"

            grn.status = "CANCELLED"
            if grn.purchase_order_id:
                po_parent_stmt = (
                    select(PurchaseOrder)
                    .options(selectinload(PurchaseOrder.items))
                    .where(PurchaseOrder.id == grn.purchase_order_id)
                    .with_for_update()
                )
                po_parent = (await db.execute(po_parent_stmt)).scalar_one_or_none()
                if po_parent and po_parent.status != "CANCELLED":
                    active_items = [it for it in (po_parent.items or []) if not it.is_deleted]
                    if all((it.received_quantity or Decimal("0.000")) <= Decimal("0.000") for it in active_items):
                        po_parent.status = "APPROVED" if po_parent.approved_by_id else "CREATED"
                    elif active_items and all((it.received_quantity or Decimal("0.000")) >= it.quantity for it in active_items):
                        po_parent.status = "RECEIVED"
                    else:
                        po_parent.status = "PARTIALLY_RECEIVED"

    elif req.document_type == "MIN":
        issue_stmt = (
            select(MaterialIssue)
            .options(selectinload(MaterialIssue.allocations))
            .where(MaterialIssue.id == req.document_id)
            .with_for_update()
        )
        issue = (await db.execute(issue_stmt)).scalar_one_or_none()
        if issue and issue.status != "CANCELLED":
            # Re-credit site material stock
            mat_stmt = select(Material).where(Material.id == issue.material_id).with_for_update()
            mat = (await db.execute(mat_stmt)).scalar_one_or_none()
            if mat:
                mat.remaining_stock += issue.quantity
                mat.quantity_used -= issue.quantity

                rate = mat.purchase_rate or Decimal("0.00")
                rev_amount = (issue.quantity * rate).quantize(Decimal("0.01"))

                mat_tx = MaterialTransaction(
                    material_id=mat.id,
                    type=TransactionType.ADJUSTMENT,
                    quantity=issue.quantity,
                    rate=rate,
                    total_amount=rev_amount,
                )
                db.add(mat_tx)

                ledger = MaterialLedger(
                    material_id=mat.id,
                    type=TransactionType.ADJUSTMENT,
                    quantity=issue.quantity,
                    rate=rate,
                    total_amount=rev_amount,
                )
                db.add(ledger)

                # Revert ProjectMaterialValuation on MIN cancellation (Area C)
                val_stmt = (
                    select(ProjectMaterialValuation)
                    .where(
                        and_(
                            ProjectMaterialValuation.project_id == issue.project_id,
                            ProjectMaterialValuation.material_master_id == issue.material_master_id,
                        )
                    )
                    .with_for_update()
                )
                val = (await db.execute(val_stmt)).scalar_one_or_none()
                if val:
                    val.total_purchased_quantity += issue.quantity
                    val.total_purchased_amount += rev_amount
                    if val.total_purchased_quantity > Decimal("0.000"):
                        val.current_moving_weighted_average_rate = (val.total_purchased_amount / val.total_purchased_quantity).quantize(Decimal("0.01"))
                    val.last_recalculated_at = datetime.utcnow()

            # Restore batch current balances from allocations
            for alloc in issue.allocations:
                batch = await db.get(StockBatch, alloc.batch_id)
                if batch:
                    batch.current_balance += alloc.allocated_quantity
                    batch.status = "ACTIVE"

            issue.status = "CANCELLED"

    elif req.document_type == "DAILY_STOCK":
        dsr_stmt = (
            select(DailyStockReconciliation)
            .where(DailyStockReconciliation.id == req.document_id)
            .with_for_update()
        )
        dsr = (await db.execute(dsr_stmt)).scalar_one_or_none()
        if not dsr:
            raise NotFoundError(f"Daily Stock Reconciliation {req.document_id} not found")
        if dsr.status == "CANCELLED":
            raise AppError(status_code=400, message="Daily Stock Reconciliation is already cancelled")

        # Scenario A3: Compensating Ledger Reversal if an adjustment was made
        if dsr.review_outcome == "ADJUSTMENT_APPROVED" or dsr.status == "ADJUSTED":
            variance = dsr.variance
            if variance != Decimal("0.000"):
                mat_stmt = select(Material).where(Material.id == dsr.material_id).with_for_update()
                mat = (await db.execute(mat_stmt)).scalar_one_or_none()
                if mat:
                    rate = mat.purchase_rate or Decimal("0.00")
                    compensating_qty = -variance
                    rev_amount = (abs(compensating_qty) * rate).quantize(Decimal("0.01"))

                    # If variance was positive, we added stock. Reversal must subtract stock.
                    if variance > Decimal("0.000"):
                        if mat.remaining_stock < variance:
                            raise AppError(
                                status_code=400,
                                message=f"Cannot cancel reconciliation: positive adjustment of {variance} has already been consumed (remaining: {mat.remaining_stock})",
                            )
                        mat.remaining_stock += compensating_qty

                        # Deplete the batch created during adjustment
                        adj_batch_stmt = (
                            select(StockBatch)
                            .where(StockBatch.batch_number == f"ADJ-{dsr.reconciliation_number}")
                            .with_for_update()
                        )
                        adj_batch = (await db.execute(adj_batch_stmt)).scalar_one_or_none()
                        if adj_batch:
                            adj_batch.current_balance = Decimal("0.000")
                            adj_batch.status = "DEPLETED"

                    else:
                        # Negative variance: we subtracted stock. Reversal restores stock.
                        mat.remaining_stock += compensating_qty
                        # Create compensating batch to restore stock balance
                        rev_batch = StockBatch(
                            batch_number=f"REV-ADJ-{dsr.reconciliation_number}",
                            project_id=dsr.project_id,
                            material_master_id=dsr.material_master_id,
                            initial_quantity=compensating_qty,
                            current_balance=compensating_qty,
                            purchase_rate=rate,
                            receipt_date=datetime.utcnow(),
                            batch_source="ADJUSTMENT_REVERSAL",
                            status="ACTIVE",
                        )
                        db.add(rev_batch)

                    # Add compensating MaterialTransaction and MaterialLedger entries
                    mat_tx = MaterialTransaction(
                        material_id=mat.id,
                        type=TransactionType.ADJUSTMENT,
                        quantity=compensating_qty,
                        rate=rate,
                        total_amount=rev_amount,
                    )
                    db.add(mat_tx)

                    ledger = MaterialLedger(
                        material_id=mat.id,
                        type=TransactionType.ADJUSTMENT,
                        quantity=compensating_qty,
                        rate=rate,
                        total_amount=rev_amount,
                    )
                    db.add(ledger)

                    # Update ProjectMaterialValuation if exists
                    val_stmt = (
                        select(ProjectMaterialValuation)
                        .where(
                            and_(
                                ProjectMaterialValuation.project_id == dsr.project_id,
                                ProjectMaterialValuation.material_master_id == dsr.material_master_id,
                            )
                        )
                        .with_for_update()
                    )
                    val = (await db.execute(val_stmt)).scalar_one_or_none()
                    if val:
                        val.total_purchased_quantity += compensating_qty
                        if compensating_qty > Decimal("0.000"):
                            val.total_purchased_amount += rev_amount
                        else:
                            val.total_purchased_amount = max(Decimal("0.00"), val.total_purchased_amount - rev_amount)
                        if val.total_purchased_quantity > Decimal("0.000"):
                            val.current_moving_weighted_average_rate = (val.total_purchased_amount / val.total_purchased_quantity).quantize(Decimal("0.01"))
                        else:
                            val.total_purchased_quantity = Decimal("0.000")
                            val.total_purchased_amount = Decimal("0.00")
                            val.current_moving_weighted_average_rate = Decimal("0.00")
                        val.last_recalculated_at = datetime.utcnow()

        dsr.status = "CANCELLED"

    # Mark request as verified
    req.status = "VERIFIED"
    req.verified_by_id = user_id
    req.verified_at = datetime.utcnow()

    # Area B: Dispatch notification to company stakeholders on verified cancellation
    try:
        from app.services.notification_service import notify_company_stakeholders
        await notify_company_stakeholders(
            db=db,
            company_id=company_id,
            title=f"Cancellation Approved: {req.document_type} {req.document_number}",
            message=f"Document {req.document_type} {req.document_number} cancellation was verified and executed by checker {user_id}.",
            type="info",
            link=f"/cancellations/requests/{req.id}",
            actor_id=user_id,
        )
    except Exception:
        pass

    await db.commit()
    await db.refresh(req)
    return req
