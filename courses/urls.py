# courses/urls.py
from django.urls import path
from django.views.generic import TemplateView

from . import views

urlpatterns = [
    path("", TemplateView.as_view(template_name="courses/home.html"), name="home"),
    path("timetable/", views.my_timetable, name="my_timetable"),
    path("courses/", views.course_list, name="course_list"),
    path("courses/<int:course_id>/", views.course_detail, name="course_detail"),
    path("courses/<int:course_id>/enroll/", views.enroll, name="enroll"),
    path("courses/<int:course_id>/drop/", views.drop, name="drop"),
    path("teacher/", views.teacher_courses, name="teacher_courses"),
    path("teacher/courses/<int:course_id>/", views.teacher_course_detail, name="teacher_course_detail"),
    path("teacher/courses/new/", views.course_create, name="course_create"),
    path("teacher/courses/<int:course_id>/edit/", views.course_edit, name="course_edit"),
    path("teacher/courses/<int:course_id>/delete/", views.course_delete, name="teacher_course_delete"),
    path("manage/courses/<int:course_id>/delete/", views.course_delete, name="admin_course_delete"),    path("manage/", views.admin_dashboard, name="admin_dashboard"),
    path("manage/courses/new/", views.course_create, name="admin_course_create"),
    path("manage/courses/<int:course_id>/edit/", views.course_edit, name="admin_course_edit"),
    path("manage/enrollments/<int:enrollment_id>/drop/", views.admin_force_drop, name="admin_force_drop"),
    path("manage/students/<int:student_id>/max-credits/", views.admin_set_max_credits, name="admin_set_max_credits"),
]