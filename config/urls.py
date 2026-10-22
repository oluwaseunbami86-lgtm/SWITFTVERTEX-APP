from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView

from accounts import views as account_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("welcome/", account_views.welcome, name="welcome"),
    path("sign-in/", account_views.SVLoginView.as_view(), name="login"),
    path("sign-up/", account_views.signup, name="signup"),
    path("sign-up/pending/", account_views.signup_pending, name="signup_pending"),
    path("login/", RedirectView.as_view(pattern_name="login", query_string=True)),
    path("signup/", RedirectView.as_view(pattern_name="signup", query_string=True)),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("forgot-password/", account_views.SVPasswordResetView.as_view(), name="password_reset"),
    path("forgot-password/sent/", account_views.SVPasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", account_views.SVPasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", account_views.SVPasswordResetCompleteView.as_view(), name="password_reset_complete"),

    path("", include("dashboard.urls")),
    path("clients/", include("clients.urls")),
    path("jobs/", include("jobs.urls")),
    path("survey-points/", include("survey_points.urls")),
    path("documents/", include("documents.urls")),
    path("reports/", include("reports.urls")),
    path("accounts/", include("accounts.urls")),
    path("locations/", include("locations.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

handler403 = "accounts.views.permission_denied"
