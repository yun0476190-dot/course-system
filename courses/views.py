# courses/views.py
"""學生、教師與共用頁面。管理員（教務處）專用頁面在 admin_views.py。"""
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services
from .exports import csv_response, ics_response, timetable_ics
from .forms import CourseForm, StyledPasswordChangeForm
from .models import ActivityLog, Course, Enrollment, SystemSetting, User, WaitlistEntry
from .utils import (
    can_manage_course, is_admin, is_student, is_teacher, log_action, with_course_counts,
)


WEEKDAYS = Course.Weekday.choices


# ---------- 共用工具 ----------

def _respond(request, ok, message, course_id):
    """AJAX 請求回傳 JSON；一般表單請求沿用原本的跳轉行為。"""
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": ok, "message": message, "course_id": course_id})
    if ok:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect(f"{reverse('course_list')}#course-{course_id}")


def _course_list_url(user):
    """開課、編輯、刪除課程後回到的頁面。"""
    return "admin_courses" if is_admin(user) else "teacher_courses"


# ---------- 登入與密碼 ----------

def login_view(request):
    """登入頁：成功登入後寫入操作紀錄，且只跳轉回本站網址。"""
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            auth_login(request, form.get_user())
            log_action(request, ActivityLog.Action.LOGIN, request.user.username, "登入成功")
            next_url = request.POST.get("next", "")
            if not url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                next_url = reverse("home")
            return redirect(next_url)
    else:
        form = AuthenticationForm(request)
    return render(request, "courses/login.html", {
        "form": form,
        "next": request.GET.get("next", ""),
    })


class UserPasswordChangeView(auth_views.PasswordChangeView):
    """所有身分都能自行修改密碼；完成後回首頁並寫入操作紀錄。"""

    form_class = StyledPasswordChangeForm
    template_name = "courses/password_change.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        response = super().form_valid(form)
        log_action(self.request, ActivityLog.Action.PASSWORD_CHANGE, self.request.user.username)
        messages.success(self.request, "密碼已更新。")
        return response


# ---------- 學生 ----------

@login_required
def my_timetable(request):
    if not is_student(request.user):
        return redirect("home")
    student = request.user
    enrollments = list(
        Enrollment.objects.filter(student=student)
        .select_related("course__teacher", "course__classroom")
        .order_by("course__weekday", "course__start_period")
    )
    courses = [e.course for e in enrollments]

    category_labels = dict(Course.Category.choices)
    category_credits = {key: 0 for key in category_labels}
    for course in courses:
        category_credits[course.category] += course.credits
    category_summary = [
        {"label": category_labels[key], "credits": category_credits[key]}
        for key in category_labels
    ]

    # 起始節次放課程資料（模板用 rowspan 跨列），其餘節次標成 skip 不輸出格子
    grid = {day: {} for day, _ in WEEKDAYS}
    overlaps = []
    for course in courses:
        periods = range(course.start_period, course.end_period + 1)
        day_slots = grid[course.weekday]
        # 課程被改時間後可能和其他已選課程重疊；重疊的課不放進表格，以免表格錯位
        if any(period in day_slots for period in periods):
            overlaps.append(course)
            continue
        day_slots[course.start_period] = {
            "name": course.name,
            "classroom": course.classroom.name,
            "span": len(periods),
        }
        for period in periods[1:]:
            day_slots[period] = {"skip": True}

    waitlist = [
        {"entry": entry, "position": entry.position}
        for entry in student.waitlist_entries.select_related("course")
    ]
    periods = [
        {"number": p, "start": services.period_start(p), "end": services.period_end(p)}
        for p in services.PERIODS
    ]
    return render(request, "courses/timetable.html", {
        "enrollments": enrollments,
        "summary": services.credit_summary(student, courses),
        "category_summary": category_summary,
        "grid": grid,
        "overlaps": overlaps,
        "waitlist": waitlist,
        "weekdays": [day for day, _ in WEEKDAYS],
        "weekday_labels": WEEKDAYS,
        "periods": periods,
    })


@login_required
def timetable_calendar(request):
    """下載課表行事曆檔（.ics），可匯入 Google 日曆或手機行事曆。"""
    if not is_student(request.user):
        return redirect("home")
    courses = [
        e.course for e in Enrollment.objects.filter(student=request.user)
        .select_related("course__teacher", "course__classroom")
    ]
    start, end = SystemSetting.load().semester_dates()
    return ics_response(timetable_ics(request.user, courses, start, end))


@login_required
def course_list(request):
    if not is_student(request.user):
        return redirect("home")
    student = request.user
    setting = SystemSetting.load()
    current = services.enrolled_courses(student)
    enrolled_ids = {c.pk for c in current}
    positions = services.waitlist_positions(student)

    courses = list(with_course_counts(Course.objects.select_related("teacher", "classroom")))
    for course in courses:
        # 每門課對這位學生的狀態，決定卡片上顯示的按鈕與提示
        course.remaining = max(course.capacity - course.num_enrolled, 0)
        course.block_reason = ""
        if course.pk in enrolled_ids:
            course.status = "enrolled"
        elif course.pk in positions:
            course.status = "waiting"
            course.position = positions[course.pk]
        else:
            course.block_reason = services.enroll_block_reason(student, course, current) or ""
            if course.block_reason:
                course.status = "blocked"
            elif course.remaining == 0:
                course.status = "full"
            else:
                course.status = "open"

    return render(request, "courses/course_list.html", {
        "courses": courses,
        "summary": services.credit_summary(student, current),
        "window_open": setting.enrollment_open,
        "closed_message": setting.closed_message(),
        "setting": setting,
        "categories": Course.Category.choices,
    })


@login_required
def course_detail(request, course_id):
    """課程大綱頁：所有已登入身分皆可查看。"""
    course = get_object_or_404(
        with_course_counts(Course.objects.select_related("teacher", "classroom")), pk=course_id
    )
    enrolled = False
    if is_student(request.user):
        enrolled = Enrollment.objects.filter(student=request.user, course=course).exists()
    return render(request, "courses/course_detail.html", {
        "course": course,
        "enrolled": enrolled,
    })


def _enrollment_closed(request, course_id):
    """加退選期間外，回傳給學生的錯誤回應；期間內回傳 None。"""
    setting = SystemSetting.load()
    if setting.enrollment_open:
        return None
    return _respond(request, False, setting.closed_message(), course_id)


@login_required
@require_POST
def enroll(request, course_id):
    if not is_student(request.user):
        return redirect("home")
    closed = _enrollment_closed(request, course_id)
    if closed:
        return closed
    student = request.user

    with transaction.atomic():
        # 鎖住學生與課程，避免同時加選造成超收或衝堂
        # （SQLite 不支援列鎖，改由 settings 的 IMMEDIATE 交易模式讓寫入排隊）
        User.objects.select_for_update().get(pk=student.pk)
        course = get_object_or_404(Course.objects.select_for_update(), pk=course_id)
        reason = services.enroll_block_reason(student, course, services.enrolled_courses(student))
        if reason:
            return _respond(request, False, reason, course.id)
        if course.is_full:
            return _respond(request, False, f"「{course.name}」修課人數已滿，可以登記候補。", course.id)
        Enrollment.objects.create(student=student, course=course)
        WaitlistEntry.objects.filter(student=student, course=course).delete()
        log_action(request, ActivityLog.Action.ENROLL, f"{course.code} {course.name}")

    return _respond(request, True, f"已成功加選「{course.name}」。", course.id)


@login_required
@require_POST
def drop(request, course_id):
    if not is_student(request.user):
        return redirect("home")
    closed = _enrollment_closed(request, course_id)
    if closed:
        return closed
    student = request.user

    with transaction.atomic():
        User.objects.select_for_update().get(pk=student.pk)
        course = Course.objects.select_for_update().filter(pk=course_id).first()
        enrollment = (
            Enrollment.objects.filter(student=student, course=course).first() if course else None
        )
        if enrollment is None:
            return _respond(request, False, "你尚未選此課程。", course_id)
        reason = services.drop_block_reason(student, course, services.enrolled_courses(student))
        if reason:
            return _respond(request, False, reason, course_id)
        enrollment.delete()
        log_action(request, ActivityLog.Action.DROP, f"{course.code} {course.name}")
        services.promote_waitlist(course)

    return _respond(request, True, "已退選。", course_id)


@login_required
@require_POST
def waitlist_join(request, course_id):
    """登記候補：只有額滿、而且自己符合加選條件的課程可以登記。"""
    if not is_student(request.user):
        return redirect("home")
    closed = _enrollment_closed(request, course_id)
    if closed:
        return closed
    student = request.user

    with transaction.atomic():
        User.objects.select_for_update().get(pk=student.pk)
        course = get_object_or_404(Course.objects.select_for_update(), pk=course_id)
        if WaitlistEntry.objects.filter(student=student, course=course).exists():
            return _respond(request, False, "你已在這門課的候補名單中。", course.id)
        reason = services.enroll_block_reason(student, course, services.enrolled_courses(student))
        if reason:
            return _respond(request, False, reason, course.id)
        if not course.is_full:
            return _respond(request, False, "這門課還有名額，請直接加選。", course.id)
        entry = WaitlistEntry.objects.create(student=student, course=course)
        position = entry.position
        log_action(
            request, ActivityLog.Action.WAITLIST_JOIN,
            f"{course.code} {course.name}", f"候補第 {position} 位",
        )

    return _respond(
        request, True, f"已登記候補「{course.name}」，目前第 {position} 位。", course.id
    )


@login_required
@require_POST
def waitlist_leave(request, course_id):
    if not is_student(request.user):
        return redirect("home")
    closed = _enrollment_closed(request, course_id)
    if closed:
        return closed
    entry = (
        WaitlistEntry.objects.filter(student=request.user, course_id=course_id)
        .select_related("course").first()
    )
    if entry is None:
        return _respond(request, False, "你沒有登記這門課的候補。", course_id)
    course = entry.course
    entry.delete()
    log_action(request, ActivityLog.Action.WAITLIST_LEAVE, f"{course.code} {course.name}")
    return _respond(request, True, f"已取消「{course.name}」的候補。", course_id)


# ---------- 教師 ----------

@login_required
def teacher_courses(request):
    if not is_teacher(request.user):
        return redirect("home")
    courses = with_course_counts(
        Course.objects.filter(teacher=request.user).select_related("classroom")
    ).order_by("code")
    return render(request, "courses/teacher_courses.html", {"courses": courses})


@login_required
def teacher_course_detail(request, course_id):
    if not is_teacher(request.user):
        return redirect("home")
    course = get_object_or_404(
        Course.objects.select_related("classroom"), pk=course_id, teacher=request.user
    )
    enrollments = (
        Enrollment.objects.filter(course=course)
        .select_related("student__student_profile")
        .order_by("student__username")
    )
    waitlist = course.waitlist_entries.select_related("student__student_profile")
    return render(request, "courses/teacher_course_detail.html", {
        "course": course,
        "enrollments": enrollments,
        "waitlist": waitlist,
    })


@login_required
def course_roster_csv(request, course_id):
    """下載選課名單（Excel 可開的 CSV）；教師限自己的課，管理員可下載任一門課。"""
    course = get_object_or_404(Course, pk=course_id)
    if not can_manage_course(request.user, course):
        return redirect("home")
    enrollments = (
        Enrollment.objects.filter(course=course)
        .select_related("student__student_profile")
        .order_by("student__student_profile__student_no", "student__username")
    )
    rows = []
    for number, enrollment in enumerate(enrollments, start=1):
        student = enrollment.student
        profile = getattr(student, "student_profile", None)
        rows.append([
            number,
            profile.student_no if profile else "",
            student.display_name or student.username,
            student.username,
            profile.department if profile else "",
            profile.get_grade_display() if profile else "",
            timezone.localtime(enrollment.enrolled_at).strftime("%Y-%m-%d %H:%M"),
        ])
    return csv_response(
        f"{course.code}_選課名單.csv",
        ["序號", "學號", "姓名", "帳號", "系所", "年級", "選課時間"],
        rows,
    )


# ---------- 開課、編輯、刪除（教師與管理員共用） ----------

@login_required
def course_create(request):
    """教師只能開自己的課；管理員可指定任一位教師。"""
    if not (is_teacher(request.user) or is_admin(request.user)):
        return redirect("home")

    if request.method == "POST":
        form = CourseForm(request.POST, user=request.user)
        if form.is_valid():
            course = form.save(commit=False)
            if is_teacher(request.user):
                course.teacher = request.user
            course.save()
            log_action(request, ActivityLog.Action.COURSE_CREATE, f"{course.code} {course.name}")
            messages.success(request, f"已建立課程「{course.name}」。")
            return redirect(_course_list_url(request.user))
    else:
        form = CourseForm(user=request.user)

    return render(request, "courses/course_form.html", {"form": form})


@login_required
def course_edit(request, course_id):
    """教師只能編輯自己的課；管理員可編輯任一門課。"""
    if not (is_admin(request.user) or is_teacher(request.user)):
        return redirect("home")
    course = get_object_or_404(with_course_counts(Course.objects.all()), pk=course_id)
    if not can_manage_course(request.user, course):
        return redirect("home")

    if request.method == "POST":
        form = CourseForm(request.POST, instance=course, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                course = form.save()
                # 人數上限調高時，依序遞補候補學生
                promoted = services.promote_waitlist(course)
                log_action(request, ActivityLog.Action.COURSE_UPDATE, f"{course.code} {course.name}")
            message = f"已更新課程「{course.name}」。"
            if promoted:
                message += f"已從候補名單遞補 {len(promoted)} 位學生。"
            messages.success(request, message)
            return redirect(_course_list_url(request.user))
    else:
        form = CourseForm(instance=course, user=request.user)

    return render(request, "courses/course_form.html", {
        "form": form,
        "course": course,
    })


@login_required
@require_POST
def course_delete(request, course_id):
    """刪除課程（連同選課與候補紀錄一併刪除），並寫入操作紀錄。"""
    if not (is_admin(request.user) or is_teacher(request.user)):
        return redirect("home")
    course = get_object_or_404(Course, pk=course_id)
    if not can_manage_course(request.user, course):
        return redirect("home")

    name = f"{course.code} {course.name}"
    enrolled = course.enrollments.count()
    with transaction.atomic():
        course.delete()
        log_action(request, ActivityLog.Action.COURSE_DELETE, name,
                   f"連同 {enrolled} 筆選課紀錄一併刪除")

    messages.success(request, f"已刪除課程「{name}」。")
    return redirect(_course_list_url(request.user))
