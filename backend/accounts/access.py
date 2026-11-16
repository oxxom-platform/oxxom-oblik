"""Organization authorization. HTTP callers must also enforce verified MFA."""
from django.core.exceptions import PermissionDenied
from .models import Membership


ROLE_PERMISSIONS = {
    Membership.Role.OWNER: frozenset({"organization.manage", "members.manage", "records.read", "sales.draft", "stock.draft", "documents.post", "payments.manage", "reports.read", "periods.close"}),
    Membership.Role.ACCOUNTANT: frozenset({"records.read", "sales.draft", "stock.draft", "documents.post", "payments.manage", "reports.read", "periods.close"}),
    Membership.Role.MANAGER: frozenset({"records.read", "sales.draft"}),
    Membership.Role.STOREKEEPER: frozenset({"stock.read", "stock.draft"}),
    Membership.Role.VIEWER: frozenset({"records.read"}),
}


# Explicit catalog permissions; warehouse staff cannot read sales prices or counterparties.
for role in (Membership.Role.OWNER, Membership.Role.ACCOUNTANT):
    ROLE_PERMISSIONS[role] |= {'organization.details'}
    ROLE_PERMISSIONS[role] |= frozenset(f"catalog.{kind}.{action}" for kind in ("units", "products", "counterparties", "warehouses") for action in ("read", "write"))
for role in (Membership.Role.MANAGER, Membership.Role.VIEWER):
    ROLE_PERMISSIONS[role] |= frozenset(f"catalog.{kind}.read" for kind in ("units", "products", "counterparties", "warehouses"))
ROLE_PERMISSIONS[Membership.Role.MANAGER] |= {"catalog.counterparties.write"}
ROLE_PERMISSIONS[Membership.Role.STOREKEEPER] |= {"catalog.units.read", "catalog.products.read", "catalog.warehouses.read", "catalog.warehouses.write"}


def memberships_for(user):
    # A global superuser flag never grants cross-organization business access.
    if not user.is_authenticated or not user.is_active:
        return Membership.objects.none()
    return Membership.objects.filter(user=user, is_active=True, organization__is_active=True).select_related("organization")


def require_permission(user, organization_id, permission):
    membership = memberships_for(user).filter(organization_id=organization_id).first()
    if membership is None or permission not in ROLE_PERMISSIONS.get(membership.role, frozenset()):
        raise PermissionDenied("Недостатньо прав доступу.")
    return membership
