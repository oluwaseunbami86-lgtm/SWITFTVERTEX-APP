from django.urls import path

from . import views

app_name = "documents"

urlpatterns = [
    path("", views.document_list, name="list"),
    path("<int:pk>/", views.document_detail, name="detail"),
    path("job/<int:job_pk>/upload/", views.document_upload, name="upload"),
    path("version/<int:pk>/download/", views.version_download, name="download"),
    path("version/<int:pk>/make-current/", views.version_make_current, name="make_current"),
    path("<int:pk>/verify/", views.document_verify, name="verify"),
    path("<int:pk>/delete/", views.document_delete, name="delete"),
    path("<int:pk>/restore/", views.document_restore, name="restore"),
    path("<int:pk>/purge/", views.document_purge, name="purge"),
]
