from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.user import UserRead, UserCreate
from app.services.user_service import create_user
from app.utils.dependencies import get_db_dep, get_current_user
from app.db.models.user import User

router = APIRouter()

@router.get("/me", response_model=UserRead)
async def get_me(user: User = Depends(get_current_user)) -> UserRead:
    """Get current user profile."""
    return UserRead.from_orm(user)

@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
    user_create: UserCreate,
    db: AsyncSession = Depends(get_db_dep),
) -> UserRead:
    """Register new user."""
    try:
        new_user = await create_user(db, user_create)
        return UserRead.from_orm(new_user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )