from typing import Dict, Any
from decimal import Decimal
from sqlalchemy import select, func, and_, or_, literal
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.accountant import VendorBill, VendorBillItem
from app.models.invoice import Transaction
from app.models.material import MaterialTransaction, PurchaseOrder, Supplier
from app.models.project import Project

class ReportService:
    @staticmethod
    async def get_procurement_efficiency_report(db: AsyncSession, filters: Dict[str, Any]):
        # Base VendorBill CTE with required fields and id
        vb_query = select(
            VendorBill.id.label("id"),
            VendorBill.project_id,
            VendorBill.supplier_id,
            VendorBill.status,
            VendorBill.bill_date,
            VendorBill.bill_number,
            VendorBill.total_amount,
            VendorBill.amount_paid,
        ).where(VendorBill.project_id == filters["project_id"])
        if filters.get("supplier_id"):
            vb_query = vb_query.where(VendorBill.supplier_id == filters["supplier_id"])
        if filters.get("status"):
            vb_query = vb_query.where(VendorBill.status == filters["status"])
        if filters.get("date_from"):
            vb_query = vb_query.where(VendorBill.bill_date >= filters["date_from"])
        if filters.get("date_to"):
            vb_query = vb_query.where(VendorBill.bill_date <= filters["date_to"])
        # Safe search on bill_number or supplier name
        if filters.get("search"):
            pattern = f"%{filters['search']}%"
            vb_query = vb_query.where(
                or_(
                    VendorBill.bill_number.ilike(pattern),
                    VendorBill.supplier_id.in_(
                        select(Supplier.id).where(Supplier.supplier_name.ilike(pattern))
                    ),
                )
            )
        # Apply payment_status filter directly
        if filters.get("payment_status"):
            ps = filters["payment_status"].upper()
            if ps == "PAID":
                vb_query = vb_query.where(VendorBill.amount_paid >= VendorBill.total_amount)
            elif ps == "PARTIAL":
                vb_query = vb_query.where(and_(VendorBill.amount_paid > 0, VendorBill.amount_paid < VendorBill.total_amount))
            elif ps == "UNPAID":
                vb_query = vb_query.where(VendorBill.amount_paid == 0)
        vb_cte = vb_query.cte("vendor_bill_agg")

        # Transaction CTE for payment days (linked_to like "vendor_bill:{id}")
        tx_query = select(
            Transaction.linked_to,
            Transaction.created_at.label("pay_date"),
        ).where(Transaction.linked_to.like("vendor_bill:%"))
        tx_cte = tx_query.cte("payment_tx")

        payment_days_cte = (
            select(
                vb_cte.c.supplier_id,
                func.avg(func.datediff(tx_cte.c.pay_date, vb_cte.c.bill_date)).label("avg_payment_days"),
            )
            .join(
                tx_cte,
                tx_cte.c.linked_to == func.concat("vendor_bill:", vb_cte.c.id),
                isouter=True,
            )
            .group_by(vb_cte.c.supplier_id)
        ).cte("payment_days")

        # Material purchase aggregation (type = PURCHASE)
        mt_query = select(
            func.sum(MaterialTransaction.quantity).label("total_quantity"),
            func.sum(MaterialTransaction.total_amount).label("total_value"),
        ).where(
            MaterialTransaction.project_id == filters["project_id"],
            MaterialTransaction.type == "PURCHASE",
        )
        if filters.get("date_from"):
            mt_query = mt_query.where(MaterialTransaction.transaction_date >= filters["date_from"])
        if filters.get("date_to"):
            mt_query = mt_query.where(MaterialTransaction.transaction_date <= filters["date_to"])
        mt_cte = mt_query.cte("material_purchase_agg")

        # Outstanding Purchase Orders (status values: CREATED, PENDING)
        po_query = select(
            func.count(PurchaseOrder.id).label("outstanding_count"),
            func.sum(PurchaseOrder.total_amount).label("outstanding_value"),
        ).where(
            PurchaseOrder.project_id == filters["project_id"],
            PurchaseOrder.status.in_(["CREATED", "PENDING"]),
            PurchaseOrder.is_deleted == False,
        )
        if filters.get("supplier_id"):
            po_query = po_query.where(PurchaseOrder.supplier_id == filters["supplier_id"])
        po_cte = po_query.cte("outstanding_po")

        # Project budget CTE
        proj_query = select(
            Project.id.label("project_id"),
            Project.project_name,
            Project.budget_amount,
        ).where(Project.id == filters["project_id"]).cte("proj_budget")

        # Aggregate totals
        agg_stmt = (
            select(
                proj_query.c.project_id,
                proj_query.c.project_name,
                proj_query.c.budget_amount,
                func.coalesce(func.sum(vb_cte.c.total_amount), 0).label("total_spend"),
                func.coalesce(func.sum(vb_cte.c.amount_paid), 0).label("total_paid"),
                mt_cte.c.total_quantity,
                mt_cte.c.total_value,
                po_cte.c.outstanding_count,
                po_cte.c.outstanding_value,
            )
            .select_from(
                proj_query
                .outerjoin(vb_cte, vb_cte.c.project_id == proj_query.c.project_id)
                .outerjoin(mt_cte, literal(True))
                .outerjoin(po_cte, literal(True))
            )
            .group_by(
                proj_query.c.project_id,
                proj_query.c.project_name,
                proj_query.c.budget_amount,
                mt_cte.c.total_quantity,
                mt_cte.c.total_value,
                po_cte.c.outstanding_count,
                po_cte.c.outstanding_value,
            )
        )
        result = await db.execute(agg_stmt)
        row = result.first()
        if not row:
            raise Exception("Report data missing")

        # Supplier level breakdown
        sup_stmt = (
            select(
                Supplier.id.label("supplier_id"),
                Supplier.supplier_name,
                func.count(vb_cte.c.id).label("bill_count"),
                func.coalesce(func.sum(vb_cte.c.total_amount), 0).label("total_spend"),
                func.coalesce(func.sum(vb_cte.c.amount_paid), 0).label("paid_amount"),
                (func.coalesce(func.sum(vb_cte.c.total_amount), 0) - func.coalesce(func.sum(vb_cte.c.amount_paid), 0)).label("pending_amount"),
                func.coalesce(payment_days_cte.c.avg_payment_days, 0).label("avg_payment_days"),
            )
            .join(vb_cte, vb_cte.c.supplier_id == Supplier.id, isouter=True)
            .join(payment_days_cte, payment_days_cte.c.supplier_id == Supplier.id, isouter=True)
            .where(
                and_(
                    vb_cte.c.project_id == filters["project_id"],
                    Supplier.is_deleted == False,
                )
            )
            .group_by(Supplier.id, Supplier.supplier_name, payment_days_cte.c.avg_payment_days)
        )
        sup_res = await db.execute(sup_stmt)
        suppliers = sup_res.fetchall()

        # Build DTOs (import lazily)
        from app.schemas.report import (
            ProcurementEfficiencyReportDTO,
            ReportSummaryDTO,
            ProcurementTotalsDTO,
            MaterialsProcuredDTO,
            SupplierPerformanceDTO,
            PurchaseOrdersDTO,
            FiltersAppliedDTO,
        )

        total_spend = Decimal(row.total_spend or 0)
        total_paid = Decimal(row.total_paid or 0)
        total_pending = total_spend - total_paid
        budget = Decimal(row.budget_amount or 0)
        budget_vs_actual = "0%" if budget == 0 else f"{(total_spend / budget * 100):.1f}%"

        supplier_list = [
            SupplierPerformanceDTO(
                supplier_id=s.supplier_id,
                supplier_name=s.supplier_name,
                bill_count=s.bill_count,
                total_spend=Decimal(s.total_spend or 0),
                paid_amount=Decimal(s.paid_amount or 0),
                pending_amount=Decimal(s.pending_amount or 0),
                avg_payment_days=round(float(s.avg_payment_days or 0)),
            )
            for s in suppliers
        ]

        report = ProcurementEfficiencyReportDTO(
            summary=ReportSummaryDTO(
                project_id=row.project_id,
                project_name=row.project_name,
                budget_amount=budget,
                total_spend=total_spend,
                budget_vs_actual=budget_vs_actual,
            ),
            procurement=ProcurementTotalsDTO(
                total_spend=total_spend,
                total_paid=total_paid,
                total_pending=total_pending,
                materials_procured=MaterialsProcuredDTO(
                    total_quantity=Decimal(row.total_quantity or 0),
                    total_value=Decimal(row.total_value or 0),
                ),
            ),
            suppliers=supplier_list,
            purchase_orders=PurchaseOrdersDTO(
                outstanding_count=row.outstanding_count or 0,
                outstanding_value=Decimal(row.outstanding_value or 0),
            ),
            filters_applied=FiltersAppliedDTO(**filters),
        )
        return report
