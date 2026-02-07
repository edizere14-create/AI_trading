from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.auth import Token
from app.schemas.user import UserCreate, UserRead
from app.services.auth_service import authenticate_user, create_access_token
from app.services.user_service import create_user
from app.utils.dependencies import get_db_dep

router = APIRouter()

@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db_dep),
) -> Token:
    """Authenticate user and return access token."""
    user = await authenticate_user(form_data.username, form_data.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": user.username})
    return Token(access_token=access_token, token_type="bearer")

@router.post("/signup", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def signup(
    user_create: UserCreate,
    db: AsyncSession = Depends(get_db_dep),
) -> UserRead:
    """Register a new user."""
    try:
        new_user = await create_user(db, user_create)
        return UserRead.from_orm(new_user)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
