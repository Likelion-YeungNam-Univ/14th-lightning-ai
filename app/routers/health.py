from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter(tags=["health"])

# 이후 모든 라우터에서 이 별칭을 재사용한다 (Annotated 의존성 주입 — B008 회피)
DbDep = Annotated[Session, Depends(get_db)]


@router.get("/health")
def health(db: DbDep) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "ok"}
