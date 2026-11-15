from apps.records import views
from django.urls import path

urlpatterns = [
    path("<slug:type_slug>/", views.list_or_create_records, name="records-list-create"),
    path("<slug:type_slug>/<str:record_id>/", views.record_detail, name="record-detail"),
]
