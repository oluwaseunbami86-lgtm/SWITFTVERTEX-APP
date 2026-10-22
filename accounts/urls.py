from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("team/", views.team_list, name="team"),
    path("profile/", views.my_profile, name="my_profile"),
    path("profile/password/", views.change_password, name="change_password"),
    path("profile/delete/", views.delete_account_info, name="delete_account_info"),
    path("pending-approvals/", views.pending_approvals, name="pending_approvals"),
    path("pending-approvals/<int:user_id>/", views.approval_action, name="approval_action"),
    path("users/", views.users_list, name="users"),
    path("users/<int:user_id>/status/", views.user_status_action, name="user_status"),
    path("users/<int:user_id>/role/", views.user_role_action, name="user_role"),
    path("users/<int:user_id>/password/", views.user_password_action, name="user_password"),
    path("users/<int:user_id>/delete/", views.user_delete_action, name="user_delete"),
    path("audit/", views.audit_log, name="audit_log"),
]
