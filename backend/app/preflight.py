"""Refuse to start with a missing or example password: code an agent writes runs next to the worker and can reach
Postgres, Redis and the API over the network, so a known password would let it in."""
import sys
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

from .config import get_settings

EXAMPLES = {"", "change-me", "admin", "orchestrator"}


def problems(s, admin: bool = True) -> list[str]:
    bad = []
    if admin and s.admin_password in EXAMPLES:
        bad.append("ADMIN_PASSWORD")
    db = make_url(s.database_url)
    if db.get_backend_name() != "sqlite" and (db.password or "") in EXAMPLES:
        bad.append("POSTGRES_PASSWORD (and the password in DATABASE_URL)")
    if (urlparse(s.redis_url).password or "") in EXAMPLES:
        bad.append("REDIS_PASSWORD")
    return bad


def check(admin: bool = True) -> None:
    if bad := problems(get_settings(), admin):
        sys.exit(f"Refusing to start: {', '.join(bad)} unset or still the example value, and code an agent writes can "
                 "reach these services. Put random values in .env (openssl rand -hex 16); `make setup` adds a missing "
                 "REDIS_PASSWORD. A database created with the old password also needs: docker compose exec db "
                 "psql -U orchestrator -c \"ALTER USER orchestrator PASSWORD '<new>'\"")


if __name__ == "__main__":
    check()
