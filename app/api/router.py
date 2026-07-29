from fastapi import APIRouter

from app.api.routes import auth, diagnoses, patients, sync

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(patients.router)
api_router.include_router(diagnoses.router)
api_router.include_router(sync.router)
