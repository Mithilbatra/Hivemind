import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import logging_setup
from .agents import seed_agents
from .api import admin, hub, tasks
from .auth import hash_password, verify_password
from .config import get_settings
from .db import Base, engine, session_scope
from .models import Project, User


def bootstrap(create_tables: bool = False) -> None:
    """Seed the admin user, default agents and a default project. Idempotent."""
    s = get_settings()
    if create_tables:
        Base.metadata.create_all(engine())
    with session_scope() as db:
        admin = db.query(User).filter_by(username=s.admin_username).first()
        if admin is None:
            db.add(User(username=s.admin_username, password_hash=hash_password(s.admin_password)))
        elif not verify_password(s.admin_password, admin.password_hash):  # .env is the source of truth
            admin.password_hash = hash_password(s.admin_password)
        if not db.query(Project).first():
            db.add(Project(name="default", max_retries=s.max_retries))
        db.commit()
        seed_agents(db)


@asynccontextmanager
async def lifespan(app):
    bootstrap()
    yield


def create_app() -> FastAPI:
    logging_setup.setup()
    app = FastAPI(title="Code Orchestrator", lifespan=lifespan)
    app.include_router(admin.public, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(hub.router, prefix="/api")
    logging.getLogger("orchestrator").info("started")
    return app


app = create_app()
