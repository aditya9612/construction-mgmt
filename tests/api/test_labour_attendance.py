import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.core.dependencies import get_current_user, get_current_active_user, require_roles
from app.db.session import AsyncSessionLocal
from app.models.user import User, UserRole
from app.models.company import Company
from app.models.owner import Owner
from app.models.project import Project, ProjectStatus
from app.models.master_data import LabourType, Unit
from app.models.labour import Labour, LabourProject
from app.core.enums import SkillType

admin_user = User(
    id=9001,
    email="admin_labour_test@test.com",
    role=UserRole.ADMIN.value,
    is_active=True,
    is_super_admin=False,
    company_id=1,
)


def override_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    def mock_require_roles(roles):
        return lambda: user

    app.dependency_overrides[require_roles] = mock_require_roles


def clear_user_override():
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean_overrides():
    yield
    clear_user_override()


@pytest.mark.asyncio
async def test_create_labour_sets_company_id_and_allows_checkin():
    async with AsyncSessionLocal() as db:
        # Company
        c1 = await db.get(Company, 1)
        if not c1:
            c1 = Company(id=1, name="Test Company", is_active=True)
            db.add(c1)
            await db.flush()

        # Admin user in DB
        admin = await db.scalar(select(User).where(User.company_id == 1, User.role == UserRole.ADMIN.value))
        if not admin:
            admin = User(
                email="admin_labour_test@test.com",
                full_name="Admin Test",
                role=UserRole.ADMIN.value,
                is_active=True,
                company_id=1,
            )
            db.add(admin)
            await db.flush()

        admin_id = admin.id
        admin_obj = User(
            id=admin.id,
            email=admin.email,
            role=admin.role,
            is_active=admin.is_active,
            company_id=admin.company_id,
            is_super_admin=False,
        )

        owner = await db.scalar(select(Owner).where(Owner.company_id == 1))
        if not owner:
            owner = Owner(name="Owner Test", phone="9988776655", email="owner_test@test.com", company_id=1)
            db.add(owner)
            await db.flush()

        suffix = uuid.uuid4().hex[:6]
        project = Project(
            business_id=f"PRJ-CHK-{suffix}",
            company_id=1,
            project_name=f"Project Checkin {suffix}",
            owner_id=owner.id,
            status=ProjectStatus.ONGOING,
        )
        db.add(project)
        await db.flush()

        lt = await db.scalar(select(LabourType))
        if not lt:
            lt = LabourType(name="General Worker", skill_category=SkillType.SKILLED, default_wage=500)
            db.add(lt)
            await db.flush()

        await db.commit()
        project_id = project.id
        lt_id = lt.id

    # 1. Admin creates labour with project_id
    override_user(admin_obj)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        rand_mobile = f"9{uuid.uuid4().int % 1000000000:09d}"
        rand_aadhaar = f"8{uuid.uuid4().int % 100000000000:011d}"
        resp = await ac.post(
            f"/api/v1/labour?aadhaar_number={rand_aadhaar}&labour_name=Test%20Worker&mobile_number={rand_mobile}&labour_type_id={lt_id}&project_id={project_id}&status=Active",
        )
        assert resp.status_code == 200, resp.text
        labour_data = resp.json()
        assert labour_data["company_id"] == 1
        labour_user_id = labour_data["user_id"]
        assert labour_user_id is not None

        # Verify User in DB has company_id == 1
        async with AsyncSessionLocal() as db:
            created_user = await db.get(User, labour_user_id)
            assert created_user.company_id == 1
            assert created_user.role == UserRole.LABOUR.value

        # 2. Labour user logs in and does check-in
        labour_actor = User(
            id=labour_user_id,
            email=labour_data.get("email"),
            role=UserRole.LABOUR.value,
            is_active=True,
            is_super_admin=False,
            company_id=1,
        )
        override_user(labour_actor)

        check_in_resp = await ac.post(
            "/api/v1/attendance/check-in",
            data={
                "project_id": project_id,
                "check_in_address": "Pune Site",
                "check_in_latitude": 18.52,
                "check_in_longitude": 73.85,
            },
        )
        assert check_in_resp.status_code == 200, check_in_resp.text
        assert check_in_resp.json()["project_id"] == project_id
        assert check_in_resp.json()["user_id"] == labour_user_id
