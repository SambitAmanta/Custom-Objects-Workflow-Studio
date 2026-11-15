from apps.schemas import views
from django.urls import path

urlpatterns = [
    path("types/", views.list_or_create_types, name="schema-types-list-create"),
    path("types/<slug:slug>/", views.get_type_detail, name="schema-type-detail"),
    path(
        "types/<slug:slug>/compiled/", views.get_compiled_json_schema, name="schema-type-compiled"
    ),
    path("types/<slug:slug>/versions/", views.list_type_versions, name="schema-type-versions"),
    path("types/<slug:slug>/publish/", views.publish_type, name="schema-type-publish"),
    path(
        "types/<slug:slug>/compat-check/",
        views.check_compatibility,
        name="schema-type-compat-check",
    ),
]
