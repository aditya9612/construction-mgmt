from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.ai_prediction import AIPredictRequest, AIPredictResponse, AIPredictionOut
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.schemas.boq import BOQCreate, BOQOut, BOQUpdate
from app.schemas.document import DocumentCreate, DocumentOut, DocumentUpdate
from app.schemas.equipment import EquipmentCreate, EquipmentOut, EquipmentUpdate
from app.schemas.labour import LabourCreate, LabourOut, LabourUpdate
from app.schemas.material import MaterialCreate, MaterialOut, MaterialUpdate
from app.schemas.project import ProjectCreate, ProjectOut, ProjectUpdate
from app.schemas.token import AuthResponse
from app.schemas.user import UserCreate, UserLogin, UserOut

# -----------------------------------------------------------------------------
# Router
# -----------------------------------------------------------------------------
router = APIRouter(prefix="/common", dependencies=[default_rate_limiter_dependency()])

# -----------------------------------------------------------------------------
# Pagination helpers (reference)
# -----------------------------------------------------------------------------
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200


def _get_pagination_params(payload: dict):
    """Return (page, page_size, get_all)."""
    get_all = (payload or {}).get("get_all", False)
    if isinstance(get_all, str):
        get_all = get_all.lower() in ("true", "1", "yes", "all")
    if get_all:
        return 1, MAX_PAGE_SIZE, True
    page_val = (payload or {}).get("page", DEFAULT_PAGE)
    size_val = (payload or {}).get("page_size", DEFAULT_PAGE_SIZE)
    try:
        page = int(page_val)
    except (TypeError, ValueError):
        page = DEFAULT_PAGE
    try:
        page_size = int(size_val)
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    return max(1, page), max(1, min(page_size, MAX_PAGE_SIZE)), False


# -----------------------------------------------------------------------------
# AUTH
# -----------------------------------------------------------------------------
@router.post("/auth/signup", response_model=AuthResponse)
async def signup(payload: UserCreate, db: AsyncSession = Depends(get_db_session)):
    """Ref: auth signup - validate, hash password, create user, return token."""
    ...


@router.post("/auth/login", response_model=AuthResponse)
async def login(payload: UserLogin, db: AsyncSession = Depends(get_db_session)):
    """Ref: auth login - validate creds, return token."""
    ...


# -----------------------------------------------------------------------------
# USERS
# -----------------------------------------------------------------------------
@router.get("/users/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_active_user)):
    """Ref: return current user."""
    return current_user


@router.get("/users", response_model=PaginatedResponse[UserOut])
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
):
    """Ref: list users with pagination, optional search."""
    ...


# -----------------------------------------------------------------------------
# PROJECTS
# -----------------------------------------------------------------------------
@router.post("/projects", response_model=ProjectOut)
async def create_project(
    payload: ProjectCreate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: create project."""
    ...


@router.get("/projects", response_model=PaginatedResponse[ProjectOut])
async def list_projects(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: list projects."""
    ...


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: get project by id."""
    ...


@router.put("/projects/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: update project."""
    ...


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: delete project."""
    ...


# -----------------------------------------------------------------------------
# BOQ
# -----------------------------------------------------------------------------
@router.post("/boq", response_model=BOQOut)
async def create_boq(
    payload: BOQCreate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: create BOQ."""
    ...


@router.get("/boq", response_model=PaginatedResponse[BOQOut])
async def list_boq(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: list BOQ."""
    ...


@router.get("/boq/{boq_id}", response_model=BOQOut)
async def get_boq(
    boq_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: get BOQ by id."""
    ...


@router.put("/boq/{boq_id}", response_model=BOQOut)
async def update_boq(
    boq_id: int,
    payload: BOQUpdate,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.ACCOUNTANT])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: update BOQ."""
    ...


@router.delete("/boq/{boq_id}", status_code=204)
async def delete_boq(
    boq_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: delete BOQ."""
    ...


# -----------------------------------------------------------------------------
# MATERIALS
# -----------------------------------------------------------------------------
@router.post("/materials", response_model=MaterialOut)
async def create_material(
    payload: MaterialCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: create material."""
    ...


@router.get("/materials", response_model=PaginatedResponse[MaterialOut])
async def list_materials(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: list materials."""
    ...


@router.get("/materials/{material_id}", response_model=MaterialOut)
async def get_material(
    material_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: get material by id."""
    ...


@router.put("/materials/{material_id}", response_model=MaterialOut)
async def update_material(
    material_id: int,
    payload: MaterialUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: update material."""
    ...


@router.delete("/materials/{material_id}", status_code=204)
async def delete_material(
    material_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: delete material."""
    ...


# -----------------------------------------------------------------------------
# LABOUR
# -----------------------------------------------------------------------------
@router.post("/labour", response_model=LabourOut)
async def create_labour(
    payload: LabourCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: create labour."""
    ...


@router.get("/labour", response_model=PaginatedResponse[LabourOut])
async def list_labour(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: list labour."""
    ...


@router.get("/labour/{labour_id}", response_model=LabourOut)
async def get_labour(
    labour_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: get labour by id."""
    ...


@router.put("/labour/{labour_id}", response_model=LabourOut)
async def update_labour(
    labour_id: int,
    payload: LabourUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: update labour."""
    ...


@router.delete("/labour/{labour_id}", status_code=204)
async def delete_labour(
    labour_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: delete labour."""
    ...


# -----------------------------------------------------------------------------
# EQUIPMENT
# -----------------------------------------------------------------------------
@router.post("/equipment", response_model=EquipmentOut)
async def create_equipment(
    payload: EquipmentCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: create equipment."""
    ...


@router.get("/equipment", response_model=PaginatedResponse[EquipmentOut])
async def list_equipment(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: list equipment."""
    ...


@router.get("/equipment/{equipment_id}", response_model=EquipmentOut)
async def get_equipment(
    equipment_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: get equipment by id."""
    ...


@router.put("/equipment/{equipment_id}", response_model=EquipmentOut)
async def update_equipment(
    equipment_id: int,
    payload: EquipmentUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER, UserRole.CONTRACTOR])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: update equipment."""
    ...


@router.delete("/equipment/{equipment_id}", status_code=204)
async def delete_equipment(
    equipment_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: delete equipment."""
    ...


# -----------------------------------------------------------------------------
# DOCUMENTS
# -----------------------------------------------------------------------------
@router.post("/documents", response_model=DocumentOut)
async def create_document(
    payload: DocumentCreate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: create document."""
    ...


@router.get("/documents", response_model=PaginatedResponse[DocumentOut])
async def list_documents(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    document_type: Optional[str] = None,
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: list documents."""
    ...


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: get document by id."""
    ...


@router.put("/documents/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: int,
    payload: DocumentUpdate,
    current_user: User = Depends(
        require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER, UserRole.SITE_ENGINEER])
    ),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: update document."""
    ...


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: delete document."""
    ...


# -----------------------------------------------------------------------------
# AI
# -----------------------------------------------------------------------------
@router.post("/ai/predict", response_model=AIPredictResponse)
async def predict(
    payload: AIPredictRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: AI predict - call model, store prediction."""
    ...


@router.get("/ai", response_model=PaginatedResponse[AIPredictionOut])
async def list_predictions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    module_name: Optional[str] = None,
    search: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: list AI predictions."""
    ...


@router.get("/ai/{prediction_id}", response_model=AIPredictionOut)
async def get_prediction(
    prediction_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: get prediction by id."""
    ...


@router.put("/ai/{prediction_id}", response_model=AIPredictionOut)
async def update_prediction(
    prediction_id: int,
    payload: Dict[str, Any],
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: update prediction."""
    ...


@router.delete("/ai/{prediction_id}", status_code=204)
async def delete_prediction(
    prediction_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    """Ref: delete prediction."""
    ...
