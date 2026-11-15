from apps.records import views
from apps.workflows import views as workflow_views
from django.urls import path

urlpatterns = [
    path("<slug:type_slug>/", views.list_or_create_records, name="records-list-create"),
    path("<slug:type_slug>/<str:record_id>/", views.record_detail, name="record-detail"),
    path(
        "<slug:type_slug>/<str:record_id>/transitions/",
        workflow_views.list_transitions,
        name="record-transitions-list",
    ),
    path(
        "<slug:type_slug>/<str:record_id>/transitions/<str:transition_name>/",
        workflow_views.execute_transition,
        name="record-transition-execute",
    ),
]
