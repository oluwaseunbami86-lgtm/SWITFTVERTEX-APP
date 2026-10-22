from django.urls import path

from . import views

app_name = "survey_points"

urlpatterns = [
    path("job/<int:job_pk>/new/", views.survey_point_create, name="create"),
    path("job/<int:job_pk>/import/cancel/", views.survey_point_import_cancel, name="import_cancel"),
    path("job/<int:job_pk>/import/mapping/", views.survey_point_import_mapping, name="import_mapping"),
]
