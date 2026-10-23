# courses/admin_views.py
"""管理員（教務處）專用頁面：總覽、學生、教師、課程、教室、選課紀錄、操作紀錄、批次匯入與系統設定。"""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, F, IntegerField, Q, Sum
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services
from .exports import csv_response
from .forms import (
    AdminSetPasswordForm, ClassroomForm, ImportForm, LogFilterForm, StudentCreateForm,
    StudentEditForm, SystemSettingForm, TeacherCreateForm, TeacherEditForm,
)
from .imports import IMPORT_KINDS, ImportFileError, run_import, template_rows
from .models import (
    ActivityLog, Classroom, Course, Enrollment, StudentProfile, SystemSetting,
    TeacherProfile, User, WaitlistEntry,
)
from .utils import is_admin, log_action, paginate, with_course_counts


# 學生學分分布圖的分組：(標籤, 最少, 最多)
CREDIT_BUCKETS = [
    ("0", 0, 0),
    ("1–15", 1, 15),
    ("16–20", 16, 20),
    ("21–25", 21, 25),
    ("26 以上", 26, None),
]


def admin_required(view):
    """只允許管理員；其他身分導回首頁。"""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not is_admin(request.user):
            return redirect("home")
        return view(request, *args, **kwargs)
    return login_required(wrapper)


def _render(request, template, tab, context=None):
    """管理頁面共用：帶入目前的分頁籤，讓上方導覽標示所在位置。"""
    return render(request, template, {**(context or {}), "tab": tab})


def _redirect_back(request, fallback):
    """回到送出表單的清單頁（保留搜尋與頁碼）；網址不安全時改回預設頁。"""
    next_url = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect(fallback)


def _list_url_for(user):
    """依帳號身分回到學生或教師清單。"""
    if user.is_student:
        return "admin_students"
    if user.is_teacher:
        return "admin_teachers"
    return "admin_dashboard"


def _with_credit_totals(queryset):
    """學生資料加上目前已選學分 total_credits。"""
    return queryset.annotate(
        total_credits=Coalesce(
            Sum("user__enrollments__course__credits"), 0, output_field=IntegerField()
        )
    )


def _bar_rows(rows):
    """幫長條圖的每一列算出相對最大值的百分比（決定長條長度）。"""
    peak = max((row["count"] for row in rows), default=0) or 1
    for row in rows:
        row["pct"] = round(row["count"] * 100 / peak)
    return rows


# ---------- 總覽 ----------

@admin_required
def admin_dashboard(request):
    setting = SystemSetting.load()

    courses = list(with_course_counts(Course.objects.select_related("teacher")))
    for course in courses:
        course.fill_ratio = course.num_enrolled / course.capacity if course.capacity else 0
        course.fill_pct = min(round(course.fill_ratio * 100), 100)
    courses.sort(key=lambda c: (c.fill_ratio, c.num_enrolled), reverse=True)
    # 選課率前 10 名分成左右兩欄，由上往下讀完左欄再讀右欄
    top = courses[:10]
    half = (len(top) + 1) // 2
    fill_columns = [top[:half], top[half:]]

    by_category = dict(
        Enrollment.objects.values_list("course__category").annotate(n=Count("id")).order_by()
    )
    category_rows = _bar_rows([
        {"label": label, "count": by_category.get(value, 0)}
        for value, label in Course.Category.choices
    ])

    students = list(_with_credit_totals(
        StudentProfile.objects.select_related("user").filter(user__is_active=True)
    ))
    bucket_rows = _bar_rows([
        {
            "label": label,
            "count": sum(
                1 for s in students
                if s.total_credits >= low and (high is None or s.total_credits <= high)
            ),
        }
        for label, low, high in CREDIT_BUCKETS
    ])
    below_min = [s for s in students if s.total_credits < s.min_credits]
    busiest = sorted((c for c in courses if c.num_waiting), key=lambda c: c.num_waiting, reverse=True)

    kpis = [
        {"label": "學生", "value": len(students), "url": reverse("admin_students")},
        {
            "label": "教師",
            "value": User.objects.filter(role=User.Role.TEACHER, is_active=True).count(),
            "url": reverse("admin_teachers"),
        },
        {"label": "開課數", "value": len(courses), "url": reverse("admin_courses")},
        {"label": "選課人次", "value": Enrollment.objects.count(), "url": reverse("admin_enrollments")},
        {"label": "候補人次", "value": WaitlistEntry.objects.count(), "url": ""},
        {
            "label": "未達學分下限",
            "value": len(below_min),
            "url": reverse("admin_students") + "?below_min=1",
        },
    ]
    return _render(request, "courses/manage/overview.html", "overview", {
        "setting": setting,
        "status": setting.enrollment_status(),
        "kpis": kpis,
        "courses": courses,
        "top_courses": top,
        "fill_columns": fill_columns,
        "category_rows": category_rows,
        "bucket_rows": bucket_rows,
        "below_min": below_min[:8],
        "below_min_count": len(below_min),
        "busiest": busiest[:5],
    })


# ---------- 學生 ----------

@admin_required
def admin_students(request):
    q = request.GET.get("q", "").strip()
    below_min = request.GET.get("below_min") == "1"
    students = _with_credit_totals(StudentProfile.objects.select_related("user")).order_by("student_no")
    if q:
        students = students.filter(
            Q(student_no__icontains=q) | Q(user__username__icontains=q)
            | Q(user__display_name__icontains=q) | Q(department__icontains=q)
        )
    if below_min:
        students = students.filter(total_credits__lt=F("min_credits"), user__is_active=True)
    return _render(request, "courses/manage/students.html", "students", {
        "page": paginate(request, students),
        "q": q,
        "below_min": below_min,
    })


@admin_required
def admin_student_create(request):
    """管理員新增學生：同時建立帳號與學生資料。"""
    if request.method == "POST":
        form = StudentCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            data = form.cleaned_data
            log_action(request, ActivityLog.Action.STUDENT_CREATE,
                       f"{user.username} ({data['student_no']})",
                       f"{data['display_name']}／{data['department']}")
            messages.success(request, f"已新增學生「{data['display_name']}」。")
            return redirect("admin_students")
    else:
        form = StudentCreateForm()
    return _render(request, "courses/student_form.html", "students", {"form": form})


@admin_required
def admin_student_edit(request, student_id):
    """管理員編輯學生基本資料。"""
    profile = get_object_or_404(StudentProfile.objects.select_related("user"), pk=student_id)
    if request.method == "POST":
        form = StudentEditForm(request.POST, instance=profile)
        if form.is_valid():
            with transaction.atomic():
                profile = form.save()
                profile.user.display_name = form.cleaned_data["display_name"]
                profile.user.save(update_fields=["display_name"])
                log_action(request, ActivityLog.Action.STUDENT_UPDATE,
                           f"{profile.user.username} ({profile.student_no})")
            messages.success(request, "已更新學生資料。")
            return redirect("admin_students")
    else:
        form = StudentEditForm(instance=profile)
    return _render(request, "courses/student_form.html", "students", {
        "form": form,
        "profile": profile,
    })


@admin_required
@require_POST
def admin_set_credits(request, student_id):
    """在學生清單直接調整學分上下限。"""
    profile = get_object_or_404(StudentProfile.objects.select_related("user"), pk=student_id)
    try:
        low = int(request.POST.get("min_credits", ""))
        high = int(request.POST.get("max_credits", ""))
    except ValueError:
        messages.error(request, "學分上下限須為整數。")
        return _redirect_back(request, "admin_students")
    if not (0 <= low <= 40 and 1 <= high <= 40):
        messages.error(request, "學分下限須為 0–40、上限須為 1–40。")
    elif low > high:
        messages.error(request, "學分下限不可高於學分上限。")
    else:
        old = f"{profile.min_credits}–{profile.max_credits}"
        profile.min_credits, profile.max_credits = low, high
        profile.save(update_fields=["min_credits", "max_credits"])
        log_action(request, ActivityLog.Action.CREDIT_UPDATE,
                   profile.user.username, f"{old} → {low}–{high}")
        messages.success(request, f"已將 {profile.user.username} 的學分範圍設為 {low}–{high}。")
    return _redirect_back(request, "admin_students")


# ---------- 教師 ----------

@admin_required
def admin_teachers(request):
    q = request.GET.get("q", "").strip()
    teachers = (
        TeacherProfile.objects.select_related("user")
        .annotate(num_courses=Count("user__taught_courses"))
        .order_by("teacher_no")
    )
    if q:
        teachers = teachers.filter(
            Q(teacher_no__icontains=q) | Q(user__username__icontains=q)
            | Q(user__display_name__icontains=q) | Q(department__icontains=q)
        )
    return _render(request, "courses/manage/teachers.html", "teachers", {
        "page": paginate(request, teachers),
        "q": q,
    })


@admin_required
def admin_teacher_create(request):
    """管理員新增教師：同時建立帳號與教師資料。"""
    if request.method == "POST":
        form = TeacherCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            data = form.cleaned_data
            log_action(request, ActivityLog.Action.TEACHER_CREATE,
                       f"{user.username} ({data['teacher_no']})",
                       f"{data['display_name']}／{data['department']}")
            messages.success(request, f"已新增教師「{data['display_name']}」。")
            return redirect("admin_teachers")
    else:
        form = TeacherCreateForm()
    return _render(request, "courses/teacher_form.html", "teachers", {"form": form})


@admin_required
def admin_teacher_edit(request, teacher_id):
    """管理員編輯教師基本資料。"""
    profile = get_object_or_404(TeacherProfile.objects.select_related("user"), pk=teacher_id)
    if request.method == "POST":
        form = TeacherEditForm(request.POST, instance=profile)
        if form.is_valid():
            with transaction.atomic():
                profile = form.save()
                profile.user.display_name = form.cleaned_data["display_name"]
                profile.user.save(update_fields=["display_name"])
                log_action(request, ActivityLog.Action.TEACHER_UPDATE,
                           f"{profile.user.username} ({profile.teacher_no})")
            messages.success(request, "已更新教師資料。")
            return redirect("admin_teachers")
    else:
        form = TeacherEditForm(instance=profile)
    return _render(request, "courses/teacher_form.html", "teachers", {
        "form": form,
        "profile": profile,
    })


# ---------- 帳號 ----------

@admin_required
@require_POST
def admin_toggle_account(request, user_id):
    """停用或啟用帳號（軟刪除，保留所有歷史紀錄）。"""
    target = get_object_or_404(User, pk=user_id)
    if target.pk == request.user.pk:
        messages.error(request, "不能停用自己的帳號。")
    elif target.is_superuser:
        messages.error(request, "不能停用超級使用者。")
    else:
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        action = ActivityLog.Action.ACCOUNT_ENABLE if target.is_active else ActivityLog.Action.ACCOUNT_DISABLE
        log_action(request, action, target.username, f"身分：{target.get_role_display()}")
        word = "啟用" if target.is_active else "停用"
        messages.success(request, f"已{word}帳號 {target.username}。")
    return _redirect_back(request, _list_url_for(target))


@admin_required
def admin_reset_password(request, user_id):
    """管理員替忘記密碼的學生或教師設定新密碼。"""
    target = get_object_or_404(User, pk=user_id)
    if target.is_superuser or target.pk == request.user.pk:
        messages.error(request, "這個帳號請由本人從「修改密碼」變更。")
        return redirect(_list_url_for(target))
    if request.method == "POST":
        form = AdminSetPasswordForm(target, request.POST)
        if form.is_valid():
            form.save()
            log_action(request, ActivityLog.Action.PASSWORD_RESET, target.username,
                       f"身分：{target.get_role_display()}")
            messages.success(request, f"已重設 {target.username} 的密碼，請將新密碼告知本人。")
            return redirect(_list_url_for(target))
    else:
        form = AdminSetPasswordForm(target)
    tab = "students" if target.is_student else "teachers"
    return _render(request, "courses/manage/reset_password.html", tab, {
        "form": form,
        "target": target,
        "back_url": _list_url_for(target),
    })


# ---------- 課程 ----------

@admin_required
def admin_courses(request):
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "")
    courses = with_course_counts(
        Course.objects.select_related("teacher", "classroom")
    ).order_by("code")
    if q:
        courses = courses.filter(
            Q(code__icontains=q) | Q(name__icontains=q) | Q(teacher__username__icontains=q)
            | Q(teacher__display_name__icontains=q) | Q(classroom__name__icontains=q)
        )
    if category in Course.Category.values:
        courses = courses.filter(category=category)
    return _render(request, "courses/manage/courses.html", "courses", {
        "page": paginate(request, courses),
        "q": q,
        "category": category,
        "categories": Course.Category.choices,
    })


# ---------- 教室 ----------

@admin_required
def admin_classrooms(request):
    q = request.GET.get("q", "").strip()
    rooms = Classroom.objects.annotate(num_courses=Count("courses")).order_by("name")
    if q:
        rooms = rooms.filter(name__icontains=q)
    return _render(request, "courses/manage/classrooms.html", "classrooms", {
        "page": paginate(request, rooms),
        "q": q,
    })


@admin_required
def admin_classroom_create(request):
    form = ClassroomForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        room = form.save()
        log_action(request, ActivityLog.Action.CLASSROOM_CREATE, room.name, f"容納 {room.capacity} 人")
        messages.success(request, f"已新增教室 {room.name}。")
        return redirect("admin_classrooms")
    return _render(request, "courses/manage/classroom_form.html", "classrooms", {"form": form})


@admin_required
def admin_classroom_edit(request, room_id):
    room = get_object_or_404(Classroom, pk=room_id)
    form = ClassroomForm(request.POST or None, instance=room)
    if request.method == "POST" and form.is_valid():
        room = form.save()
        log_action(request, ActivityLog.Action.CLASSROOM_UPDATE, room.name, f"容納 {room.capacity} 人")
        messages.success(request, f"已更新教室 {room.name}。")
        return redirect("admin_classrooms")
    return _render(request, "courses/manage/classroom_form.html", "classrooms", {
        "form": form,
        "room": room,
    })


@admin_required
@require_POST
def admin_classroom_delete(request, room_id):
    room = get_object_or_404(Classroom, pk=room_id)
    used = room.courses.count()
    if used:
        messages.error(request, f"還有 {used} 門課排在 {room.name}，請先調整課程的教室再刪除。")
    else:
        name = room.name
        room.delete()
        log_action(request, ActivityLog.Action.CLASSROOM_DELETE, name)
        messages.success(request, f"已刪除教室 {name}。")
    return redirect("admin_classrooms")


# ---------- 選課紀錄 ----------

@admin_required
def admin_enrollments(request):
    q = request.GET.get("q", "").strip()
    enrollments = (
        Enrollment.objects.select_related("student__student_profile", "course")
        .order_by("-enrolled_at")
    )
    if q:
        enrollments = enrollments.filter(
            Q(student__username__icontains=q) | Q(student__display_name__icontains=q)
            | Q(student__student_profile__student_no__icontains=q)
            | Q(course__code__icontains=q) | Q(course__name__icontains=q)
        )
    return _render(request, "courses/manage/enrollments.html", "enrollments", {
        "page": paginate(request, enrollments),
        "q": q,
    })


@admin_required
@require_POST
def admin_force_drop(request, enrollment_id):
    """強制退選（不受學分下限限制），空出的名額依序遞補候補學生。"""
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("student", "course"), pk=enrollment_id
    )
    course = enrollment.course
    name = f"{enrollment.student.username} 的「{course.name}」"
    with transaction.atomic():
        enrollment.delete()
        log_action(request, ActivityLog.Action.FORCE_DROP, name)
        promoted = services.promote_waitlist(course)
    message = f"已強制退選：{name}。"
    if promoted:
        message += f"已由候補的 {promoted[0].username} 遞補。"
    messages.success(request, message)
    return _redirect_back(request, "admin_enrollments")


# ---------- 操作紀錄 ----------

@admin_required
def admin_logs(request):
    form = LogFilterForm(request.GET or None)
    logs = ActivityLog.objects.select_related("actor").order_by("-created_at")
    if form.is_valid():
        data = form.cleaned_data
        if data["q"]:
            logs = logs.filter(
                Q(actor__username__icontains=data["q"]) | Q(target__icontains=data["q"])
                | Q(detail__icontains=data["q"])
            )
        if data["action"]:
            logs = logs.filter(action=data["action"])
        if data["date_from"]:
            logs = logs.filter(created_at__date__gte=data["date_from"])
        if data["date_to"]:
            logs = logs.filter(created_at__date__lte=data["date_to"])
    return _render(request, "courses/manage/logs.html", "logs", {
        "form": form,
        "page": paginate(request, logs, per_page=50),
    })


# ---------- 批次匯入 ----------

@admin_required
def admin_import(request):
    forms_by_kind = {kind: ImportForm(prefix=kind) for kind in IMPORT_KINDS}
    result = None
    kind = request.POST.get("kind")
    if request.method == "POST" and kind in IMPORT_KINDS:
        form = ImportForm(request.POST, request.FILES, prefix=kind)
        forms_by_kind[kind] = form
        if form.is_valid():
            label = IMPORT_KINDS[kind]["label"]
            try:
                created, errors = run_import(kind, form.cleaned_data["file"], request.user)
            except ImportFileError as error:
                form.add_error("file", str(error))
            else:
                if errors:
                    result = {"label": label, "errors": errors}
                else:
                    log_action(request, ActivityLog.Action.BULK_IMPORT, f"{label} {created} 筆")
                    messages.success(request, f"已匯入 {created} 筆{label}資料。")
                    return redirect({
                        "students": "admin_students",
                        "teachers": "admin_teachers",
                        "courses": "admin_courses",
                    }[kind])
    sections = [
        {"kind": key, "spec": spec, "form": forms_by_kind[key]}
        for key, spec in IMPORT_KINDS.items()
    ]
    return _render(request, "courses/manage/import.html", "import", {
        "sections": sections,
        "result": result,
    })


@admin_required
def admin_import_template(request, kind):
    """下載匯入範例檔（含標題列與範例資料）。"""
    if kind not in IMPORT_KINDS:
        raise Http404
    header, rows = template_rows(kind)
    return csv_response(f"{IMPORT_KINDS[kind]['label']}匯入範例.csv", header, rows)


# ---------- 系統設定 ----------

def _period_text(value):
    return timezone.localtime(value).strftime("%Y-%m-%d %H:%M") if value else "不限"


@admin_required
def admin_settings(request):
    setting = SystemSetting.load()
    if request.method == "POST":
        quick = request.POST.get("quick")
        if quick in ("open", "close"):
            # 快速操作：立即開放（清除時間限制）或立即結束加退選
            now = timezone.now()
            if quick == "open":
                setting.enrollment_start = setting.enrollment_end = None
            else:
                setting.enrollment_end = now
                if setting.enrollment_start and setting.enrollment_start > now:
                    setting.enrollment_start = None
            setting.save()
            form = None
        else:
            form = SystemSettingForm(request.POST, instance=setting)
            if form.is_valid():
                setting = form.save()
        if form is None or form.is_valid():
            log_action(
                request, ActivityLog.Action.SETTINGS_UPDATE, "加退選期間",
                f"{_period_text(setting.enrollment_start)} ～ {_period_text(setting.enrollment_end)}",
            )
            messages.success(request, "已更新系統設定。")
            return redirect("admin_settings")
    else:
        form = SystemSettingForm(instance=setting)
    semester_start, semester_end = setting.semester_dates()
    return _render(request, "courses/manage/settings.html", "settings", {
        "form": form,
        "setting": setting,
        "status": setting.enrollment_status(),
        "semester_start": semester_start,
        "semester_end": semester_end,
    })
