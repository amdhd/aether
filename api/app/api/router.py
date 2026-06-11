from fastapi import APIRouter

from app.api.routes import auth, conversations, notes, tasks

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(tasks.router)
api_router.include_router(notes.router)
api_router.include_router(conversations.router)
