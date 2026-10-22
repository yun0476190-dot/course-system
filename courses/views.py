# courses/views.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .forms import CourseForm
from .models import ActivityLog, Course, Enrollment, StudentProfile


WEEKDAYS = [(1, "週一"), (2, "週二"), (3, "週三"), (4, "週四"), (5, "週五")]
PERIODS = list(range(1, 15))


# ---------- 共用工具 ----------

def _student_enrollments(user):
    return (
        Enrollment.objects.filter(student=user)
        .select_related("course__teacher")
        .order_by("course__weekday", "course__start_period")
    )


def _respond(request, ok, message, course_id):
    """AJAX 請求回傳 JSON；一般表單請求沿用原本的跳轉行為。"""
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": ok, "message": message, "course_id": course_id})
    if ok:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect(f"{reverse('course_list')}#course-{course_id}")


def _require_role(user, role):
    return user.is_authenticated and (
        getattr(user, "role", None) == role or user.is_superuser
    )


def _is_admin(user):
    return _require_role(user, "admin")


def _is_teacher(user):
    return _require_role(user, "teacher")


def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _log(request, action, target="", detail=""):
    """寫入操作紀錄。"""
    ActivityLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        target=target[:200],
        detail=detail[:255],
        ip_address=_client_ip(request),
    )


def _can_manage_course(user, course):
    """管理員可管理任一門課；教師只能管理自己的課。"""
    if _is_admin(user):
        return True
    return _is_teacher(user) and course.teacher_id == user.id


# ---------- 學生 ----------

@login_required
def my_timetable(request):
    if not _require_role(request.user, "student"):
        return redirect("home")
    enrollments = list(_student_enrollments(request.user))
    total_credits = sum(e.course.credits for e in enrollments)

    category_labels = dict(Course.Category.choices)
    category_credits = {key: 0 for key in category_labels}
    for e in enrollments:
        category_credits[e.course.category] += e.course.credits

    category_summary = [
        {"label": category_labels[key], "credits": category_credits[key]}
        for key in category_labels
    ]

    grid = {day: {} for day, _ in WEEKDAYS}
    for e in enrollments:
        course = e.course
        cell = {
            "name": course.name,
            "classroom": course.classroom,
            "span": course.end_period - course.start_period + 1,
        }
        for period in range(course.start_period, course.end_period + 1):
            grid[course.weekday][period] = cell if period == course.start_period else {"skip": True}

    return render(request, "courses/timetable.html", {
        "enrollments": enrollments,
        "total_credits": total_credits,
        "category_summary": category_summary,
        "grid": grid,
        "weekdays": [day for day, _ in WEEKDAYS],
        "weekday_labels": WEEKDAYS,
        "periods": PERIODS,
    })


@login_required
def course_list(request):
    if not _require_role(request.user, "student"):
        return redirect("home")
    courses = Course.objects.select_related("teacher").all()
    enrolled_ids = set(
        Enrollment.objects.filter(student=request.user).values_list("course_id", flat=True)
    )
    return render(request, "courses/course_list.html", {
        "courses": courses,
        "enrolled_ids": enrolled_ids,
    })


@login_required
def course_detail(request, course_id):
    """課程大綱頁：所有已登入身分皆可查看。"""
    course = get_object_or_404(Course.objects.select_related("teacher"), pk=course_id)
    enrolled = False
    if _require_role(request.user, "student"):
        enrolled = Enrollment.objects.filter(student=request.user, course=course).exists()
    return render(request, "courses/course_detail.html", {
        "course": course,
        "enrolled": enrolled,
        "enrolled_count": course.enrollments.count(),
    })


@login_required
@require_POST
def enroll(request, course_id):
    if not _require_role(request.user, "student"):
        return redirect("home")
    course = get_object_or_404(Course, pk=course_id)
    student = request.user

    with transaction.atomic():
        current = list(_student_enrollments(student).select_for_update())

        if any(e.course_id == course.id for e in current):
            return _respond(request, False, "你已選過這門課。", course.id)

        if course.is_full:
            return _respond(request, False, f"{course.name} 修課人數已滿。", course.id)

        for e in current:
            if course.conflicts_with(e.course):
                return _respond(
                    request, False,
                    f"與「{e.course.name}」衝堂（{course.get_weekday_display()} "
                    f"第 {e.course.start_period}–{e.course.end_period} 節）。",
                    course.id,
                )

        profile = getattr(student, "student_profile", None)
        max_credits = profile.max_credits if profile else 25
        total = sum(e.course.credits for e in current) + course.credits
        if total > max_credits:
            return _respond(
                request, False,
                f"加選後學分為 {total}，超過上限 {max_credits} 學分。",
                course.id,
            )

        Enrollment.objects.create(student=student, course=course)
        _log(request, ActivityLog.Action.ENROLL, f"{course.code} {course.name}")

    return _respond(request, True, f"已成功加選「{course.name}」。", course.id)


@login_required
@require_POST
def drop(request, course_id):
    if not _require_role(request.user, "student"):
        return redirect("home")
    course = Course.objects.filter(pk=course_id).first()
    deleted, _ = Enrollment.objects.filter(
        student=request.user, course_id=course_id
    ).delete()
    if deleted:
        _log(request, ActivityLog.Action.DROP,
             f"{course.code} {course.name}" if course else str(course_id))
        return _respond(request, True, "已退選。", course_id)
    return _respond(request, False, "你尚未選此課程。", course_id)


# ---------- 教師 ----------

@login_required
def teacher_courses(request):
    if not _is_teacher(request.user):
        return redirect("home")
    courses = (
        Course.objects.filter(teacher=request.user)
        .annotate(student_count=Count("enrollments"))
        .order_by("code")
    )
    return render(request, "courses/teacher_courses.html", {"courses": courses})


@login_required
def teacher_course_detail(request, course_id):
    if not _is_teacher(request.user):
        return redirect("home")
    course = get_object_or_404(Course, pk=course_id, teacher=request.user)
    enrollments = (
        Enrollment.objects.filter(course=course)
        .select_related("student__student_profile")
        .order_by("student__username")
    )
    return render(request, "courses/teacher_course_detail.html", {
        "course": course,
        "enrollments": enrollments,
    })


# ---------- 開課、編輯、刪除（教師與管理員共用） ----------

@login_required
def course_create(request):
    """教師只能開自己的課；管理員可指定任一位教師。"""
    if not (_is_teacher(request.user) or _is_admin(request.user)):
        return redirect("home")

    if request.method == "POST":
        form = CourseForm(request.POST, user=request.user)
        if form.is_valid():
            course = form.save(commit=False)
            if _is_teacher(request.user):
                course.teacher = request.user
            course.save()
            _log(request, ActivityLog.Action.COURSE_CREATE, f"{course.code} {course.name}")
            messages.success(request, f"已建立課程「{course.name}」。")
            if _is_admin(request.user):
                return redirect("admin_dashboard")
            return redirect("teacher_courses")
    else:
        form = CourseForm(user=request.user)

    return render(request, "courses/course_form.html", {"form": form})


@login_required
def course_edit(request, course_id):
    """教師只能編輯自己的課；管理員可編輯任一門課。"""
    if not (_is_admin(request.user) or _is_teacher(request.user)):
        return redirect("home")
    course = get_object_or_404(Course, pk=course_id)
    if not _can_manage_course(request.user, course):
        return redirect("home")

    if request.method == "POST":
        form = CourseForm(request.POST, instance=course, user=request.user)
        if form.is_valid():
            form.save()
            _log(request, ActivityLog.Action.COURSE_UPDATE, f"{course.code} {course.name}")
            messages.success(request, f"已更新課程「{course.name}」。")
            if _is_admin(request.user):
                return redirect("admin_dashboard")
            return redirect("teacher_courses")
    else:
        form = CourseForm(instance=course, user=request.user)

    return render(request, "courses/course_form.html", {
        "form": form,
        "course": course,
    })


@login_required
@require_POST
def course_delete(request, course_id):
    """刪除課程（連同選課紀錄一併刪除），並寫入操作紀錄。"""
    if not (_is_admin(request.user) or _is_teacher(request.user)):
        return redirect("home")
    course = get_object_or_404(Course, pk=course_id)
    if not _can_manage_course(request.user, course):
        return redirect("home")

    name = f"{course.code} {course.name}"
    enrolled = course.enrollments.count()
    with transaction.atomic():
        course.delete()
        _log(request, ActivityLog.Action.COURSE_DELETE, name,
             f"連同 {enrolled} 筆選課紀錄一併刪除")

    messages.success(request, f"已刪除課程「{name}」。")
    if _is_admin(request.user):
        return redirect("admin_dashboard")
    return redirect("teacher_courses")


# ---------- 管理員 ----------

@login_required
def admin_dashboard(request):
    if not _is_admin(request.user):
        return redirect("home")
    courses = Course.objects.select_related("teacher").all()
    enrollments = (
        Enrollment.objects.select_related("student", "course")
        .order_by("-enrolled_at")[:50]
    )
    students = StudentProfile.objects.select_related("user").order_by("student_no")
    logs = ActivityLog.objects.select_related("actor").order_by("-created_at")[:30]
    return render(request, "courses/admin_dashboard.html", {
        "courses": courses,
        "enrollments": enrollments,
        "students": students,
        "logs": logs,
    })


@login_required
@require_POST
def admin_force_drop(request, enrollment_id):
    if not _is_admin(request.user):
        return redirect("home")
    enrollment = get_object_or_404(Enrollment, pk=enrollment_id)
    name = f"{enrollment.student.username} 的「{enrollment.course.name}」"
    _log(request, ActivityLog.Action.FORCE_DROP, name)
    enrollment.delete()
    messages.success(request, f"已強制退選：{name}")
    return redirect("admin_dashboard")


@login_required
@require_POST
def admin_set_max_credits(request, student_id):
    if not _is_admin(request.user):
        return redirect("home")
    profile = get_object_or_404(StudentProfile, pk=student_id)
    try:
        value = int(request.POST.get("max_credits", ""))
        if not 1 <= value <= 40:
            raise ValueError
    except ValueError:
        messages.error(request, "學分上限須為 1–40 的整數。")
        return redirect("admin_dashboard")
    old = profile.max_credits
    profile.max_credits = value
    profile.save(update_fields=["max_credits"])
    _log(request, ActivityLog.Action.CREDIT_UPDATE,
         profile.user.username, f"{old} → {value}")
    messages.success(request, f"已將 {profile.user.username} 的學分上限設為 {value}。")
    return redirect("admin_dashboard")


from django.contrib.auth import login as auth_login
from django.contrib.auth.forms import AuthenticationForm


def login_view(request):
    """登入頁：成功登入後寫入操作紀錄。"""
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            auth_login(request, form.get_user())
            ActivityLog.objects.create(
                actor=request.user,
                action=ActivityLog.Action.LOGIN,
                target=request.user.username,
                detail="登入成功",
                ip_address=_client_ip(request),
            )
            return redirect(request.POST.get("next") or "home")
    else:
        form = AuthenticationForm(request)
    return render(request, "courses/login.html", {"form": form, "next": request.GET.get("next", "")})