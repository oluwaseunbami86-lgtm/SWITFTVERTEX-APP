from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.reports_home, name="home"),
    path("export/jobs.csv", views.export_jobs_csv, name="export_jobs_csv"),
    path("export/jobs.docx", views.export_jobs_docx, name="export_jobs_docx"),
]
