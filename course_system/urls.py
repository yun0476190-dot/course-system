# course_system/urls.py
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from courses.views import UserPasswordChangeView, login_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", login_view, name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password/", UserPasswordChangeView.as_view(), name="password_change"),
    path("", include("courses.urls")),
]