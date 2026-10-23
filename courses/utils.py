# courses/utils.py
"""views 共用的小工具：身分判斷、操作紀錄與分頁。"""
import ipaddress

from django.core.paginator import Paginator
from django.db.models import Count

from .models import ActivityLog


# 身分判斷：只有管理員身分會放行 superuser，學生與教師頁面一律看 role
def is_student(user):
    return user.is_authenticated and user.is_student


def is_teacher(user):
    return user.is_authenticated and user.is_teacher


def is_admin(user):
    return user.is_authenticated and user.is_academic_admin


def can_manage_course(user, course):
    """管理員可管理任一門課；教師只能管理自己的課。"""
    if is_admin(user):
        return True
    return is_teacher(user) and course.teacher_id == user.id


def client_ip(request):
    """取得來源 IP。X-Forwarded-For 可被偽造，僅供參考；格式不對就改用連線位址。"""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    for candidate in (forwarded.split(",")[0].strip(), request.META.get("REMOTE_ADDR")):
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            continue
    return None


def log_action(request, action, target="", detail=""):
    """寫入操作紀錄，操作者與 IP 取自目前的請求。"""
    actor = request.user if request.user.is_authenticated else None
    ActivityLog.record(action, target, detail, actor=actor, ip_address=client_ip(request))


def paginate(request, queryset, per_page=20):
    """依網址的 ?page= 回傳分頁物件；頁碼不合法時自動修正。"""
    return Paginator(queryset, per_page).get_page(request.GET.get("page"))


def with_course_counts(queryset):
    """課程加上選課人數 num_enrolled 與候補人數 num_waiting（兩個計數都要 distinct，避免互相放大）。"""
    return queryset.annotate(
        num_enrolled=Count("enrollments", distinct=True),
        num_waiting=Count("waitlist_entries", distinct=True),
    )
