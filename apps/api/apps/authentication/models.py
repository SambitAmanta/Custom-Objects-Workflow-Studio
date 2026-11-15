import hashlib
import uuid

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class RoleChoices(models.TextChoices):
    ADMIN = "admin", "Admin"
    EDITOR = "editor", "Editor"
    VIEWER = "viewer", "Viewer"


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=100, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auth_tenants"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.slug})"


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        db_table = "auth_users"
        ordering = ["-created_at"]

    def __str__(self):
        return self.email


class TenantMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(
        max_length=20,
        choices=RoleChoices.choices,
        default=RoleChoices.VIEWER,
    )
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "auth_tenant_memberships"
        unique_together = ("user", "tenant")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.email} -> {self.tenant.slug} ({self.role})"


class ApiKey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=100)
    key_prefix = models.CharField(max_length=16, db_index=True)
    key_hash = models.CharField(max_length=64, db_index=True)
    role = models.CharField(
        max_length=20,
        choices=RoleChoices.choices,
        default=RoleChoices.EDITOR,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "auth_api_keys"
        ordering = ["-created_at"]

    @classmethod
    def generate(
        cls, tenant: Tenant, name: str, role: str = RoleChoices.EDITOR, expires_at=None
    ) -> tuple["ApiKey", str]:
        """Generates a secure API key, returns (ApiKey instance, raw_secret).
        Raw secret is shown only once and never stored unhashed.
        """
        raw_token = uuid.uuid4().hex + uuid.uuid4().hex
        prefix = f"fg_{raw_token[:8]}"
        raw_key = f"{prefix}_{raw_token[8:]}"
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

        api_key = cls.objects.create(
            tenant=tenant,
            name=name,
            key_prefix=prefix,
            key_hash=key_hash,
            role=role,
            expires_at=expires_at,
        )
        return api_key, raw_key

    @property
    def is_valid(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return self.tenant.is_active
