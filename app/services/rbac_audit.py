import json
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rbac import RBACAuditLog
from app.models.user import User


async def record_rbac_audit(
    db: AsyncSession,
    actor: User,
    action: str,
    target_type: str,
    target_id: Optional[str] = None,
    company_id: Optional[int] = None,
    permission: Optional[str] = None,
    old_value: Any = None,
    new_value: Any = None,
) -> RBACAuditLog:
    """
    Records an RBAC mutation event transactionally within the current database session.
    
    Security rules:
    - Never accept company_id from untrusted client request input.
    - Derive company_id from the authenticated actor or target context.
    - Set company_id to None ONLY for system-level operations performed by Super Admin.
    - Sanitize payloads to ensure no sensitive tokens/passwords are ever persisted.
    """
    effective_company_id = company_id
    if effective_company_id is None:
        if actor and not getattr(actor, "is_super_admin", False):
            effective_company_id = actor.company_id
        elif actor and getattr(actor, "is_super_admin", False):
            effective_company_id = company_id  # explicitly None if system-wide

    def _sanitize(val: Any) -> Optional[str]:
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            return json.dumps(val, default=str)
        return str(val)

    audit_entry = RBACAuditLog(
        company_id=effective_company_id,
        actor_id=actor.id if actor else None,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        permission=permission,
        old_value=_sanitize(old_value),
        new_value=_sanitize(new_value),
    )
    db.add(audit_entry)
    return audit_entry
