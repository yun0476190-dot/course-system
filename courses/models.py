# courses/models.py
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


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
    """學生擴充資料：學號、所屬系所、學分上限。"""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="student_profile",
        verbose_name="使用者",
    )
    student_no = models.CharField("學號", max_length=20, unique=True)
    department = models.CharField("系所", max_length=50)
    max_credits = models.PositiveSmallIntegerField(
        "學期學分上限", default=25, validators=[MaxValueValidator(40)]
    )

    class Meta:
        verbose_name = "學生資料"
        verbose_name_plural = "學生資料"

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
    classroom = models.CharField("教室", max_length=50)
    capacity = models.PositiveSmallIntegerField("修課人數上限", default=60)
    syllabus = models.TextField("課程大綱", blank=True, default="")

    class Meta:
        verbose_name = "課程"
        verbose_name_plural = "課程"
        ordering = ["code"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_period__gte=models.F("start_period")),
                name="end_period_gte_start_period",
            ),
        ]

    def __str__(self):
        return f"[{self.code}] {self.name}"

    def clean(self):
        super().clean()
        if self.end_period < self.start_period:
            raise ValidationError("結束節次不可早於開始節次。")

    @property
    def enrolled_count(self):
        return self.enrollments.count()

    @property
    def is_full(self):
        return self.enrolled_count >= self.capacity

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
        CREDIT_UPDATE = "credit_update", "調整學分上限"

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