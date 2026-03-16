from app.api.admin import router as admin_router
from fastapi import FastAPI
from app.api.health import router as health_router
from app.api.telegram import router as telegram_router
from app.api.groups import router as groups_router
from app.api.join_requests import router as join_requests_router
from app.api.users import router as users_router
from app.api.surveys import router as surveys_router
from app.api.survey_sessions import router as survey_sessions_router

app = FastAPI()

app.include_router(health_router, prefix="/api/v1")
app.include_router(telegram_router, prefix="/api/v1")
app.include_router(groups_router, prefix="/api/v1")
app.include_router(join_requests_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(surveys_router, prefix="/api/v1")
app.include_router(survey_sessions_router, prefix="/api/v1")
app.include_router(admin_router)


@app.get("/health")
def health():
    return {"ok": True}
