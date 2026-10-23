# courses/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    ActivityLog, Classroom, Course, Enrollment, StudentProfile, SystemSetting,
    TeacherProfile, User, WaitlistEntry,
)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ("身分資訊", {"fields": ("role", "display_name")}),
    )
    list_display = ("username", "display_name", "role", "is_staff")
    list_filter = ("role", "is_staff")


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("student_no", "user", "department", "grade", "min_credits", "max_credits")
    list_filter = ("grade", "department")
    search_fields = ("student_no", "user__display_name")


@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ("teacher_no", "user", "department")
    search_fields = ("teacher_no", "user__display_name")


@admin.register(Classroom)
class ClassroomAdmin(admin.ModelAdmin):
    list_display = ("name", "capacity")
    search_fields = ("name",)


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


@admin.register(WaitlistEntry)
class WaitlistEntryAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "created_at")
    list_filter = ("course",)
    search_fields = ("student__username", "course__name")


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    """操作紀錄只供查詢，不能在後台修改或刪除。"""

    list_display = ("created_at", "actor", "action", "target", "ip_address")
    list_filter = ("action",)
    search_fields = ("actor__username", "target", "detail")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("__str__", "enrollment_start", "enrollment_end", "semester_start", "semester_end")
