from datetime import date
from typing import Dict, Any, Optional
from decimal import Decimal
from sqlalchemy import select, func, and_, or_, literal, case
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.accountant import VendorBill, VendorBillItem
from app.models.billing import RABill
from app.models.expense import Expense
from app.models.invoice import Transaction
from app.models.material import MaterialTransaction, PurchaseOrder, Supplier
from app.models.project import Project
from app.utils.helpers import NotFoundError
from app.schemas.report import (
    FinancialHealthSummaryDTO,
    StatusMetricDTO,
    RecentBillItemDTO,
    BillingOverviewDTO,
    ExpenseCategoryDTO,
    RecentExpenseItemDTO,
    ExpensesOverviewDTO,
    ReceivablesDTO,
    PayablesDTO,
    PendingPaymentsDTO,
    FinancialFiltersAppliedDTO,
    ProjectFinancialHealthReportDTO,
)

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

    @staticmethod
    async def get_project_financial_health_report(
        db: AsyncSession, filters: Dict[str, Any]
    ) -> ProjectFinancialHealthReportDTO:
        """Generate comprehensive Project Financial Health Report.

        Aggregates:
        1. Project budget and verification.
        2. Client billing via RABill (Approved/Paid revenue, pending client amounts).
        3. Vendor Bill expenses (excluding REJECTED, tracking payables).
        4. Direct site expenses (categorized and dated).
        5. Cashflow position and deterministic financial health status & score.
        """
        project_id = filters["project_id"]
        date_from = filters.get("date_from")
        date_to = filters.get("date_to")

        if isinstance(date_from, str) and date_from:
            date_from = date.fromisoformat(date_from)
        if isinstance(date_to, str) and date_to:
            date_to = date.fromisoformat(date_to)

        # 1. Project verification
        project = await db.get(Project, project_id)
        if not project:
            raise NotFoundError("Project not found")

        budget_amount = Decimal(str(project.budget_amount or 0))
        project_name = project.project_name or ""

        # 2. RA Bills aggregation
        ra_filters = [RABill.project_id == project_id]
        if date_from:
            ra_filters.append(RABill.bill_date >= date_from)
        if date_to:
            ra_filters.append(RABill.bill_date <= date_to)

        ra_totals_stmt = select(
            func.count(RABill.id).label("bill_count"),
            func.coalesce(func.sum(RABill.total_amount), 0).label("total_billed"),
            func.coalesce(
                func.sum(
                    case(
                        (RABill.status.in_(["Approved", "Paid"]), RABill.total_amount),
                        else_=0,
                    )
                ),
                0,
            ).label("total_certified"),
            func.coalesce(
                func.sum(
                    case(
                        (RABill.status == "Paid", RABill.total_amount),
                        else_=0,
                    )
                ),
                0,
            ).label("total_received"),
            func.count(
                case(
                    (RABill.status == "Approved", RABill.id),
                )
            ).label("pending_certified_count"),
        ).where(and_(*ra_filters))

        ra_totals_res = await db.execute(ra_totals_stmt)
        ra_totals = ra_totals_res.one()

        ra_bills_count = ra_totals.bill_count or 0
        total_billed = Decimal(str(ra_totals.total_billed or 0))
        total_certified = Decimal(str(ra_totals.total_certified or 0))
        total_received = Decimal(str(ra_totals.total_received or 0))
        total_pending_client = total_certified - total_received
        pending_client_bills_count = ra_totals.pending_certified_count or 0

        # Billing status breakdown (Draft, Submitted, Approved, Paid)
        status_breakdown = {
            "Draft": StatusMetricDTO(count=0, amount=Decimal("0.00")),
            "Submitted": StatusMetricDTO(count=0, amount=Decimal("0.00")),
            "Approved": StatusMetricDTO(count=0, amount=Decimal("0.00")),
            "Paid": StatusMetricDTO(count=0, amount=Decimal("0.00")),
        }

        status_stmt = (
            select(
                RABill.status,
                func.count(RABill.id).label("cnt"),
                func.coalesce(func.sum(RABill.total_amount), 0).label("amt"),
            )
            .where(and_(*ra_filters))
            .group_by(RABill.status)
        )
        status_res = await db.execute(status_stmt)
        for s_row in status_res.all():
            s_name = s_row.status or "Draft"
            status_breakdown[s_name] = StatusMetricDTO(
                count=s_row.cnt or 0,
                amount=Decimal(str(s_row.amt or 0)),
            )

        # Recent bills (most recent 10)
        recent_bills_stmt = (
            select(
                RABill.id,
                RABill.bill_number,
                RABill.work_description,
                RABill.bill_date,
                RABill.total_amount,
                RABill.status,
            )
            .where(and_(*ra_filters))
            .order_by(RABill.bill_date.desc(), RABill.id.desc())
            .limit(10)
        )
        recent_bills_res = await db.execute(recent_bills_stmt)
        recent_bills = [
            RecentBillItemDTO(
                id=b.id,
                bill_number=b.bill_number,
                work_description=b.work_description or "",
                bill_date=b.bill_date.isoformat() if b.bill_date else "",
                total_amount=Decimal(str(b.total_amount or 0)),
                status=b.status,
            )
            for b in recent_bills_res.all()
        ]

        billing_overview = BillingOverviewDTO(
            total_billed=total_billed,
            total_certified=total_certified,
            total_received=total_received,
            total_pending_client=total_pending_client,
            ra_bills_count=ra_bills_count,
            status_breakdown=status_breakdown,
            recent_bills=recent_bills,
        )

        # 3. Vendor Bills aggregation (exclude REJECTED)
        vb_filters = [
            VendorBill.project_id == project_id,
            VendorBill.status != "REJECTED",
        ]
        if date_from:
            vb_filters.append(VendorBill.bill_date >= date_from)
        if date_to:
            vb_filters.append(VendorBill.bill_date <= date_to)

        vb_stmt = select(
            func.coalesce(func.sum(VendorBill.total_amount), 0).label("vendor_spend"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            VendorBill.total_amount > VendorBill.amount_paid,
                            VendorBill.total_amount - VendorBill.amount_paid,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("vendor_payables"),
            func.count(
                case(
                    (
                        VendorBill.total_amount > VendorBill.amount_paid,
                        VendorBill.id,
                    ),
                )
            ).label("pending_vb_count"),
        ).where(and_(*vb_filters))

        vb_res = await db.execute(vb_stmt)
        vb_row = vb_res.one()

        vendor_bills_spend = Decimal(str(vb_row.vendor_spend or 0))
        vendor_payables = Decimal(str(vb_row.vendor_payables or 0))
        pending_vendor_bills_count = vb_row.pending_vb_count or 0

        # 4. Direct Expenses aggregation
        exp_filters = [Expense.project_id == project_id]
        if date_from:
            exp_filters.append(Expense.expense_date >= date_from)
        if date_to:
            exp_filters.append(Expense.expense_date <= date_to)

        exp_stmt = select(
            func.coalesce(func.sum(Expense.amount), 0).label("direct_spend")
        ).where(and_(*exp_filters))
        exp_res = await db.execute(exp_stmt)
        direct_expenses_spend = Decimal(str(exp_res.scalar() or 0))

        total_expenses = vendor_bills_spend + direct_expenses_spend

        # Expenses category breakdown
        cat_stmt = (
            select(
                Expense.category,
                func.coalesce(func.sum(Expense.amount), 0).label("cat_amount"),
            )
            .where(and_(*exp_filters))
            .group_by(Expense.category)
            .order_by(func.sum(Expense.amount).desc())
        )
        cat_res = await db.execute(cat_stmt)
        by_category = []
        for c_row in cat_res.all():
            cat_amount = Decimal(str(c_row.cat_amount or 0))
            if total_expenses > 0:
                pct = round(float((cat_amount / total_expenses) * 100), 2)
            elif direct_expenses_spend > 0:
                pct = round(float((cat_amount / direct_expenses_spend) * 100), 2)
            else:
                pct = 0.0
            by_category.append(
                ExpenseCategoryDTO(
                    category=c_row.category,
                    amount=cat_amount,
                    percentage=pct,
                )
            )

        # Recent expenses (most recent 10)
        recent_exp_stmt = (
            select(
                Expense.id,
                Expense.category,
                Expense.description,
                Expense.amount,
                Expense.expense_date,
                Expense.payment_mode,
            )
            .where(and_(*exp_filters))
            .order_by(Expense.expense_date.desc(), Expense.id.desc())
            .limit(10)
        )
        recent_exp_res = await db.execute(recent_exp_stmt)
        recent_expenses = [
            RecentExpenseItemDTO(
                id=e.id,
                category=e.category,
                description=e.description or "",
                amount=Decimal(str(e.amount or 0)),
                expense_date=e.expense_date.isoformat() if e.expense_date else "",
                payment_mode=e.payment_mode or "",
            )
            for e in recent_exp_res.all()
        ]

        expenses_overview = ExpensesOverviewDTO(
            total_expenses=total_expenses,
            vendor_bills_spend=vendor_bills_spend,
            direct_expenses_spend=direct_expenses_spend,
            by_category=by_category,
            recent_expenses=recent_expenses,
        )

        # 5. Pending payments & cashflow
        net_cashflow_position = total_pending_client - vendor_payables
        pending_payments = PendingPaymentsDTO(
            receivables=ReceivablesDTO(
                total_receivable=total_pending_client,
                pending_bills_count=pending_client_bills_count,
            ),
            payables=PayablesDTO(
                total_payable=vendor_payables,
                vendor_payables=vendor_payables,
                pending_vendor_bills_count=pending_vendor_bills_count,
            ),
            net_cashflow_position=net_cashflow_position,
        )

        # 6. Summary metrics
        total_revenue = total_certified
        net_profit = total_revenue - total_expenses

        if total_revenue > 0:
            profit_margin_percent = round(float((net_profit / total_revenue) * 100), 2)
        else:
            profit_margin_percent = 0.0

        if budget_amount > 0:
            budget_utilization_percent = round(float((total_expenses / budget_amount) * 100), 2)
        else:
            budget_utilization_percent = 0.0

        # 7. Financial Health Status
        # Priority rules:
        # 1. OVER_BUDGET: total_expenses > budget_amount
        # 2. CRITICAL: net_profit < 0
        # 3. MODERATE: budget_utilization_percent > 80 OR profit_margin_percent < 15
        # 4. HEALTHY: otherwise
        if total_expenses > budget_amount:
            financial_health_status = "OVER_BUDGET"
        elif net_profit < 0:
            financial_health_status = "CRITICAL"
        elif budget_utilization_percent > 80.0 or profit_margin_percent < 15.0:
            financial_health_status = "MODERATE"
        else:
            financial_health_status = "HEALTHY"

        # 8. Deterministic Health Score (0–100, clamped)
        # Weights: Budget Adherence 40%, Profit Margin 40%, Cashflow 20%
        if budget_amount > 0:
            util = float(budget_utilization_percent)
            if util <= 80.0:
                budget_score = 100.0
            elif util <= 100.0:
                budget_score = 100.0 - (util - 80.0) * 2.5
            else:
                budget_score = max(0.0, 50.0 - (util - 100.0) * 2.5)
        else:
            budget_score = 100.0 if total_expenses == 0 else 0.0

        if total_revenue > 0:
            if profit_margin_percent >= 25.0:
                margin_score = 100.0
            elif profit_margin_percent >= 0.0:
                margin_score = (profit_margin_percent / 25.0) * 100.0
            else:
                margin_score = max(0.0, 50.0 + profit_margin_percent * 2.0)
        else:
            margin_score = 50.0 if total_expenses == 0 else 0.0

        if vendor_payables == 0 and total_pending_client == 0:
            cashflow_score = 100.0
        elif net_cashflow_position >= 0:
            cashflow_score = 100.0
        else:
            if vendor_payables > 0:
                coverage = float(total_pending_client / vendor_payables)
                cashflow_score = max(0.0, min(100.0, coverage * 100.0))
            else:
                cashflow_score = 100.0

        raw_score = 0.40 * budget_score + 0.40 * margin_score + 0.20 * cashflow_score
        health_score = int(round(max(0.0, min(100.0, raw_score))))

        summary = FinancialHealthSummaryDTO(
            project_id=project_id,
            project_name=project_name,
            budget_amount=budget_amount,
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            net_profit=net_profit,
            profit_margin_percent=profit_margin_percent,
            budget_utilization_percent=budget_utilization_percent,
            financial_health_status=financial_health_status,
            health_score=health_score,
        )

        filters_applied = FinancialFiltersAppliedDTO(
            project_id=project_id,
            date_from=date_from.isoformat() if date_from else None,
            date_to=date_to.isoformat() if date_to else None,
        )

        return ProjectFinancialHealthReportDTO(
            summary=summary,
            billing_overview=billing_overview,
            expenses_overview=expenses_overview,
            pending_payments=pending_payments,
            filters_applied=filters_applied,
        )
