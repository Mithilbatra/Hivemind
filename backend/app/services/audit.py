from ..models import AuditEvent


def emit(db, task_id: int | None, type: str, message: str = "", payload: dict | None = None, actor: str = "system"):
    """Append an audit event. Every state change and executed command goes through here."""
    ev = AuditEvent(task_id=task_id, type=type, message=message[:2000], payload=payload or {}, actor=actor)
    db.add(ev)
    db.flush()
    return ev
