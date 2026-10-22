# courses/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Course, Enrollment, StudentProfile, TeacherProfile, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("身分資訊", {"fields": ("role", "display_name")}),
    )
    list_display = ("username", "display_name", "role", "is_staff")
    list_filter = ("role", "is_staff")


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("student_no", "user", "department", "max_credits")
    search_fields = ("student_no", "user__display_name")


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ("teacher_no", "user", "department")
    search_fields = ("teacher_no", "user__display_name")


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = (
        "code", "name", "teacher", "credits", "category",
        "weekday", "start_period", "end_period", "classroom", "capacity",
    )
    list_filter = ("category", "department", "weekday")
    search_fields = ("code", "name")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "enrolled_at")
    list_filter = ("course",)
    search_fields = ("student__username", "course__name")