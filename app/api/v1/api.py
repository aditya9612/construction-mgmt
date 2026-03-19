from fastapi import APIRouter

from app.api.v1.endpoints.ai import router as ai_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.document import router as document_router
from app.api.v1.endpoints.equipment import router as equipment_router
from app.api.v1.endpoints.labour import router as labour_router
from app.api.v1.endpoints.material import router as material_router
from app.api.v1.endpoints.project import router as project_router
from app.api.v1.endpoints.boq import router as boq_router
from app.api.v1.endpoints.user import router as user_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["auth"])
api_router.include_router(user_router, prefix="/users", tags=["users"])

api_router.include_router(project_router, prefix="/projects")
api_router.include_router(boq_router, prefix="/boq")
api_router.include_router(material_router, prefix="/materials")
api_router.include_router(labour_router, prefix="/labour")
api_router.include_router(equipment_router, prefix="/equipment")
api_router.include_router(document_router, prefix="/documents")
api_router.include_router(ai_router, prefix="/ai")

