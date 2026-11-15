import secrets
import uuid

from apps.authentication.models import RoleChoices, Tenant
from django.db import models


def generate_webhook_secret() -> str:
    return secrets.token_hex(32)


class WebhookEndpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="webhook_endpoints")
    url = models.URLField(max_length=500)
    secret = models.CharField(max_length=128, default=generate_webhook_secret)
    role = models.CharField(max_length=20, choices=RoleChoices.choices, default=RoleChoices.VIEWER)
    topics = models.JSONField(
        default=list, help_text="List of topic patterns e.g. ['records.invoice.*']"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "webhook_endpoints"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant.slug} -> {self.url}"
