# courses/models.py
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


# 學期學分規定：每位學生的預設下限與上限，管理員可以個別調整
DEFAULT_MIN_CREDITS = 16
DEFAULT_MAX_CREDITS = 25

GRADE_CHOICES = [(1, "一年級"), (2, "二年級"), (3, "三年級"), (4, "四年級")]

# 沒設定學期日期時，匯出行事曆從本週一起算的週數
DEFAULT_SEMESTER_WEEKS = 18


def grades_label(grades):
    """把年級清單轉成文字，例如 [3, 4] → 「三、四年級」。"""
    names = dict(GRADE_CHOICES)
    return "、".join(names[g].removesuffix("年級") for g in sorted(grades) if g in names) + "年級"


class CourseUserManager(UserManager):
    """createsuperuser 建立的帳號預設為管理員身分。"""

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", self.model.Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """自訂使用者，以 role 區分學生、教師、管理員。"""

    class Role(models.TextChoices):
        STUDENT = "student", "學生"
        TEACHER = "teacher", "教師"
        ADMIN = "admin", "管理員"

    role = models.CharField(
        "身分", max_length=10, choices=Role.choices, default=Role.STUDENT
    )
    display_name = models.CharField("姓名", max_length=50, blank=True)

    objects = CourseUserManager()

    @property
    def is_student(self):
        return self.role == self.Role.STUDENT

    @property
    def is_teacher(self):
        return self.role == self.Role.TEACHER

    @property
    def is_academic_admin(self):
        return self.role == self.Role.ADMIN or self.is_superuser


class StudentProfile(models.Model):
    """學生擴充資料：學號、系所、年級、學分上下限與聯絡資料。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
        verbose_name="使用者",
    )
    student_no = models.CharField("學號", max_length=20, unique=True)
    department = models.CharField("系所", max_length=50)
    grade = models.PositiveSmallIntegerField("年級", choices=GRADE_CHOICES, default=1)
    min_credits = models.PositiveSmallIntegerField(
        "學期學分下限", default=DEFAULT_MIN_CREDITS,
        validators=[MaxValueValidator(40)],
    )
    max_credits = models.PositiveSmallIntegerField(
        "學期學分上限", default=DEFAULT_MAX_CREDITS,
        validators=[MinValueValidator(1), MaxValueValidator(40)],
    )
    phone = models.CharField("聯絡電話", max_length=20, blank=True)
    email_contact = models.EmailField("聯絡 Email", blank=True)

    class Meta:
        verbose_name = "學生資料"
        verbose_name_plural = "學生資料"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_credits__lte=models.F("max_credits")),
                name="min_credits_lte_max_credits",
                violation_error_message="學分下限不可高於學分上限。",
            ),
        ]

    def __str__(self):
        return f"{self.student_no} {self.user.display_name or self.user.username}"


class TeacherProfile(models.Model):
    """教師擴充資料：教師編號與所屬系所。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teacher_profile",
        verbose_name="使用者",
    )
    teacher_no = models.CharField("教師編號", max_length=20, unique=True)
    department = models.CharField("系所", max_length=50)

    class Meta:
        verbose_name = "教師資料"
        verbose_name_plural = "教師資料"

    def __str__(self):
        return f"{self.teacher_no} {self.user.display_name or self.user.username}"


class Classroom(models.Model):
    """教室：同一時段只能排一門課，課程人數上限不能超過容納人數。"""

    name = models.CharField("教室", max_length=50, unique=True)
    capacity = models.PositiveSmallIntegerField(
        "容納人數", validators=[MinValueValidator(1)]
    )

    class Meta:
        verbose_name = "教室"
        verbose_name_plural = "教室"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Course(models.Model):
    """課程模型。"""

    class Category(models.TextChoices):
        GE_REQUIRED = "ge_required", "通識必修"
        MAJOR_REQUIRED = "major_required", "專業必修"
        MAJOR_ELECTIVE = "major_elective", "專業選修"
        GE_ELECTIVE = "ge_elective", "通識興趣選修"

    class Weekday(models.IntegerChoices):
        MON = 1, "週一"
        TUE = 2, "週二"
        WED = 3, "週三"
        THU = 4, "週四"
        FRI = 5, "週五"

    name = models.CharField("課程名稱", max_length=100)
    code = models.CharField("課程代碼", max_length=20, unique=True)
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="taught_courses",
        limit_choices_to={"role": User.Role.TEACHER},
        verbose_name="授課教師",
    )
    credits = models.PositiveSmallIntegerField(
        "學分數", validators=[MinValueValidator(1), MaxValueValidator(6)]
    )
    category = models.CharField(
        "課程類別", max_length=20, choices=Category.choices
    )
    department = models.CharField("開課系所", max_length=50, blank=True)
    weekday = models.PositiveSmallIntegerField("星期", choices=Weekday.choices)
    start_period = models.PositiveSmallIntegerField(
        "開始節次", validators=[MinValueValidator(1), MaxValueValidator(14)]
    )
    end_period = models.PositiveSmallIntegerField(
        "結束節次", validators=[MinValueValidator(1), MaxValueValidator(14)]
    )
    classroom = models.ForeignKey(
        Classroom,
        on_delete=models.PROTECT,
        related_name="courses",
        verbose_name="教室",
    )
    capacity = models.PositiveSmallIntegerField("修課人數上限", default=60)
    # 修課資格：清單為空代表不限制
    allowed_departments = models.JSONField("限修系所", default=list, blank=True)
    allowed_grades = models.JSONField("限修年級", default=list, blank=True)
    syllabus = models.TextField("課程大綱", blank=True, default="")

    class Meta:
        verbose_name = "課程"
        verbose_name_plural = "課程"
        ordering = ["code"]
        constraints = [
            # 表單與 Django admin 驗證時也會套用這個約束，錯誤訊息只從這裡產生
            models.CheckConstraint(
                condition=models.Q(end_period__gte=models.F("start_period")),
                name="end_period_gte_start_period",
                violation_error_message="結束節次不可早於開始節次。",
            ),
        ]

    def __str__(self):
        return f"[{self.code}] {self.name}"

    @property
    def enrolled_count(self):
        return self.enrollments.count()

    @property
    def is_full(self):
        return self.enrolled_count >= self.capacity

    @property
    def time_label(self):
        return f"{self.get_weekday_display()} 第 {self.start_period}–{self.end_period} 節"

    @property
    def restriction_label(self):
        """修課限制的說明，例如「資訊管理系；三、四年級」；沒有限制時為空字串。"""
        parts = []
        if self.allowed_departments:
            parts.append("、".join(self.allowed_departments))
        if self.allowed_grades:
            parts.append(grades_label(self.allowed_grades))
        return "；".join(parts)

    def conflicts_with(self, other):
        """判斷與另一門課是否衝堂（同星期且節次區間重疊）。"""
        return (
            self.weekday == other.weekday
            and self.start_period <= other.end_period
            and other.start_period <= self.end_period
        )


class Enrollment(models.Model):
    """選課紀錄：學生與課程的關聯。"""

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="enrollments",
        limit_choices_to={"role": User.Role.STUDENT},
        verbose_name="學生",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="課程",
    )
    enrolled_at = models.DateTimeField("選課時間", auto_now_add=True)

    class Meta:
        verbose_name = "選課紀錄"
        verbose_name_plural = "選課紀錄"
        ordering = ["-enrolled_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course"], name="unique_student_course"
            ),
        ]

    def __str__(self):
        return f"{self.student} ↔ {self.course}"


class WaitlistEntry(models.Model):
    """候補登記：課程額滿時排隊，有名額時依登記順序自動遞補。"""

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
        limit_choices_to={"role": User.Role.STUDENT},
        verbose_name="學生",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="waitlist_entries",
        verbose_name="課程",
    )
    created_at = models.DateTimeField("登記時間", auto_now_add=True)

    class Meta:
        verbose_name = "候補登記"
        verbose_name_plural = "候補登記"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "course"], name="unique_waitlist_student_course"
            ),
        ]

    def __str__(self):
        return f"{self.student} 候補 {self.course}"

    @property
    def position(self):
        """在這門課候補名單中的順位（從 1 開始）。"""
        earlier = WaitlistEntry.objects.filter(course_id=self.course_id).filter(
            models.Q(created_at__lt=self.created_at)
            | models.Q(created_at=self.created_at, id__lt=self.id)
        )
        return earlier.count() + 1


class ActivityLog(models.Model):
    """操作紀錄：稽核用，記錄誰在何時做了什麼。"""

    class Action(models.TextChoices):
        LOGIN = "login", "登入"
        ENROLL = "enroll", "加選"
        DROP = "drop", "退選"
        COURSE_CREATE = "course_create", "開課"
        COURSE_UPDATE = "course_update", "編輯課程"
        COURSE_DELETE = "course_delete", "刪除課程"
        FORCE_DROP = "force_drop", "強制退選"
        CREDIT_UPDATE = "credit_update", "調整學分上下限"
        STUDENT_CREATE = "student_create", "新增學生"
        STUDENT_UPDATE = "student_update", "編輯學生資料"
        ACCOUNT_DISABLE = "account_disable", "停用帳號"
        ACCOUNT_ENABLE = "account_enable", "啟用帳號"
        TEACHER_CREATE = "teacher_create", "新增教師"
        TEACHER_UPDATE = "teacher_update", "編輯教師資料"
        PASSWORD_CHANGE = "password_change", "修改密碼"
        PASSWORD_RESET = "password_reset", "重設密碼"
        WAITLIST_JOIN = "waitlist_join", "登記候補"
        WAITLIST_LEAVE = "waitlist_leave", "取消候補"
        WAITLIST_PROMOTE = "waitlist_promote", "候補遞補"
        CLASSROOM_CREATE = "classroom_create", "新增教室"
        CLASSROOM_UPDATE = "classroom_update", "編輯教室"
        CLASSROOM_DELETE = "classroom_delete", "刪除教室"
        SETTINGS_UPDATE = "settings_update", "修改系統設定"
        BULK_IMPORT = "bulk_import", "批次匯入"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activity_logs",
        verbose_name="操作者",
    )
    action = models.CharField("操作類型", max_length=20, choices=Action.choices)
    target = models.CharField("操作對象", max_length=200, blank=True)
    detail = models.CharField("說明", max_length=255, blank=True)
    ip_address = models.GenericIPAddressField("IP 位址", null=True, blank=True)
    created_at = models.DateTimeField("時間", auto_now_add=True)

    class Meta:
        verbose_name = "操作紀錄"
        verbose_name_plural = "操作紀錄"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor} {self.get_action_display()} {self.target}"

    @classmethod
    def record(cls, action, target="", detail="", actor=None, ip_address=None):
        """寫入一筆紀錄，過長的文字自動截斷；actor 為 None 代表系統自動執行。"""
        return cls.objects.create(
            actor=actor,
            action=action,
            target=target[:200],
            detail=detail[:255],
            ip_address=ip_address,
        )


class SystemSetting(models.Model):
    """全站設定（只有一筆）：加退選期間與學期起訖日。"""

    enrollment_start = models.DateTimeField("加退選開始時間", null=True, blank=True)
    enrollment_end = models.DateTimeField("加退選結束時間", null=True, blank=True)
    semester_start = models.DateField("學期第一天", null=True, blank=True)
    semester_end = models.DateField("學期最後一天", null=True, blank=True)

    class Meta:
        verbose_name = "系統設定"
        verbose_name_plural = "系統設定"

    def __str__(self):
        return "系統設定"

    @classmethod
    def load(cls):
        """取得設定；還沒儲存過時回傳預設值（加退選不限時間）。"""
        return cls.objects.filter(pk=1).first() or cls(pk=1)

    def enrollment_status(self, now=None):
        """回傳 "open"、"not_started" 或 "ended"；沒設定時間代表一律開放。"""
        now = now or timezone.now()
        if self.enrollment_start and now < self.enrollment_start:
            return "not_started"
        if self.enrollment_end and now > self.enrollment_end:
            return "ended"
        return "open"

    @property
    def enrollment_open(self):
        return self.enrollment_status() == "open"

    def closed_message(self):
        """加退選關閉時給學生看的說明。"""
        status = self.enrollment_status()
        if status == "not_started":
            start = timezone.localtime(self.enrollment_start)
            return f"加退選尚未開始，將於 {start:%Y-%m-%d %H:%M} 開放。"
        if status == "ended":
            end = timezone.localtime(self.enrollment_end)
            return f"加退選已於 {end:%Y-%m-%d %H:%M} 結束。"
        return ""

    def semester_dates(self, today=None):
        """學期起訖日；沒設定時從本週一起算 18 週。"""
        today = today or timezone.localdate()
        start = self.semester_start or today - timedelta(days=today.weekday())
        end = self.semester_end or start + timedelta(weeks=DEFAULT_SEMESTER_WEEKS, days=-1)
        return start, end
