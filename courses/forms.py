# courses/forms.py
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.db.models import Q

from .models import (
    DEFAULT_MAX_CREDITS, DEFAULT_MIN_CREDITS, GRADE_CHOICES,
    ActivityLog, Classroom, Course, StudentProfile, SystemSetting, TeacherProfile, User,
)
from .services import course_change_problems


FIELD_CLASS = "w-full border dark:border-slate-600 dark:bg-slate-700 rounded-xl px-3 py-2"
CHECKBOX_CLASS = "h-4 w-4 rounded border-slate-300 dark:border-slate-600"


class StyledFormMixin:
    """幫表單欄位加上統一的 Tailwind 樣式；勾選框用較小的樣式。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            is_checkbox = isinstance(
                field.widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)
            )
            field.widget.attrs.setdefault("class", CHECKBOX_CLASS if is_checkbox else FIELD_CLASS)


def department_choices(extra=()):
    """學生與教師所屬的系所，供「限修系所」勾選（通識中心這類開課單位不列入）。"""
    names = set(StudentProfile.objects.values_list("department", flat=True))
    names |= set(TeacherProfile.objects.values_list("department", flat=True))
    names |= set(extra)
    return [(name, name) for name in sorted(n for n in names if n)]


class CourseForm(StyledFormMixin, forms.ModelForm):
    allowed_departments = forms.MultipleChoiceField(
        label="限修系所", required=False, widget=forms.CheckboxSelectMultiple,
        help_text="不勾選代表所有系所都能選。",
    )
    allowed_grades = forms.TypedMultipleChoiceField(
        label="限修年級", required=False, coerce=int, choices=GRADE_CHOICES,
        widget=forms.CheckboxSelectMultiple, help_text="不勾選代表所有年級都能選。",
    )

    class Meta:
        model = Course
        fields = [
            "code", "name", "teacher", "credits", "category", "department",
            "weekday", "start_period", "end_period", "classroom", "capacity",
            "allowed_departments", "allowed_grades", "syllabus",
        ]
        labels = {
            "code": "課程代碼", "name": "課程名稱", "teacher": "授課教師",
            "credits": "學分數", "category": "課程類別", "department": "開課系所",
            "weekday": "星期", "start_period": "開始節次", "end_period": "結束節次",
            "classroom": "教室", "capacity": "修課人數上限", "syllabus": "課程大綱",
        }
        widgets = {
            "syllabus": forms.Textarea(attrs={"rows": 6}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None and getattr(user, "role", None) == User.Role.TEACHER:
            self.fields.pop("teacher")
        else:
            # 只列出啟用中的教師；編輯時保留目前的授課教師，舊課程才能照常儲存
            self.fields["teacher"].queryset = User.objects.filter(
                Q(is_active=True) | Q(pk=self.instance.teacher_id),
                role=User.Role.TEACHER,
            ).order_by("username")
            self.fields["teacher"].label_from_instance = (
                lambda u: f"{u.display_name}（{u.username}）" if u.display_name else u.username
            )
        self.fields["allowed_departments"].choices = department_choices(
            self.instance.allowed_departments if self.instance.pk else ()
        )
        self.fields["classroom"].label_from_instance = (
            lambda room: f"{room.name}（容納 {room.capacity} 人）"
        )

    def clean(self):
        cleaned = super().clean()
        weekday = cleaned.get("weekday")
        start = cleaned.get("start_period")
        end = cleaned.get("end_period")
        teacher = cleaned.get("teacher") or self.user
        classroom = cleaned.get("classroom")
        capacity = cleaned.get("capacity")
        # 節次先後由 Course 的資料庫約束檢查並顯示錯誤，這裡只在節次合理時檢查時段衝突
        time_ok = bool(weekday and start and end and end >= start)
        others = Course.objects.exclude(pk=self.instance.pk).filter(
            weekday=weekday, start_period__lte=end, end_period__gte=start
        ) if time_ok else Course.objects.none()

        if teacher:
            clash = others.filter(teacher=teacher).first()
            if clash:
                self.add_error(None, f"與教師已開的「{clash.name}」時間衝堂。")
        if classroom:
            clash = others.filter(classroom=classroom).first()
            if clash:
                self.add_error(
                    "classroom",
                    f"{classroom.name} 在這個時段已排了「{clash.name}」（{clash.time_label}）。",
                )
            if capacity and capacity > classroom.capacity:
                self.add_error(
                    "capacity", f"超過教室 {classroom.name} 的容納人數 {classroom.capacity} 人。"
                )
        if self.instance.pk:
            self._check_enrolled_students(cleaned, time_ok)
        return cleaned

    def _check_enrolled_students(self, cleaned, time_ok):
        """編輯課程時，不能讓已選課的學生超收、衝堂、超過學分上限或失去修課資格。"""
        course = self.instance
        enrolled = course.enrollments.count()
        capacity = cleaned.get("capacity")
        if capacity is not None and capacity < enrolled:
            self.add_error("capacity", f"目前已有 {enrolled} 人選課，人數上限不能低於 {enrolled}。")
        if not enrolled or not time_ok or cleaned.get("credits") is None:
            return
        candidate = Course(
            pk=course.pk,
            weekday=cleaned["weekday"],
            start_period=cleaned["start_period"],
            end_period=cleaned["end_period"],
            credits=cleaned["credits"],
            allowed_departments=cleaned.get("allowed_departments", []),
            allowed_grades=cleaned.get("allowed_grades", []),
        )
        problems = course_change_problems(course.pk, candidate)
        if problems:
            names = "、".join(
                f"{student.display_name or student.username}（{reason}）"
                for student, reason in problems[:5]
            )
            more = f"等 {len(problems)} 人" if len(problems) > 5 else ""
            self.add_error(None, f"這次修改會影響已選課的學生：{names}{more}。請先調整或請學生退選。")


class AccountCreateForm(StyledFormMixin, forms.Form):
    """新增帳號共用欄位：帳號、初始密碼與姓名。"""

    role = None  # 由子類別指定

    username = forms.CharField(
        label="帳號", max_length=150, validators=[User.username_validator],
        help_text="只能使用文字、數字與 @ . + - _ 符號。",
    )
    password = forms.CharField(
        label="初始密碼", widget=forms.PasswordInput,
        help_text="至少 8 個字元，不能全是數字或過於常見。",
    )
    display_name = forms.CharField(label="姓名", max_length=50)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("此帳號已存在。")
        return username

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        if password:
            # 套用 settings.AUTH_PASSWORD_VALIDATORS 的密碼規則
            candidate = User(username=cleaned.get("username", ""))
            try:
                password_validation.validate_password(password, candidate)
            except forms.ValidationError as error:
                self.add_error("password", error)
        return cleaned

    def create_user(self, password_hash=None):
        """建立帳號。批次匯入時可傳入已加密的密碼，相同的初始密碼只需加密一次。"""
        data = self.cleaned_data
        user = User(
            username=User.normalize_username(data["username"]),
            role=self.role,
            display_name=data["display_name"],
            password=password_hash or make_password(data["password"]),
        )
        user.save()
        return user


class ProfileEditForm(StyledFormMixin, forms.ModelForm):
    """編輯學生／教師資料共用：姓名存在 User 上，由 view 另外儲存。"""

    display_name = forms.CharField(label="姓名", max_length=50)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["display_name"].initial = self.instance.user.display_name


class StudentCreateForm(AccountCreateForm):
    """管理員新增學生：同時建立帳號與學生資料。"""

    role = User.Role.STUDENT

    student_no = forms.CharField(label="學號", max_length=20)
    department = forms.CharField(label="系所", max_length=50)
    grade = forms.TypedChoiceField(label="年級", choices=GRADE_CHOICES, coerce=int, initial=1)
    min_credits = forms.IntegerField(
        label="學分下限", min_value=0, max_value=40, initial=DEFAULT_MIN_CREDITS,
    )
    max_credits = forms.IntegerField(
        label="學分上限", min_value=1, max_value=40, initial=DEFAULT_MAX_CREDITS,
    )
    phone = forms.CharField(label="聯絡電話", max_length=20, required=False)
    email_contact = forms.EmailField(label="聯絡 Email", required=False)

    def clean_student_no(self):
        student_no = self.cleaned_data["student_no"]
        if StudentProfile.objects.filter(student_no=student_no).exists():
            raise forms.ValidationError("此學號已存在。")
        return student_no

    def clean(self):
        cleaned = super().clean()
        low, high = cleaned.get("min_credits"), cleaned.get("max_credits")
        if low is not None and high is not None and low > high:
            self.add_error("min_credits", "學分下限不可高於學分上限。")
        return cleaned

    def save(self, password_hash=None):
        data = self.cleaned_data
        with transaction.atomic():
            user = self.create_user(password_hash)
            StudentProfile.objects.create(
                user=user,
                student_no=data["student_no"],
                department=data["department"],
                grade=data["grade"],
                min_credits=data["min_credits"],
                max_credits=data["max_credits"],
                phone=data["phone"],
                email_contact=data["email_contact"],
            )
        return user


class StudentEditForm(ProfileEditForm):
    """管理員編輯學生基本資料（不含密碼）。"""

    class Meta:
        model = StudentProfile
        fields = [
            "student_no", "department", "grade", "min_credits", "max_credits",
            "phone", "email_contact",
        ]
        labels = {
            "student_no": "學號", "department": "系所", "grade": "年級",
            "min_credits": "學分下限", "max_credits": "學分上限",
            "phone": "聯絡電話", "email_contact": "聯絡 Email",
        }

    def clean_student_no(self):
        student_no = self.cleaned_data["student_no"]
        qs = StudentProfile.objects.filter(student_no=student_no)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("此學號已存在。")
        return student_no


class TeacherCreateForm(AccountCreateForm):
    """管理員新增教師：同時建立帳號與教師資料。"""

    role = User.Role.TEACHER

    teacher_no = forms.CharField(label="教師編號", max_length=20)
    department = forms.CharField(label="系所", max_length=50)

    def clean_teacher_no(self):
        teacher_no = self.cleaned_data["teacher_no"]
        if TeacherProfile.objects.filter(teacher_no=teacher_no).exists():
            raise forms.ValidationError("此教師編號已存在。")
        return teacher_no

    def save(self, password_hash=None):
        data = self.cleaned_data
        with transaction.atomic():
            user = self.create_user(password_hash)
            TeacherProfile.objects.create(
                user=user, teacher_no=data["teacher_no"], department=data["department"],
            )
        return user


class TeacherEditForm(ProfileEditForm):
    """管理員編輯教師基本資料（不含密碼）。"""

    class Meta:
        model = TeacherProfile
        fields = ["teacher_no", "department"]
        labels = {"teacher_no": "教師編號", "department": "系所"}

    def clean_teacher_no(self):
        teacher_no = self.cleaned_data["teacher_no"]
        qs = TeacherProfile.objects.filter(teacher_no=teacher_no)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("此教師編號已存在。")
        return teacher_no


class ClassroomForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Classroom
        fields = ["name", "capacity"]
        labels = {"name": "教室名稱", "capacity": "容納人數"}

    def clean_capacity(self):
        capacity = self.cleaned_data["capacity"]
        if self.instance.pk:
            biggest = self.instance.courses.order_by("-capacity").first()
            if biggest and capacity < biggest.capacity:
                raise forms.ValidationError(
                    f"「{biggest.name}」的人數上限是 {biggest.capacity} 人，容納人數不能比它少。"
                )
        return capacity


class SystemSettingForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = SystemSetting
        fields = ["enrollment_start", "enrollment_end", "semester_start", "semester_end"]
        widgets = {
            "enrollment_start": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "enrollment_end": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "semester_start": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "semester_end": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }
        help_texts = {
            "enrollment_start": "留空代表不限開始時間。",
            "enrollment_end": "留空代表不限結束時間。",
            "semester_start": "匯出行事曆時，課程從這天起每週重複。",
            "semester_end": "沒設定學期日期時，從本週一起算 18 週。",
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("enrollment_start"), cleaned.get("enrollment_end")
        if start and end and end <= start:
            self.add_error("enrollment_end", "結束時間必須晚於開始時間。")
        first, last = cleaned.get("semester_start"), cleaned.get("semester_end")
        if first and last and last <= first:
            self.add_error("semester_end", "最後一天必須晚於第一天。")
        return cleaned


class ImportForm(forms.Form):
    """批次匯入的上傳欄位。"""

    file = forms.FileField(label="CSV 檔案")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if not upload.name.lower().endswith(".csv"):
            raise forms.ValidationError("請上傳 .csv 檔。")
        if upload.size > 1024 * 1024:
            raise forms.ValidationError("檔案不能超過 1 MB。")
        return upload


class LogFilterForm(StyledFormMixin, forms.Form):
    """操作紀錄的篩選條件。"""

    q = forms.CharField(label="關鍵字", required=False)
    action = forms.ChoiceField(
        label="動作", required=False, choices=[("", "全部動作"), *ActivityLog.Action.choices],
    )
    date_from = forms.DateField(
        label="從", required=False, widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        label="到", required=False, widget=forms.DateInput(attrs={"type": "date"}),
    )


class StyledPasswordChangeForm(StyledFormMixin, PasswordChangeForm):
    """使用者自行修改密碼，沿用 Django 內建的驗證規則。"""


class AdminSetPasswordForm(StyledFormMixin, SetPasswordForm):
    """管理員替使用者設定新密碼，同樣套用密碼規則。"""
