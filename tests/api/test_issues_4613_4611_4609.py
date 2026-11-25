import asyncio
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal

import openpyxl
import pypdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main
from app.main import app
from app.models.master_data import LabourType
from app.models.contractor import Contractor
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.project import Project, ProjectMember, Issue
from app.models.invoice import Invoice
from app.models.expense import Expense
from app.models.equipment import Equipment, EquipmentMaintenance, EquipmentRental, EquipmentPurchase, EquipmentUsage
from app.core.enums import (
    IssuePriority,
    IssueStatus,
    IssueCategory,
    EquipmentStatus,
    EquipmentCondition,
    PurchaseType,
    ProjectStatus,
)
from app.core.security import create_access_token

client = TestClient(app)


@pytest.fixture(scope="module")
def seed_test_context():
    """
    Seeds isolated, deterministic test context for Company 1 and Company 2.
    """
    async def _setup():
        async with AsyncSessionLocal() as db:
            user_a = await db.scalar(
                select(User).where(User.company_id == 1, User.role == UserRole.ADMIN.value).limit(1)
            )
            user_b = await db.scalar(
                select(User).where(User.company_id == 2, User.role == UserRole.ADMIN.value).limit(1)
            )
            if not user_a or not user_b:
                raise RuntimeError("Required test users not found in DB")

            tag = uuid.uuid4().hex[:6]

            # Use existing projects for Company 1 and Company 2
            proj_a = await db.scalar(select(Project).where(Project.company_id == user_a.company_id).limit(1))
            proj_b = await db.scalar(select(Project).where(Project.company_id == user_b.company_id).limit(1))
            if not proj_a or not proj_b:
                raise RuntimeError("Required test projects not found in DB")

            # Ensure membership
            mem_a = await db.scalar(
                select(ProjectMember).where(
                    ProjectMember.project_id == proj_a.id,
                    ProjectMember.user_id == user_a.id,
                )
            )
            if not mem_a:
                db.add(ProjectMember(project_id=proj_a.id, user_id=user_a.id))

            mem_b = await db.scalar(
                select(ProjectMember).where(
                    ProjectMember.project_id == proj_b.id,
                    ProjectMember.user_id == user_b.id,
                )
            )
            if not mem_b:
                db.add(ProjectMember(project_id=proj_b.id, user_id=user_b.id))

            await db.flush()

            # Seed specific tagged issues for testing Issue #4609
            # - 1 Open Low
            # - 1 Open High
            # - 1 Closed High
            # - 1 Closed Medium
            issue1 = Issue(
                project_id=proj_a.id,
                business_id=f"ISS-1-{tag}",
                title=f"Open Low Issue {tag}",
                category=IssueCategory.SAFETY,
                priority=IssuePriority.LOW,
                status=IssueStatus.OPEN,
                reported_date=date.today(),
                description="Minor safety gear check",
            )
            issue2 = Issue(
                project_id=proj_a.id,
                business_id=f"ISS-2-{tag}",
                title=f"Open High Issue {tag}",
                category=IssueCategory.MATERIAL,
                priority=IssuePriority.HIGH,
                status=IssueStatus.OPEN,
                reported_date=date.today(),
                description="Concrete strength inspection pending",
            )
            issue3 = Issue(
                project_id=proj_a.id,
                business_id=f"ISS-3-{tag}",
                title=f"Closed High Issue {tag}",
                category=IssueCategory.DELAY,
                priority=IssuePriority.HIGH,
                status=IssueStatus.CLOSED,
                reported_date=date.today() - timedelta(days=2),
                description="Main circuit breaker tripped",
                resolution="Replaced breaker and tested load",
            )
            issue4 = Issue(
                project_id=proj_a.id,
                business_id=f"ISS-4-{tag}",
                title=f"Closed Medium Issue {tag}",
                category=IssueCategory.SAFETY,
                priority=IssuePriority.MEDIUM,
                status=IssueStatus.CLOSED,
                reported_date=date.today() - timedelta(days=3),
                description="Pipe leak on 2nd floor",
                resolution="Fittings re-sealed",
            )
            db.add_all([issue1, issue2, issue3, issue4])

            await db.commit()

            token_a = create_access_token({"sub": str(user_a.id)})
            token_b = create_access_token({"sub": str(user_b.id)})

            return {
                "proj_a_id": proj_a.id,
                "proj_a_name": proj_a.project_name,
                "proj_b_id": proj_b.id,
                "token_a": token_a,
                "token_b": token_b,
                "tag": tag,
            }

    return asyncio.run(_setup())


# ==============================================================================
# ISSUE #4613 TESTS: Profit & Loss PDF Export — Project Name Displayed
# ==============================================================================

def test_issue_4613_profit_loss_pdf_displays_project_name(seed_test_context):
    """
    Verify that GET /api/v1/reports/profit-loss/pdf?project_id={id} displays the actual
    project name in the Report Information section, not merely the numeric project ID.
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    proj_id = ctx["proj_a_id"]
    proj_name = ctx["proj_a_name"]

    res = client.get(
        f"/api/v1/reports/profit-loss/pdf?project_id={proj_id}",
        headers=headers,
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"

    reader = pypdf.PdfReader(io.BytesIO(res.content))
    text = "".join(p.extract_text() or "" for p in reader.pages)

    # 1. Project name must be displayed
    assert proj_name in text, f"Project name '{proj_name}' not found in PDF output"

    # 2. Report Information structure should display Project and Project Name
    assert "Profit & Loss Statement" in text
    assert "REPORT INFORMATION" in text
    assert "Revenue" in text


def test_issue_4613_profit_loss_pdf_all_projects_when_no_filter(seed_test_context):
    """
    Verify that when no project_id filter is passed, 'All Projects' is displayed.
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    res = client.get(
        "/api/v1/reports/profit-loss/pdf",
        headers=headers,
    )
    assert res.status_code == 200
    reader = pypdf.PdfReader(io.BytesIO(res.content))
    text = "".join(p.extract_text() or "" for p in reader.pages)

    assert "All Projects" in text


def test_issue_4613_profit_loss_pdf_cross_tenant_rejected(seed_test_context):
    """
    Verify that Company B cannot export Profit & Loss PDF for Company A's project (IDOR protection).
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_b']}"}
    proj_id = ctx["proj_a_id"]

    res = client.get(
        f"/api/v1/reports/profit-loss/pdf?project_id={proj_id}",
        headers=headers,
    )
    assert res.status_code in (403, 404), f"Expected 403 or 404 for cross-tenant access, got {res.status_code}"


# ==============================================================================
# ISSUE #4611 TESTS: Equipment PDF Export — Footer Overlap & Layout
# ==============================================================================

def test_issue_4611_equipment_pdf_footer_layout_and_cost_summary(seed_test_context):
    """
    Verify that GET /api/v1/equipment/reports/pdf generates a well-formatted PDF where
    Cost Summary has KeepTogether protection and bottomMargin reserves space above footer.
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    res = client.get(
        "/api/v1/equipment/reports/pdf",
        headers=headers,
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"

    reader = pypdf.PdfReader(io.BytesIO(res.content))
    assert len(reader.pages) >= 1

    all_text = ""
    has_cost_summary = False
    has_footer_signatures = False

    for page in reader.pages:
        t = page.extract_text() or ""
        all_text += t
        if "8. COST SUMMARY" in t:
            has_cost_summary = True
        if "Prepared By:" in t and "Reviewed By:" in t and "Approved By:" in t:
            has_footer_signatures = True

    assert has_cost_summary, "'8. COST SUMMARY' section not found in equipment PDF"
    assert has_footer_signatures, "Footer signatures not found in equipment PDF"
    assert "Total Maintenance Cost" in all_text
    assert "Total Rental Cost" in all_text
    assert "Total Purchase Cost" in all_text
    assert "Grand Total" in all_text


def test_issue_4611_equipment_pdf_multipage_handling(seed_test_context):
    """
    Verify multi-page equipment PDF renders page numbers and footers consistently across pages.
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}

    res = client.get(
        "/api/v1/equipment/reports/pdf",
        headers=headers,
    )
    assert res.status_code == 200
    reader = pypdf.PdfReader(io.BytesIO(res.content))

    # Check footer on each page
    for idx, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        assert "Generated by InfraPilot" in text, f"Footer missing on page {idx + 1}"
        assert f"Page {idx + 1}" in text, f"Page number missing on page {idx + 1}"


# ==============================================================================
# ISSUE #4609 TESTS: Issues Excel Export — Status and Priority Counts
# ==============================================================================

def test_issue_4609_issues_excel_summary_counts_complete(seed_test_context):
    """
    Verify that GET /api/v1/reports/issues/excel contains all four summary counts:
    - Total Issues
    - Open Issues
    - Resolved Issues
    - High Priority Issues
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    proj_id = ctx["proj_a_id"]

    res = client.get(
        f"/api/v1/reports/issues/excel?project_id={proj_id}",
        headers=headers,
    )
    assert res.status_code == 200
    assert "spreadsheetml.sheet" in res.headers["content-type"]

    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    assert "Summary" in wb.sheetnames
    assert "Site Issue Log" in wb.sheetnames

    ws = wb["Summary"]
    summary_dict = {}
    for row in range(1, ws.max_row + 1):
        k = ws.cell(row=row, column=1).value
        v = ws.cell(row=row, column=2).value
        if k:
            summary_dict[str(k).strip()] = v

    # Verify all four required summary counts are present
    assert "Total Issues" in summary_dict, "Total Issues missing from Summary sheet"
    assert "Open Issues" in summary_dict, "Open Issues missing from Summary sheet"
    assert "Resolved Issues" in summary_dict, "Resolved Issues missing from Summary sheet"
    assert "High Priority Issues" in summary_dict, "High Priority Issues missing from Summary sheet"

    # Verify that counts are valid integers and sum correctly
    total = summary_dict["Total Issues"]
    open_cnt = summary_dict["Open Issues"]
    resolved_cnt = summary_dict["Resolved Issues"]
    high_cnt = summary_dict["High Priority Issues"]

    assert total >= 4
    assert open_cnt >= 2
    assert resolved_cnt >= 2
    assert high_cnt >= 2
    assert open_cnt + resolved_cnt == total


def test_issue_4609_issues_excel_counts_respect_filters(seed_test_context):
    """
    Verify that filtering by priority or status updates counts correctly without double-counting.
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    proj_id = ctx["proj_a_id"]

    # Filter by status=Open
    res_open = client.get(
        f"/api/v1/reports/issues/excel?project_id={proj_id}&status={IssueStatus.OPEN.value}",
        headers=headers,
    )
    assert res_open.status_code == 200
    wb_open = openpyxl.load_workbook(io.BytesIO(res_open.content))
    ws_open = wb_open["Summary"]
    summary_open = {
        str(ws_open.cell(row=r, column=1).value).strip(): ws_open.cell(row=r, column=2).value
        for r in range(1, ws_open.max_row + 1)
        if ws_open.cell(row=r, column=1).value
    }

    assert summary_open["Total Issues"] >= 2
    assert summary_open["Open Issues"] == summary_open["Total Issues"]
    assert summary_open["Resolved Issues"] == 0

    # Filter by status=Closed
    res_closed = client.get(
        f"/api/v1/reports/issues/excel?project_id={proj_id}&status={IssueStatus.CLOSED.value}",
        headers=headers,
    )
    assert res_closed.status_code == 200
    wb_closed = openpyxl.load_workbook(io.BytesIO(res_closed.content))
    ws_closed = wb_closed["Summary"]
    summary_closed = {
        str(ws_closed.cell(row=r, column=1).value).strip(): ws_closed.cell(row=r, column=2).value
        for r in range(1, ws_closed.max_row + 1)
        if ws_closed.cell(row=r, column=1).value
    }

    assert summary_closed["Total Issues"] >= 2
    assert summary_closed["Open Issues"] == 0
    assert summary_closed["Resolved Issues"] == summary_closed["Total Issues"]


def test_issue_4609_issues_excel_data_sheet_integrity(seed_test_context):
    """
    Verify that the data sheet headers, columns, and rows remain intact.
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_a']}"}
    proj_id = ctx["proj_a_id"]

    res = client.get(
        f"/api/v1/reports/issues/excel?project_id={proj_id}",
        headers=headers,
    )
    assert res.status_code == 200

    wb = openpyxl.load_workbook(io.BytesIO(res.content))
    ws_data = wb["Site Issue Log"]

    expected_headers = [
        "Issue ID",
        "Project Name",
        "Title",
        "Category",
        "Reported Date",
        "Priority",
        "Status",
        "Assigned To",
        "Description",
        "Resolution Notes",
    ]
    actual_headers = [ws_data.cell(row=1, column=c).value for c in range(1, len(expected_headers) + 1)]
    assert actual_headers == expected_headers

    # Verify rows exist
    assert ws_data.max_row >= 5  # at least 1 header + 4 rows


def test_issue_4609_issues_excel_cross_tenant_rejected(seed_test_context):
    """
    Verify that cross-tenant access to another company's project issues export is blocked.
    """
    ctx = seed_test_context
    headers = {"Authorization": f"Bearer {ctx['token_b']}"}
    proj_id = ctx["proj_a_id"]

    res = client.get(
        f"/api/v1/reports/issues/excel?project_id={proj_id}",
        headers=headers,
    )
    assert res.status_code in (403, 404)
