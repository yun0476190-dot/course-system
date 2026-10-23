# courses/urls.py
from django.urls import path
from django.views.generic import TemplateView

from . import admin_views, views

urlpatterns = [
    path("", TemplateView.as_view(template_name="courses/home.html"), name="home"),

    # 學生
    path("timetable/", views.my_timetable, name="my_timetable"),
    path("timetable/calendar.ics", views.timetable_calendar, name="timetable_calendar"),
    path("courses/", views.course_list, name="course_list"),
    path("courses/<int:course_id>/", views.course_detail, name="course_detail"),
    path("courses/<int:course_id>/enroll/", views.enroll, name="enroll"),
    path("courses/<int:course_id>/drop/", views.drop, name="drop"),
    path("courses/<int:course_id>/waitlist/", views.waitlist_join, name="waitlist_join"),
    path("courses/<int:course_id>/waitlist/leave/", views.waitlist_leave, name="waitlist_leave"),

    # 教師（開課、編輯、刪除與名單匯出也開放給管理員）
    path("teacher/", views.teacher_courses, name="teacher_courses"),
    path("teacher/courses/<int:course_id>/", views.teacher_course_detail, name="teacher_course_detail"),
    path("teacher/courses/<int:course_id>/roster.csv", views.course_roster_csv, name="course_roster_csv"),
    path("teacher/courses/new/", views.course_create, name="course_create"),
    path("teacher/courses/<int:course_id>/edit/", views.course_edit, name="course_edit"),
    path("teacher/courses/<int:course_id>/delete/", views.course_delete, name="teacher_course_delete"),

    # 管理員
    path("manage/", admin_views.admin_dashboard, name="admin_dashboard"),
    path("manage/students/", admin_views.admin_students, name="admin_students"),
    path("manage/students/new/", admin_views.admin_student_create, name="admin_student_create"),
    path("manage/students/<int:student_id>/edit/", admin_views.admin_student_edit, name="admin_student_edit"),
    path("manage/students/<int:student_id>/credits/", admin_views.admin_set_credits, name="admin_set_credits"),
    path("manage/teachers/", admin_views.admin_teachers, name="admin_teachers"),
    path("manage/teachers/new/", admin_views.admin_teacher_create, name="admin_teacher_create"),
    path("manage/teachers/<int:teacher_id>/edit/", admin_views.admin_teacher_edit, name="admin_teacher_edit"),
    path("manage/users/<int:user_id>/toggle/", admin_views.admin_toggle_account, name="admin_toggle_account"),
    path("manage/users/<int:user_id>/password/", admin_views.admin_reset_password, name="admin_reset_password"),
    path("manage/courses/", admin_views.admin_courses, name="admin_courses"),
    path("manage/courses/new/", views.course_create, name="admin_course_create"),
    path("manage/courses/<int:course_id>/edit/", views.course_edit, name="admin_course_edit"),
    path("manage/courses/<int:course_id>/delete/", views.course_delete, name="admin_course_delete"),
    path("manage/classrooms/", admin_views.admin_classrooms, name="admin_classrooms"),
    path("manage/classrooms/new/", admin_views.admin_classroom_create, name="admin_classroom_create"),
    path("manage/classrooms/<int:room_id>/edit/", admin_views.admin_classroom_edit, name="admin_classroom_edit"),
    path("manage/classrooms/<int:room_id>/delete/", admin_views.admin_classroom_delete, name="admin_classroom_delete"),
    path("manage/enrollments/", admin_views.admin_enrollments, name="admin_enrollments"),
    path("manage/enrollments/<int:enrollment_id>/drop/", admin_views.admin_force_drop, name="admin_force_drop"),
    path("manage/logs/", admin_views.admin_logs, name="admin_logs"),
    path("manage/import/", admin_views.admin_import, name="admin_import"),
    path("manage/import/<str:kind>-template.csv", admin_views.admin_import_template, name="admin_import_template"),
    path("manage/settings/", admin_views.admin_settings, name="admin_settings"),
]
