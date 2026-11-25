import pytest
import pytest_asyncio
import uuid
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func

from app.main import app
from app.db.session import AsyncSessionLocal
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectMember
from app.models.invoice import Invoice
from app.models.client_payment import ClientPayment
from app.models.user import User, UserRole
from app.models.rbac import Permission, UserPermissionOverride
from app.core.enums import InvoiceStatus, InvoiceType, PaymentMethod, PaymentStatus
from app.core.dependencies import get_current_user, get_current_active_user


# =========================================================================
# Client-side Form Validation Logic Model (Frontend Contract for #4313 & #4311)
# =========================================================================

class ClientPayPaymentFormValidator:
    """
    Models the frontend client-side validation logic for the Pay Payment component
    in accordance with Issues #4313, #4311, and #4240.
    """

    MANDATORY_FIELDS = {
        "invoice_id": "Invoice ID",
        "project_id": "Project",
        "amount": "Amount",
        "payment_method": "Payment Method",
    }

    def __init__(self):
        self.field_errors: dict[str, str] = {}
        self.form_error: str | None = None

    def on_blur(self, field_name: str, value: any) -> str | None:
        """
        Triggered when a field loses focus (on blur).
        If mandatory field is left blank, displays inline validation message below field.
        Clears or updates error when the field becomes valid.
        """
        is_blank = False
        if value is None:
            is_blank = True
        elif isinstance(value, str) and not value.strip():
            is_blank = True

        if is_blank:
            if field_name in self.MANDATORY_FIELDS:
                label = self.MANDATORY_FIELDS[field_name]
                msg = f"{label} is required."
                self.field_errors[field_name] = msg
                return msg
        else:
            # Field has value; validate field-specific rules
            if field_name == "amount":
                try:
                    amt = Decimal(str(value).strip())
                    if amt <= Decimal("0"):
                        msg = "Paid Amount must be greater than zero."
                        self.field_errors[field_name] = msg
                        return msg
                except Exception:
                    msg = "Invalid Amount format."
                    self.field_errors[field_name] = msg
                    return msg

            # If valid, clear field error
            self.field_errors.pop(field_name, None)
            return None

    def on_pay_payment_click(self, form_data: dict) -> tuple[bool, str | None]:
        """
        Triggered when user clicks 'Pay Payment'.
        Validates Invoice ID, Project, Amount, and Payment Method.
        If any required field is missing, prevents submission and displays:
          'Please fill out all required fields.'
        If amount is zero or negative:
          'Paid Amount must be greater than zero.'
        """
        # 1. Check all mandatory fields
        missing_fields = []
        for field in self.MANDATORY_FIELDS:
            val = form_data.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing_fields.append(field)

        if missing_fields:
            self.form_error = "Please fill out all required fields."
            return False, self.form_error

        # 2. Check Amount positivity
        try:
            amt = Decimal(str(form_data.get("amount")).strip())
            if amt <= Decimal("0"):
                self.form_error = "Paid Amount must be greater than zero."
                return False, self.form_error
        except Exception:
            self.form_error = "Invalid Amount format."
            return False, self.form_error

        self.form_error = None
        return True, None


# =========================================================================
# Fixtures
# =========================================================================

@pytest_asyncio.fixture
async def setup_payment_validation_context():
    uid = uuid.uuid4().hex[:6]
    rand_int = uuid.uuid4().int % 100000000

    async with AsyncSessionLocal() as db:
        comp = Company(name=f"Comp_Val_{uid}", is_active=True)
        db.add(comp)
        await db.flush()

        owner_a = Owner(
            company_id=comp.id,
            owner_code=f"OA_Val_{uid}",
            owner_name=f"OwnerA_{uid}",
            mobile=f"91{rand_int:08d}",
        )
        owner_b = Owner(
            company_id=comp.id,
            owner_code=f"OB_Val_{uid}",
            owner_name=f"OwnerB_{uid}",
            mobile=f"92{rand_int:08d}",
        )
        db.add_all([owner_a, owner_b])
        await db.flush()

        client_user = User(
            email=f"client_val_{uid}@test.com",
            mobile=f"93{rand_int:08d}",
            role=UserRole.CLIENT.value,
            is_active=True,
            company_id=comp.id,
        )
        admin_user = User(
            email=f"admin_val_{uid}@test.com",
            mobile=f"94{rand_int:08d}",
            role=UserRole.ADMIN.value,
            is_active=True,
            company_id=comp.id,
        )
        db.add_all([client_user, admin_user])
        await db.flush()

        # Grant permissions
        for p_code in ["invoices.view", "invoices.create", "invoices.edit", "projects.view"]:
            p = await db.scalar(select(Permission).where(Permission.code == p_code))
            if p:
                db.add_all([
                    UserPermissionOverride(user_id=client_user.id, permission_id=p.id, is_granted=True),
                    UserPermissionOverride(user_id=admin_user.id, permission_id=p.id, is_granted=True),
                ])

        proj_a = Project(
            company_id=comp.id,
            owner_id=owner_a.id,
            project_name=f"ProjectA_{uid}",
            business_id=f"PA-Val-{uid}",
            status="Ongoing",
        )
        proj_b = Project(
            company_id=comp.id,
            owner_id=owner_b.id,
            project_name=f"ProjectB_{uid}",
            business_id=f"PB-Val-{uid}",
            status="Ongoing",
        )
        db.add_all([proj_a, proj_b])
        await db.flush()

        # Assign client to proj_a
        db.add(ProjectMember(project_id=proj_a.id, user_id=client_user.id))

        inv_a = Invoice(
            company_id=comp.id,
            project_id=proj_a.id,
            owner_id=owner_a.id,
            type=InvoiceType.OWNER,
            invoice_number=f"INV-A-{uid}",
            amount=Decimal("3000.00"),
            total_amount=Decimal("3000.00"),
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("3000.00"),
            status=InvoiceStatus.PENDING,
        )
        inv_b = Invoice(
            company_id=comp.id,
            project_id=proj_b.id,
            owner_id=owner_b.id,
            type=InvoiceType.OWNER,
            invoice_number=f"INV-B-{uid}",
            amount=Decimal("5000.00"),
            total_amount=Decimal("5000.00"),
            paid_amount=Decimal("0.00"),
            pending_amount=Decimal("5000.00"),
            status=InvoiceStatus.PENDING,
        )
        db.add_all([inv_a, inv_b])
        await db.flush()

        # Create an existing pending payment for update tests
        existing_payment = ClientPayment(
            company_id=comp.id,
            client_user_id=client_user.id,
            project_id=proj_a.id,
            invoice_id=inv_a.id,
            payment_no=f"PAY-EXIST-{uid}",
            amount=Decimal("400.00"),
            payment_method=PaymentMethod.CASH,
            payment_status=PaymentStatus.PENDING,
        )
        db.add(existing_payment)
        await db.commit()

        p_a_id = proj_a.id
        p_b_id = proj_b.id
        i_a_id = inv_a.id
        i_b_id = inv_b.id
        pay_id = existing_payment.id

    return {
        "client": client_user,
        "admin": admin_user,
        "p_a_id": p_a_id,
        "p_b_id": p_b_id,
        "i_a_id": i_a_id,
        "i_b_id": i_b_id,
        "existing_payment_id": pay_id,
    }


# =========================================================================
# Step 5 - Test Cases 1 & 2: Blur Validation & Error Clearing (#4313)
# =========================================================================

def test_client_empty_mandatory_field_on_blur_and_clearing():
    """
    Issue #4313:
    1. When a mandatory field loses focus while empty, inline error is displayed below field.
    2. When the field receives valid input, the error is cleared.
    """
    validator = ClientPayPaymentFormValidator()

    # 1. Blur on empty fields displays inline errors
    err_inv = validator.on_blur("invoice_id", "")
    assert err_inv == "Invoice ID is required."
    assert validator.field_errors.get("invoice_id") == "Invoice ID is required."

    err_proj = validator.on_blur("project_id", None)
    assert err_proj == "Project is required."

    err_amt = validator.on_blur("amount", "   ")
    assert err_amt == "Amount is required."

    err_pm = validator.on_blur("payment_method", "")
    assert err_pm == "Payment Method is required."

    # 2. Entering valid input and blurring clears the error
    clear_inv = validator.on_blur("invoice_id", "101")
    assert clear_inv is None
    assert "invoice_id" not in validator.field_errors

    clear_proj = validator.on_blur("project_id", "5")
    assert clear_proj is None
    assert "project_id" not in validator.field_errors

    clear_amt = validator.on_blur("amount", "250.00")
    assert clear_amt is None
    assert "amount" not in validator.field_errors

    clear_pm = validator.on_blur("payment_method", "CASH")
    assert clear_pm is None
    assert "payment_method" not in validator.field_errors


# =========================================================================
# Step 5 - Test Cases 3, 4, 5: Pay Payment Click Validation (#4311, #4240)
# =========================================================================

def test_client_pay_payment_click_missing_fields_and_negative_amount():
    """
    Issue #4311 & #4240:
    3. Clicking Pay Payment with missing fields prevents the request.
    4. Displays exactly: 'Please fill out all required fields.'
    5. Negative amount is rejected by the UI with 'Paid Amount must be greater than zero.'
    """
    validator = ClientPayPaymentFormValidator()

    # Empty form
    can_submit, err = validator.on_pay_payment_click({})
    assert can_submit is False
    assert err == "Please fill out all required fields."

    # Missing project_id and amount
    can_submit, err = validator.on_pay_payment_click({
        "invoice_id": "1",
        "payment_method": "CASH",
    })
    assert can_submit is False
    assert err == "Please fill out all required fields."

    # Missing only payment_method
    can_submit, err = validator.on_pay_payment_click({
        "invoice_id": "1",
        "project_id": "2",
        "amount": "100.00",
    })
    assert can_submit is False
    assert err == "Please fill out all required fields."

    # Negative amount (-20)
    can_submit, err = validator.on_pay_payment_click({
        "invoice_id": "1",
        "project_id": "2",
        "amount": "-20",
        "payment_method": "CASH",
    })
    assert can_submit is False
    assert err == "Paid Amount must be greater than zero."

    # Zero amount (0)
    can_submit, err = validator.on_pay_payment_click({
        "invoice_id": "1",
        "project_id": "2",
        "amount": "0",
        "payment_method": "CASH",
    })
    assert can_submit is False
    assert err == "Paid Amount must be greater than zero."

    # Valid submission
    can_submit, err = validator.on_pay_payment_click({
        "invoice_id": "1",
        "project_id": "2",
        "amount": "500.00",
        "payment_method": "CASH",
    })
    assert can_submit is True
    assert err is None


# =========================================================================
# Step 5 - Test Cases 4, 6, 7: API Rejection of Missing, Zero & Negative Fields
# =========================================================================

@pytest.mark.asyncio
async def test_api_rejects_missing_mandatory_fields(setup_payment_validation_context):
    """
    Issue #4311:
    Direct API request with missing mandatory fields must return 400 Bad Request
    with exact detail: 'Please fill out all required fields.'
    """
    ctx = setup_payment_validation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Case 1: Empty form-data
        res1 = await client.post("/api/v1/client-payments", data={})
        assert res1.status_code == 400
        assert res1.json().get("detail") == "Please fill out all required fields."

        # Case 2: Partial form-data (missing amount and payment_method)
        res2 = await client.post("/api/v1/client-payments", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
        })
        assert res2.status_code == 400
        assert res2.json().get("detail") == "Please fill out all required fields."

        # Case 3: Empty string values
        res3 = await client.post("/api/v1/client-payments", data={
            "invoice_id": "",
            "project_id": "",
            "amount": "",
            "payment_method": "",
        })
        assert res3.status_code == 400
        assert res3.json().get("detail") == "Please fill out all required fields."

        # Case 4: JSON payload with missing fields
        res4 = await client.post("/api/v1/client-payments", json={
            "invoice_id": ctx["i_a_id"],
        })
        assert res4.status_code == 400
        assert res4.json().get("detail") == "Please fill out all required fields."

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_api_rejects_zero_and_negative_amount(setup_payment_validation_context):
    """
    Issue #4240:
    Direct API requests with zero or negative amounts must return 400 Bad Request
    with exact detail: 'Paid Amount must be greater than zero.'
    """
    ctx = setup_payment_validation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Zero amount via form-data
        res_zero = await client.post("/api/v1/client-payments", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
            "amount": "0",
            "payment_method": "CASH",
        })
        assert res_zero.status_code == 400
        assert res_zero.json().get("detail") == "Paid Amount must be greater than zero."

        # Negative amount (-20) via form-data
        res_neg = await client.post("/api/v1/client-payments", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
            "amount": "-20",
            "payment_method": "CASH",
        })
        assert res_neg.status_code == 400
        assert res_neg.json().get("detail") == "Paid Amount must be greater than zero."

        # Negative amount via JSON payload
        res_neg_json = await client.post("/api/v1/client-payments", json={
            "invoice_id": ctx["i_a_id"],
            "project_id": ctx["p_a_id"],
            "amount": "-50.00",
            "payment_method": "CASH",
        })
        assert res_neg_json.status_code == 400
        assert res_neg_json.json().get("detail") == "Paid Amount must be greater than zero."

        # Update payment with negative amount
        res_upd_neg = await client.put(f"/api/v1/client-payments/{ctx['existing_payment_id']}", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
            "amount": "-10.00",
            "payment_method": "CASH",
        })
        assert res_upd_neg.status_code == 400
        assert res_upd_neg.json().get("detail") == "Paid Amount must be greater than zero."

        # Update payment with zero amount
        res_upd_zero = await client.put(f"/api/v1/client-payments/{ctx['existing_payment_id']}", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
            "amount": "0",
            "payment_method": "CASH",
        })
        assert res_upd_zero.status_code == 400
        assert res_upd_zero.json().get("detail") == "Paid Amount must be greater than zero."

    app.dependency_overrides.clear()


# =========================================================================
# Step 5 - Test Cases 8 & 9: Positive Payment & No Invalid Database Mutation
# =========================================================================

@pytest.mark.asyncio
async def test_valid_positive_payment_and_no_invalid_records(setup_payment_validation_context):
    """
    Test 8: Valid positive payment still succeeds (201 Created).
    Test 9: Invalid payment creates no database record and does not update existing record.
    """
    ctx = setup_payment_validation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with AsyncSessionLocal() as db:
            count_before = await db.scalar(select(func.count()).select_from(ClientPayment))
            existing_pay = await db.get(ClientPayment, ctx["existing_payment_id"])
            original_amount = existing_pay.amount

        # Failed creation attempts (missing fields, negative amount, zero amount)
        await client.post("/api/v1/client-payments", data={})
        await client.post("/api/v1/client-payments", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
            "amount": "-20.00",
            "payment_method": "CASH",
        })
        await client.post("/api/v1/client-payments", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
            "amount": "0.00",
            "payment_method": "CASH",
        })

        # Failed update attempts
        await client.put(f"/api/v1/client-payments/{ctx['existing_payment_id']}", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
            "amount": "-999.00",
            "payment_method": "CASH",
        })

        # Verify no database records were created and existing payment unchanged
        async with AsyncSessionLocal() as db:
            count_after_failed = await db.scalar(select(func.count()).select_from(ClientPayment))
            assert count_after_failed == count_before, "Failed payment attempts must create no records"

            refreshed_pay = await db.get(ClientPayment, ctx["existing_payment_id"])
            assert refreshed_pay.amount == original_amount, "Failed update must not alter existing amount"

        # Now execute valid positive payment
        res_valid = await client.post("/api/v1/client-payments", data={
            "invoice_id": str(ctx["i_a_id"]),
            "project_id": str(ctx["p_a_id"]),
            "amount": "750.00",
            "payment_method": "CASH",
        })
        assert res_valid.status_code == 201
        pay_data = res_valid.json()
        assert pay_data["amount"] == "750.00"
        assert pay_data["payment_status"] in ("PENDING", "VERIFICATION_PENDING")
        assert pay_data["project_id"] == ctx["p_a_id"]
        assert pay_data["invoice_id"] == ctx["i_a_id"]

        # Verify exactly one record added in database
        async with AsyncSessionLocal() as db:
            count_final = await db.scalar(select(func.count()).select_from(ClientPayment))
            assert count_final == count_before + 1

    app.dependency_overrides.clear()


# =========================================================================
# Step 5 - Test Case 10: Ownership and Authorization Checks Remain Effective
# =========================================================================

@pytest.mark.asyncio
async def test_payment_ownership_and_project_authorization(setup_payment_validation_context):
    """
    Test 10: Existing invoice/project ownership and authorization checks remain effective.
    - Project not assigned to client -> 404 Project not found.
    - Invoice belonging to another project -> 400 Selected invoice does not belong to this project.
    """
    ctx = setup_payment_validation_context
    app.dependency_overrides[get_current_active_user] = lambda: ctx["client"]
    app.dependency_overrides[get_current_user] = lambda: ctx["client"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Client tries to pay for unassigned project B
        res_unassigned = await client.post("/api/v1/client-payments", data={
            "project_id": str(ctx["p_b_id"]),
            "invoice_id": str(ctx["i_b_id"]),
            "amount": "100.00",
            "payment_method": "CASH",
        })
        assert res_unassigned.status_code == 404
        assert res_unassigned.json().get("detail") in ("Project not found", "Project not found.")

        # Client tries to pay for assigned project A with invoice B (which belongs to project B)
        res_mismatch = await client.post("/api/v1/client-payments", data={
            "project_id": str(ctx["p_a_id"]),
            "invoice_id": str(ctx["i_b_id"]),
            "amount": "100.00",
            "payment_method": "CASH",
        })
        assert res_mismatch.status_code == 400
        assert res_mismatch.json().get("detail") == "Selected invoice does not belong to this project."

    app.dependency_overrides.clear()
