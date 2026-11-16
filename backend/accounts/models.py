from uuid import uuid4
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        if not email or not email.strip():
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).strip().lower(), **extra)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Superuser flags must be true")
        if not password:
            raise ValueError("Superuser password is required")
        return self.create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    security_stamp = models.UUIDField(default=uuid4, editable=False)
    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="account_email_ci_unique")]

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        previous = type(self).objects.filter(pk=self.pk).values("is_active").first()
        if previous and previous["is_active"] != self.is_active:
            self.security_stamp = uuid4()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"security_stamp"}
        return super().save(*args, **kwargs)

    def _get_session_auth_hash(self, secret=None):
        from django.utils.crypto import salted_hmac
        return salted_hmac("accounts.User.session", f"{self.password}:{self.security_stamp}", secret=secret, algorithm="sha256").hexdigest()


class Organization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    name = models.CharField(max_length=200)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Власник"
        ACCOUNTANT = "accountant", "Бухгалтер"
        MANAGER = "manager", "Менеджер"
        STOREKEEPER = "storekeeper", "Комірник"
        VIEWER = "viewer", "Спостерігач"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "organization"], name="membership_user_org_unique"),
            models.CheckConstraint(condition=models.Q(role__in=["owner", "accountant", "manager", "storekeeper", "viewer"]), name="membership_valid_role"),
        ]


class TOTPDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    secret = models.TextField()
    confirmed = models.BooleanField(default=False)
    last_step = models.BigIntegerField(default=-1)


class RecoveryCode(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=256)


class AuthThrottle(models.Model):
    key = models.CharField(max_length=64, primary_key=True)
    window_started = models.FloatField(default=0)
    failures = models.PositiveIntegerField(default=0)


class AccessEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    actor = models.ForeignKey(User, on_delete=models.PROTECT)
    target = models.ForeignKey(Membership, on_delete=models.PROTECT)
    action = models.CharField(max_length=32)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
