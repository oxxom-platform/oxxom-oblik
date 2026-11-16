from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import Membership, Organization, User


@transaction.atomic
def create_owner(*, email, password, organization_name):
    """Administrative provisioning only; never upgrades an existing account."""
    name = organization_name.strip()
    if not name:
        raise ValidationError("Вкажіть назву організації.")
    user = User(email=email.strip().lower())
    user.set_password(password)
    user.full_clean()
    validate_password(password, user=user)
    organization = Organization(name=name)
    organization.full_clean()
    user.save()
    organization.save()
    Membership.objects.create(user=user, organization=organization, role=Membership.Role.OWNER)
    from catalog.defaults import ensure_initial_units
    ensure_initial_units(organization)
    return user, organization
