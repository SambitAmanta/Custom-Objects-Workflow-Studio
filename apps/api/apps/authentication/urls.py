from apps.authentication import views
from django.urls import path

urlpatterns = [
    path("token/", views.obtain_token, name="auth-token"),
    path("tenants/", views.list_user_tenants, name="auth-tenants"),
    path("api-keys/", views.create_api_key, name="auth-create-api-key"),
]
