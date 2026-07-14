"""Shared FastAPI dependency aliases.

Annotated aliases keep `Depends(...)` out of argument defaults, which is both the current
FastAPI idiom and what lets routers stay readable as the signatures grow.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from lgapp.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
