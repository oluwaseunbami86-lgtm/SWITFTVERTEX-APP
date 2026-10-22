from django.urls import path

from . import views

app_name = "jobs"

urlpatterns = [
    path("", views.job_list, name="list"),
    path("new/", views.job_create, name="create"),
    path("<int:pk>/", views.job_detail, name="detail"),
    path("<int:pk>/edit/", views.job_update, name="update"),
    path("<int:pk>/archive/", views.job_archive, name="archive"),
    path("<int:pk>/restore/", views.job_restore, name="restore"),
    path("<int:pk>/delete/", views.job_delete, name="delete"),
    path("<int:pk>/purge/", views.job_purge, name="purge"),
]
