# courses/tests.py
import csv
import io
from datetime import date, datetime, timedelta
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .exports import _fold
from .forms import CourseForm, StudentCreateForm, StudentEditForm
from .models import (
    ActivityLog, Classroom, Course, Enrollment, StudentProfile, SystemSetting,
    TeacherProfile, User, WaitlistEntry,
)
from .utils import client_ip


PASSWORD = "Pass-word-123"
AJAX = {"x-requested-with": "XMLHttpRequest"}


def make_room(name="B101", capacity=100):
    return Classroom.objects.get_or_create(name=name, defaults={"capacity": capacity})[0]


def make_teacher(username, display_name="", department="資管系", **extra):
    user = User.objects.create_user(
        username, password=PASSWORD, role=User.Role.TEACHER,
        display_name=display_name, **extra,
    )
    TeacherProfile.objects.create(user=user, teacher_no=f"T-{username}", department=department)
    return user


def make_student(username, max_credits=25, min_credits=16, department="資管系", grade=1):
    user = User.objects.create_user(
        username, password=PASSWORD, role=User.Role.STUDENT,
        display_name=f"{username} 同學",
    )
    StudentProfile.objects.create(
        user=user, student_no=f"S-{username}", department=department, grade=grade,
        min_credits=min_credits, max_credits=max_credits,
    )
    return user


def make_admin():
    return User.objects.create_superuser("root", password=PASSWORD)


def make_course(code, teacher, weekday=1, start=1, end=2, credits=2, capacity=60, room=None, **extra):
    return Course.objects.create(
        code=code, name=f"課程{code}", teacher=teacher, credits=credits,
        category=Course.Category.MAJOR_ELECTIVE, department="資管系",
        weekday=weekday, start_period=start, end_period=end,
        classroom=room or make_room(), capacity=capacity, **extra,
    )


def course_post_data(room, teacher=None, **overrides):
    """開課表單的 POST 資料；管理員開課時才需要帶 teacher。"""
    data = {
        "code": "NEW001", "name": "新課程", "credits": "2",
        "category": Course.Category.GE_ELECTIVE, "department": "通識中心",
        "weekday": "3", "start_period": "1", "end_period": "2",
        "classroom": str(room.pk), "capacity": "30", "syllabus": "",
    }
    if teacher is not None:
        data["teacher"] = str(teacher.pk)
    data.update(overrides)
    return data


def course_edit_data(course, **overrides):
    """把既有課程的設定轉成表單資料，再套用要修改的欄位。"""
    data = {
        "code": course.code, "name": course.name, "teacher": str(course.teacher_id),
        "credits": str(course.credits), "category": course.category,
        "department": course.department, "weekday": str(course.weekday),
        "start_period": str(course.start_period), "end_period": str(course.end_period),
        "classroom": str(course.classroom_id), "capacity": str(course.capacity),
        "syllabus": course.syllabus,
        "allowed_departments": list(course.allowed_departments),
        "allowed_grades": [str(g) for g in course.allowed_grades],
    }
    data.update(overrides)
    return data


def csv_upload(name, rows, encoding="utf-8-sig"):
    buffer = io.StringIO()
    csv.writer(buffer).writerows(rows)
    return SimpleUploadedFile(name, buffer.getvalue().encode(encoding), content_type="text/csv")


def close_enrollment():
    SystemSetting.objects.create(pk=1, enrollment_end=timezone.now() - timedelta(days=1))


# 測試改用較快的密碼雜湊，避免建立大量帳號時拖慢測試
@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class BaseTestCase(TestCase):
    pass


class EnrollTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1", "王教授")
        self.student = make_student("s1")
        self.client.force_login(self.student)

    def post(self, name, course):
        return self.client.post(reverse(name, args=[course.pk]), headers=AJAX).json()

    def test_enroll_success_creates_enrollment_and_log(self):
        course = make_course("C1", self.teacher)
        data = self.post("enroll", course)
        self.assertEqual(
            data, {"ok": True, "message": f"已成功加選「{course.name}」。", "course_id": course.pk},
        )
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=course).exists())
        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.ENROLL, actor=self.student).exists()
        )

    def test_enroll_twice_rejected(self):
        course = make_course("C1", self.teacher)
        self.post("enroll", course)
        self.assertFalse(self.post("enroll", course)["ok"])
        self.assertEqual(Enrollment.objects.filter(student=self.student).count(), 1)

    def test_time_conflict_rejected(self):
        self.post("enroll", make_course("C1", self.teacher, weekday=1, start=1, end=3))
        data = self.post("enroll", make_course("C2", make_teacher("t2"), weekday=1, start=3, end=4))
        self.assertFalse(data["ok"])
        self.assertIn("衝堂", data["message"])

    def test_full_course_rejected(self):
        course = make_course("C1", self.teacher, capacity=1)
        Enrollment.objects.create(student=make_student("s2"), course=course)
        data = self.post("enroll", course)
        self.assertFalse(data["ok"])
        self.assertIn("已滿", data["message"])

    def test_credit_limit_rejected(self):
        profile = self.student.student_profile
        profile.min_credits, profile.max_credits = 0, 3
        profile.save()
        self.post("enroll", make_course("C1", self.teacher, credits=2))
        data = self.post("enroll", make_course("C2", self.teacher, weekday=2, credits=2))
        self.assertFalse(data["ok"])
        self.assertIn("超過上限", data["message"])

    def test_non_ajax_enroll_redirects_back_to_list(self):
        course = make_course("C1", self.teacher)
        response = self.client.post(reverse("enroll", args=[course.pk]))
        self.assertRedirects(
            response, f"{reverse('course_list')}#course-{course.pk}", fetch_redirect_response=False,
        )

    def test_drop_removes_enrollment(self):
        course = make_course("C1", self.teacher)
        Enrollment.objects.create(student=self.student, course=course)
        self.post("drop", course)
        self.assertFalse(Enrollment.objects.filter(student=self.student, course=course).exists())
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.DROP).exists())

    def test_closed_window_blocks_enroll_and_drop(self):
        enrolled = make_course("C1", self.teacher)
        Enrollment.objects.create(student=self.student, course=enrolled)
        other = make_course("C2", self.teacher, weekday=2)
        close_enrollment()
        for name, course in [("enroll", other), ("drop", enrolled), ("waitlist_join", other)]:
            with self.subTest(action=name):
                data = self.post(name, course)
                self.assertFalse(data["ok"])
                self.assertIn("加退選已於", data["message"])
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=enrolled).exists())
        self.assertFalse(Enrollment.objects.filter(course=other).exists())

    def test_window_not_started_blocks_enroll(self):
        SystemSetting.objects.create(pk=1, enrollment_start=timezone.now() + timedelta(days=3))
        data = self.post("enroll", make_course("C1", self.teacher))
        self.assertFalse(data["ok"])
        self.assertIn("尚未開始", data["message"])


class EligibilityTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1")
        self.student = make_student("s1", department="資管系", grade=1)
        self.client.force_login(self.student)

    def enroll(self, course):
        return self.client.post(reverse("enroll", args=[course.pk]), headers=AJAX).json()

    def test_department_restriction(self):
        course = make_course("C1", self.teacher, allowed_departments=["企管系"])
        data = self.enroll(course)
        self.assertFalse(data["ok"])
        self.assertIn("限企管系學生", data["message"])

    def test_grade_restriction(self):
        course = make_course("C1", self.teacher, allowed_grades=[3, 4])
        data = self.enroll(course)
        self.assertFalse(data["ok"])
        self.assertIn("三、四年級", data["message"])

    def test_eligible_student_can_enroll(self):
        course = make_course("C1", self.teacher, allowed_departments=["資管系"], allowed_grades=[1, 2])
        self.assertTrue(self.enroll(course)["ok"])


class MinCreditTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1")

    def test_drop_below_minimum_blocked_once_reached(self):
        student = make_student("s1", min_credits=4)
        first = make_course("C1", self.teacher, weekday=1)
        second = make_course("C2", self.teacher, weekday=2)
        Enrollment.objects.create(student=student, course=first)
        Enrollment.objects.create(student=student, course=second)
        self.client.force_login(student)
        data = self.client.post(reverse("drop", args=[first.pk]), headers=AJAX).json()
        self.assertFalse(data["ok"])
        self.assertIn("低於下限 4", data["message"])
        self.assertTrue(Enrollment.objects.filter(student=student, course=first).exists())

    def test_student_below_minimum_can_still_drop(self):
        student = make_student("s1")  # 下限 16，只選 2 學分
        course = make_course("C1", self.teacher)
        Enrollment.objects.create(student=student, course=course)
        self.client.force_login(student)
        self.assertTrue(self.client.post(reverse("drop", args=[course.pk]), headers=AJAX).json()["ok"])

    def test_admin_force_drop_ignores_minimum(self):
        student = make_student("s1", min_credits=2)
        enrollment = Enrollment.objects.create(student=student, course=make_course("C1", self.teacher))
        self.client.force_login(make_admin())
        self.client.post(reverse("admin_force_drop", args=[enrollment.pk]))
        self.assertFalse(Enrollment.objects.filter(pk=enrollment.pk).exists())


class WaitlistTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1")
        self.course = make_course("C1", self.teacher, capacity=1)
        self.holder = make_student("holder")
        Enrollment.objects.create(student=self.holder, course=self.course)

    def join(self, student, course=None):
        self.client.force_login(student)
        return self.client.post(
            reverse("waitlist_join", args=[(course or self.course).pk]), headers=AJAX
        ).json()

    def test_join_full_course_gets_position(self):
        first, second = make_student("s1"), make_student("s2")
        self.assertIn("第 1 位", self.join(first)["message"])
        self.assertIn("第 2 位", self.join(second)["message"])
        self.assertEqual(WaitlistEntry.objects.filter(course=self.course).count(), 2)

    def test_cannot_join_when_seats_available(self):
        open_course = make_course("C2", self.teacher, weekday=2, capacity=5)
        data = self.join(make_student("s1"), open_course)
        self.assertFalse(data["ok"])
        self.assertIn("直接加選", data["message"])

    def test_cannot_join_conflicting_course(self):
        student = make_student("s1")
        Enrollment.objects.create(student=student, course=make_course("C2", make_teacher("t2"), weekday=1))
        data = self.join(student)
        self.assertFalse(data["ok"])
        self.assertIn("衝堂", data["message"])

    def test_drop_promotes_first_in_line(self):
        waiting = make_student("s1")
        WaitlistEntry.objects.create(student=waiting, course=self.course)
        self.client.force_login(self.holder)
        self.client.post(reverse("drop", args=[self.course.pk]), headers=AJAX)
        self.assertTrue(Enrollment.objects.filter(student=waiting, course=self.course).exists())
        self.assertFalse(WaitlistEntry.objects.exists())
        log = ActivityLog.objects.get(action=ActivityLog.Action.WAITLIST_PROMOTE)
        self.assertIsNone(log.actor)

    def test_promotion_skips_student_with_conflict(self):
        blocked, next_in_line = make_student("s1"), make_student("s2")
        WaitlistEntry.objects.create(student=blocked, course=self.course)
        WaitlistEntry.objects.create(student=next_in_line, course=self.course)
        # 登記候補後又選了同時段的課，遞補時要被跳過
        Enrollment.objects.create(
            student=blocked, course=make_course("C2", make_teacher("t2"), weekday=1, room=make_room("R2")),
        )
        self.client.force_login(self.holder)
        self.client.post(reverse("drop", args=[self.course.pk]), headers=AJAX)
        self.assertTrue(Enrollment.objects.filter(student=next_in_line, course=self.course).exists())
        self.assertFalse(Enrollment.objects.filter(student=blocked, course=self.course).exists())
        self.assertTrue(WaitlistEntry.objects.filter(student=blocked, course=self.course).exists())

    def test_leave_waitlist(self):
        student = make_student("s1")
        self.join(student)
        data = self.client.post(reverse("waitlist_leave", args=[self.course.pk]), headers=AJAX).json()
        self.assertTrue(data["ok"])
        self.assertFalse(WaitlistEntry.objects.exists())
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.WAITLIST_LEAVE).exists())

    def test_raising_capacity_promotes(self):
        waiting = make_student("s1")
        WaitlistEntry.objects.create(student=waiting, course=self.course)
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("course_edit", args=[self.course.pk]), course_edit_data(self.course, capacity="2"),
        )
        self.assertRedirects(response, reverse("teacher_courses"), fetch_redirect_response=False)
        self.assertTrue(Enrollment.objects.filter(student=waiting, course=self.course).exists())

    def test_force_drop_promotes(self):
        waiting = make_student("s1")
        WaitlistEntry.objects.create(student=waiting, course=self.course)
        self.client.force_login(make_admin())
        enrollment = Enrollment.objects.get(student=self.holder)
        self.client.post(reverse("admin_force_drop", args=[enrollment.pk]))
        self.assertTrue(Enrollment.objects.filter(student=waiting, course=self.course).exists())


class CourseListTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1", "王教授")
        self.student = make_student("s1")
        self.client.force_login(self.student)

    def test_shows_remaining_seats(self):
        course = make_course("C1", self.teacher, capacity=60)
        Enrollment.objects.create(student=make_student("s2"), course=course)
        self.assertContains(self.client.get(reverse("course_list")), "剩 59 名")

    def test_marks_conflicting_course(self):
        Enrollment.objects.create(student=self.student, course=make_course("C1", self.teacher, weekday=1))
        make_course("C2", make_teacher("t2"), weekday=1, room=make_room("R2"))
        response = self.client.get(reverse("course_list"))
        self.assertContains(response, "與「課程C1」衝堂")
        self.assertContains(response, "無法加選")

    def test_full_course_offers_waitlist(self):
        course = make_course("C1", self.teacher, capacity=1)
        Enrollment.objects.create(student=make_student("s2"), course=course)
        response = self.client.get(reverse("course_list"))
        self.assertContains(response, "已額滿")
        self.assertContains(response, "登記候補")

    def test_closed_window_message(self):
        make_course("C1", self.teacher)
        close_enrollment()
        self.assertContains(self.client.get(reverse("course_list")), "目前只能瀏覽課程")

    def test_below_minimum_hint(self):
        self.assertContains(self.client.get(reverse("course_list")), "還差 16 學分達到下限")


class PermissionTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1")
        self.other_teacher = make_teacher("t2")
        self.student = make_student("s1")
        self.admin = make_admin()

    def test_student_cannot_open_teacher_or_admin_pages(self):
        self.client.force_login(self.student)
        for name in ["teacher_courses", "course_create", "admin_dashboard", "admin_students", "admin_import"]:
            with self.subTest(page=name):
                self.assertRedirects(
                    self.client.get(reverse(name)), reverse("home"), fetch_redirect_response=False,
                )

    def test_teacher_cannot_open_admin_pages(self):
        self.client.force_login(self.teacher)
        self.assertRedirects(
            self.client.get(reverse("admin_settings")), reverse("home"), fetch_redirect_response=False,
        )

    def test_teacher_cannot_edit_other_teachers_course(self):
        course = make_course("C1", self.other_teacher)
        self.client.force_login(self.teacher)
        response = self.client.post(
            reverse("course_edit", args=[course.pk]), course_edit_data(course, name="被改掉"),
        )
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        course.refresh_from_db()
        self.assertNotEqual(course.name, "被改掉")

    def test_teacher_cannot_download_other_teachers_roster(self):
        course = make_course("C1", self.other_teacher)
        self.client.force_login(self.teacher)
        self.assertRedirects(
            self.client.get(reverse("course_roster_csv", args=[course.pk])),
            reverse("home"), fetch_redirect_response=False,
        )

    def test_superuser_cannot_enroll_as_student(self):
        course = make_course("C1", self.teacher)
        self.client.force_login(self.admin)
        self.client.post(reverse("enroll", args=[course.pk]))
        self.assertFalse(Enrollment.objects.exists())
        self.assertRedirects(
            self.client.get(reverse("course_list")), reverse("home"), fetch_redirect_response=False,
        )

    def test_createsuperuser_gets_admin_role(self):
        self.assertEqual(self.admin.role, User.Role.ADMIN)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)


class CourseCreateTests(BaseTestCase):
    def test_admin_keeps_selected_teacher(self):
        teacher = make_teacher("t2", "李教授")
        self.client.force_login(make_admin())
        response = self.client.post(
            reverse("admin_course_create"), course_post_data(make_room(), teacher=teacher),
        )
        self.assertRedirects(response, reverse("admin_courses"), fetch_redirect_response=False)
        self.assertEqual(Course.objects.get(code="NEW001").teacher, teacher)

    def test_teacher_course_is_assigned_to_self(self):
        teacher = make_teacher("t1")
        self.client.force_login(teacher)
        self.client.post(reverse("course_create"), course_post_data(make_room()))
        self.assertEqual(Course.objects.get(code="NEW001").teacher, teacher)


class CourseFormTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1")
        self.room = make_room("B101", 100)

    def form(self, user=None, instance=None, **overrides):
        if instance is not None:
            data = course_edit_data(instance, **overrides)
        else:
            data = course_post_data(self.room, **overrides)
        return CourseForm(data=data, instance=instance, user=user or self.teacher)

    def test_blank_start_period_is_invalid_without_crashing(self):
        form = self.form(start_period="")
        self.assertFalse(form.is_valid())
        self.assertIn("start_period", form.errors)

    def test_reversed_periods_show_a_single_error(self):
        form = self.form(start_period="5", end_period="3")
        self.assertFalse(form.is_valid())
        self.assertEqual(form.non_field_errors(), ["結束節次不可早於開始節次。"])

    def test_teacher_time_clash_detected(self):
        make_course("C1", self.teacher, weekday=3, start=2, end=3, room=make_room("R2"))
        form = self.form()
        self.assertFalse(form.is_valid())
        self.assertIn("衝堂", form.non_field_errors()[0])

    def test_classroom_clash_detected(self):
        make_course("C1", make_teacher("t2"), weekday=3, start=2, end=3, room=self.room)
        form = self.form()
        self.assertFalse(form.is_valid())
        self.assertIn("已排了", form.errors["classroom"][0])

    def test_capacity_cannot_exceed_room(self):
        small = make_room("S1", 20)
        form = self.form(classroom=str(small.pk), capacity="30")
        self.assertFalse(form.is_valid())
        self.assertIn("容納人數 20", form.errors["capacity"][0])

    def test_capacity_not_below_enrolled(self):
        course = make_course("C1", self.teacher, capacity=5)
        for name in ("s1", "s2"):
            Enrollment.objects.create(student=make_student(name), course=course)
        form = self.form(instance=course, capacity="1")
        self.assertFalse(form.is_valid())
        self.assertIn("已有 2 人選課", form.errors["capacity"][0])

    def test_time_change_cannot_create_student_conflict(self):
        student = make_student("s1")
        course = make_course("C1", self.teacher, weekday=1)
        Enrollment.objects.create(student=student, course=course)
        Enrollment.objects.create(
            student=student, course=make_course("C2", make_teacher("t2"), weekday=2, room=make_room("R2")),
        )
        form = self.form(instance=course, weekday="2")
        self.assertFalse(form.is_valid())
        self.assertIn("s1 同學（與「課程C2」衝堂）", form.non_field_errors()[0])

    def test_credit_increase_cannot_exceed_student_limit(self):
        student = make_student("s1", min_credits=0, max_credits=4)
        course = make_course("C1", self.teacher, weekday=1)
        Enrollment.objects.create(student=student, course=course)
        Enrollment.objects.create(student=student, course=make_course("C2", self.teacher, weekday=2))
        form = self.form(instance=course, credits="3")
        self.assertFalse(form.is_valid())
        self.assertIn("學分會超過上限 4", form.non_field_errors()[0])

    def test_new_restriction_cannot_exclude_enrolled_student(self):
        make_student("other", department="企管系")  # 讓「企管系」成為可勾選的系所
        course = make_course("C1", self.teacher)
        Enrollment.objects.create(student=make_student("s1"), course=course)
        form = self.form(instance=course, allowed_departments=["企管系"])
        self.assertFalse(form.is_valid())
        self.assertIn("不符合新的修課資格", form.non_field_errors()[0])

    def test_inactive_teacher_hidden_from_new_course(self):
        inactive = make_teacher("t2", is_active=False)
        choices = CourseForm(user=make_admin()).fields["teacher"].queryset
        self.assertIn(self.teacher, choices)
        self.assertNotIn(inactive, choices)

    def test_inactive_teacher_kept_when_editing_their_course(self):
        inactive = make_teacher("t2", is_active=False)
        course = make_course("C1", inactive)
        form = CourseForm(instance=course, user=make_admin())
        self.assertIn(inactive, form.fields["teacher"].queryset)


class DisplayTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1", "王教授")
        self.student = make_student("s1")
        self.client.force_login(self.student)

    def test_timetable_multi_period_course_spans_rows(self):
        course = make_course("C1", self.teacher, weekday=2, start=3, end=5)
        Enrollment.objects.create(student=self.student, course=course)
        response = self.client.get(reverse("my_timetable"))
        self.assertContains(response, 'rowspan="3"', count=1)

    def test_timetable_lists_overlapping_courses(self):
        first = make_course("C1", self.teacher, weekday=1, start=1, end=3)
        overlapping = make_course("C2", make_teacher("t2"), weekday=1, start=2, end=2)
        Enrollment.objects.create(student=self.student, course=first)
        Enrollment.objects.create(student=self.student, course=overlapping)
        response = self.client.get(reverse("my_timetable"))
        self.assertEqual(response.context["overlaps"], [overlapping])
        self.assertContains(response, "時段重疊")

    def test_timetable_warns_below_minimum(self):
        self.assertContains(self.client.get(reverse("my_timetable")), "還差 16 學分")

    def test_course_pages_show_teacher_display_name(self):
        course = make_course("C1", self.teacher)
        self.assertContains(self.client.get(reverse("course_list")), "王教授")
        self.assertContains(self.client.get(reverse("course_detail", args=[course.pk])), "王教授")


class ExportTests(BaseTestCase):
    def setUp(self):
        self.teacher = make_teacher("t1", "王教授")
        self.student = make_student("s1")
        self.course = make_course("C1", self.teacher, weekday=3, start=2, end=3)
        Enrollment.objects.create(student=self.student, course=self.course)

    def test_roster_csv(self):
        self.client.force_login(self.teacher)
        response = self.client.get(reverse("course_roster_csv", args=[self.course.pk]))
        content = response.content.decode("utf-8")
        self.assertTrue(content.startswith("﻿序號,學號,姓名"))
        self.assertIn("s1 同學", content)
        self.assertIn("attachment", response["Content-Disposition"])

    def test_timetable_calendar(self):
        SystemSetting.objects.create(pk=1, semester_start=date(2026, 9, 14), semester_end=date(2027, 1, 15))
        self.client.force_login(self.student)
        response = self.client.get(reverse("timetable_calendar"))
        self.assertEqual(response["Content-Type"], "text/calendar; charset=utf-8")
        content = response.content.decode("utf-8")
        self.assertIn("SUMMARY:課程C1", content)
        # 2026-09-14 是週一，週三第 2 節 09:10 開始、第 3 節 11:00 下課
        self.assertIn("DTSTART;TZID=Asia/Taipei:20260916T091000", content)
        self.assertIn("DTEND;TZID=Asia/Taipei:20260916T110000", content)
        self.assertIn("RRULE:FREQ=WEEKLY;UNTIL=20270115T155959Z", content)

    def test_roster_csv_neutralises_formulas(self):
        self.student.display_name = "=HYPERLINK(\"http://evil.example\")"
        self.student.save()
        self.client.force_login(self.teacher)
        content = self.client.get(reverse("course_roster_csv", args=[self.course.pk])).content.decode("utf-8")
        self.assertIn("'=HYPERLINK", content)

    def test_long_calendar_lines_are_folded(self):
        folded = _fold("DESCRIPTION:" + "很長的課程說明" * 20)
        for line in folded.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)


class LoginTests(BaseTestCase):
    def setUp(self):
        self.student = make_student("s1")

    def login(self, next_url):
        return self.client.post(
            reverse("login"), {"username": "s1", "password": PASSWORD, "next": next_url},
        )

    def test_external_next_is_ignored(self):
        for next_url in ["https://evil.example.com/", "//evil.example.com/"]:
            with self.subTest(next_url=next_url):
                self.client.logout()
                self.assertRedirects(
                    self.login(next_url), reverse("home"), fetch_redirect_response=False,
                )

    def test_local_next_is_followed(self):
        self.assertRedirects(self.login("/timetable/"), "/timetable/", fetch_redirect_response=False)

    def test_login_is_logged(self):
        self.login("")
        log = ActivityLog.objects.get(action=ActivityLog.Action.LOGIN)
        self.assertEqual(log.actor, self.student)
        self.assertEqual(log.ip_address, "127.0.0.1")


class ClientIpTests(SimpleTestCase):
    def test_invalid_forwarded_for_falls_back_to_remote_addr(self):
        request = RequestFactory().get(
            "/", HTTP_X_FORWARDED_FOR="not-an-ip", REMOTE_ADDR="10.0.0.5",
        )
        self.assertEqual(client_ip(request), "10.0.0.5")

    def test_valid_forwarded_for_is_used(self):
        request = RequestFactory().get(
            "/", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1", REMOTE_ADDR="10.0.0.5",
        )
        self.assertEqual(client_ip(request), "203.0.113.7")


class AdminAccountTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(make_admin())

    def student_data(self, **overrides):
        data = {
            "username": "newstudent", "password": "Strong-pass-42", "display_name": "新同學",
            "student_no": "S12345", "department": "資管系", "grade": "2",
            "min_credits": "16", "max_credits": "25", "phone": "", "email_contact": "",
        }
        data.update(overrides)
        return data

    def test_weak_password_rejected(self):
        form = StudentCreateForm(data=self.student_data(password="12345678"))
        self.assertFalse(form.is_valid())
        self.assertIn("password", form.errors)

    def test_invalid_username_rejected(self):
        form = StudentCreateForm(data=self.student_data(username="bad name"))
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_duplicate_username_is_case_insensitive(self):
        make_student("s1")
        form = StudentCreateForm(data=self.student_data(username="S1"))
        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_min_credits_cannot_exceed_max(self):
        form = StudentCreateForm(data=self.student_data(min_credits="20", max_credits="18"))
        self.assertFalse(form.is_valid())
        self.assertIn("min_credits", form.errors)

    def test_create_student_through_view(self):
        response = self.client.post(reverse("admin_student_create"), self.student_data())
        self.assertRedirects(response, reverse("admin_students"), fetch_redirect_response=False)
        user = User.objects.get(username="newstudent")
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertTrue(user.check_password("Strong-pass-42"))
        profile = user.student_profile
        self.assertEqual((profile.student_no, profile.grade, profile.min_credits), ("S12345", 2, 16))

    def test_max_credits_zero_rejected_on_edit(self):
        profile = make_student("s1").student_profile
        form = StudentEditForm(
            data={
                "student_no": profile.student_no, "department": "資管系", "grade": "1",
                "min_credits": "0", "max_credits": "0", "phone": "", "email_contact": "",
                "display_name": "s1 同學",
            },
            instance=profile,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("max_credits", form.errors)

    def test_set_credit_range(self):
        student = make_student("s1")
        url = reverse("admin_set_credits", args=[student.student_profile.pk])
        self.client.post(url, {"min_credits": "12", "max_credits": "28"})
        student.student_profile.refresh_from_db()
        self.assertEqual((student.student_profile.min_credits, student.student_profile.max_credits), (12, 28))
        self.client.post(url, {"min_credits": "30", "max_credits": "20"})
        student.student_profile.refresh_from_db()
        self.assertEqual(student.student_profile.min_credits, 12)

    def test_toggle_account_disables_and_logs(self):
        student = make_student("s1")
        response = self.client.post(reverse("admin_toggle_account", args=[student.pk]))
        self.assertRedirects(response, reverse("admin_students"), fetch_redirect_response=False)
        student.refresh_from_db()
        self.assertFalse(student.is_active)
        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.ACCOUNT_DISABLE).exists()
        )

    def test_reset_password(self):
        teacher = make_teacher("t1")
        response = self.client.post(reverse("admin_reset_password", args=[teacher.pk]), {
            "new_password1": "Brand-new-77", "new_password2": "Brand-new-77",
        })
        self.assertRedirects(response, reverse("admin_teachers"), fetch_redirect_response=False)
        teacher.refresh_from_db()
        self.assertTrue(teacher.check_password("Brand-new-77"))
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.PASSWORD_RESET).exists())

    def test_cannot_reset_superuser_password(self):
        other = User.objects.create_superuser("root2", password=PASSWORD)
        self.client.post(reverse("admin_reset_password", args=[other.pk]), {
            "new_password1": "Brand-new-77", "new_password2": "Brand-new-77",
        })
        other.refresh_from_db()
        self.assertTrue(other.check_password(PASSWORD))


class AdminListTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_student_search(self):
        make_student("alice")
        make_student("bob")
        page = self.client.get(reverse("admin_students"), {"q": "alice"}).context["page"]
        self.assertEqual([s.user.username for s in page], ["alice"])

    def test_below_minimum_filter(self):
        make_student("low")
        enough = make_student("enough", min_credits=2)
        Enrollment.objects.create(student=enough, course=make_course("C1", make_teacher("t1")))
        page = self.client.get(reverse("admin_students"), {"below_min": "1"}).context["page"]
        self.assertEqual([s.user.username for s in page], ["low"])

    def test_pagination(self):
        for number in range(25):
            make_student(f"s{number:02d}")
        response = self.client.get(reverse("admin_students"), {"page": "2"})
        self.assertEqual(response.context["page"].paginator.num_pages, 2)
        self.assertEqual(len(response.context["page"].object_list), 5)

    def test_log_filter_by_action(self):
        ActivityLog.record(ActivityLog.Action.LOGIN, "a")
        ActivityLog.record(ActivityLog.Action.ENROLL, "b")
        page = self.client.get(reverse("admin_logs"), {"action": "enroll"}).context["page"]
        self.assertEqual([log.target for log in page], ["b"])

    def test_overview_counts(self):
        make_student("low")
        response = self.client.get(reverse("admin_dashboard"))
        kpis = {kpi["label"]: kpi["value"] for kpi in response.context["kpis"]}
        self.assertEqual(kpis["學生"], 1)
        self.assertEqual(kpis["未達學分下限"], 1)
        self.assertContains(response, "各課程選課率")


class ClassroomTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_create_classroom(self):
        self.client.post(reverse("admin_classroom_create"), {"name": "C301", "capacity": "45"})
        self.assertEqual(Classroom.objects.get(name="C301").capacity, 45)
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.CLASSROOM_CREATE).exists())

    def test_capacity_not_below_course(self):
        room = make_room("C301", 60)
        make_course("C1", make_teacher("t1"), capacity=50, room=room)
        response = self.client.post(
            reverse("admin_classroom_edit", args=[room.pk]), {"name": "C301", "capacity": "40"},
        )
        self.assertContains(response, "容納人數不能比它少")
        room.refresh_from_db()
        self.assertEqual(room.capacity, 60)

    def test_cannot_delete_room_in_use(self):
        room = make_room("C301")
        make_course("C1", make_teacher("t1"), room=room)
        self.client.post(reverse("admin_classroom_delete", args=[room.pk]))
        self.assertTrue(Classroom.objects.filter(pk=room.pk).exists())

    def test_delete_unused_room(self):
        room = make_room("C301")
        self.client.post(reverse("admin_classroom_delete", args=[room.pk]))
        self.assertFalse(Classroom.objects.filter(pk=room.pk).exists())


class ImportTests(BaseTestCase):
    STUDENT_HEADER = ["帳號", "初始密碼", "姓名", "學號", "系所", "年級"]

    def setUp(self):
        self.admin = make_admin()
        self.client.force_login(self.admin)

    def upload(self, kind, rows, encoding="utf-8-sig"):
        return self.client.post(reverse("admin_import"), {
            "kind": kind, f"{kind}-file": csv_upload(f"{kind}.csv", rows, encoding),
        })

    def test_import_students(self):
        response = self.upload("students", [
            self.STUDENT_HEADER,
            ["s9001", "Welcome-2026", "王小明", "S9001", "資管系", "1"],
            ["s9002", "Welcome-2026", "李小華", "S9002", "企管系", "2"],
        ])
        self.assertRedirects(response, reverse("admin_students"), fetch_redirect_response=False)
        user = User.objects.get(username="s9002")
        self.assertTrue(user.check_password("Welcome-2026"))
        self.assertEqual((user.student_profile.grade, user.student_profile.min_credits), (2, 16))
        self.assertTrue(ActivityLog.objects.filter(action=ActivityLog.Action.BULK_IMPORT).exists())

    def test_one_bad_row_imports_nothing(self):
        response = self.upload("students", [
            self.STUDENT_HEADER,
            ["s9001", "Welcome-2026", "王小明", "S9001", "資管系", "1"],
            ["s9002", "12345678", "李小華", "S9002", "資管系", "1"],
        ])
        self.assertContains(response, "第 3 列")
        self.assertFalse(User.objects.filter(username__startswith="s900").exists())

    def test_duplicate_rows_in_file(self):
        response = self.upload("students", [
            self.STUDENT_HEADER,
            ["s9001", "Welcome-2026", "王小明", "S9001", "資管系", "1"],
            ["S9001", "Welcome-2026", "王小明", "S9009", "資管系", "1"],
        ])
        self.assertContains(response, "在檔案中重複")
        self.assertFalse(User.objects.filter(username="s9001").exists())

    def test_big5_file(self):
        self.upload("teachers", [
            ["帳號", "初始密碼", "姓名", "教師編號", "系所"],
            ["t9001", "Welcome-2026", "陳教授", "T9001", "資訊管理系"],
        ], encoding="cp950")
        self.assertEqual(User.objects.get(username="t9001").display_name, "陳教授")

    def test_import_courses(self):
        make_teacher("t1")
        make_room("B101", 60)
        make_student("junior", grade=3)  # 確保年級資料存在
        response = self.upload("courses", [
            ["課程代碼", "課程名稱", "授課教師帳號", "學分數", "課程類別", "星期",
             "開始節次", "結束節次", "教室", "修課人數上限", "限修年級"],
            ["IM9001", "資料庫系統", "t1", "3", "專業選修", "週三", "2", "4", "B101", "40", "三、四年級"],
        ])
        self.assertRedirects(response, reverse("admin_courses"), fetch_redirect_response=False)
        course = Course.objects.get(code="IM9001")
        self.assertEqual((course.weekday, course.category), (3, Course.Category.MAJOR_ELECTIVE))
        self.assertEqual(course.allowed_grades, [3, 4])

    def test_course_rows_checked_against_each_other(self):
        make_teacher("t1")
        make_room("B101", 60)
        header = ["課程代碼", "課程名稱", "授課教師帳號", "學分數", "課程類別", "星期",
                  "開始節次", "結束節次", "教室", "修課人數上限"]
        response = self.upload("courses", [
            header,
            ["IM9001", "資料庫", "t1", "3", "專業選修", "3", "2", "4", "B101", "40"],
            ["IM9002", "作業系統", "t1", "3", "專業選修", "3", "3", "5", "B101", "40"],
        ])
        self.assertContains(response, "第 3 列")
        self.assertFalse(Course.objects.exists())

    def test_unknown_classroom(self):
        make_teacher("t1")
        response = self.upload("courses", [
            ["課程代碼", "課程名稱", "授課教師帳號", "學分數", "課程類別", "星期",
             "開始節次", "結束節次", "教室", "修課人數上限"],
            ["IM9001", "資料庫", "t1", "3", "專業選修", "3", "2", "4", "Z999", "40"],
        ])
        self.assertContains(response, "找不到教室「Z999」")

    def test_missing_columns(self):
        response = self.upload("students", [["帳號", "姓名"], ["s1", "王小明"]])
        self.assertContains(response, "缺少欄位")

    def test_template_download(self):
        response = self.client.get(reverse("admin_import_template", args=["students"]))
        self.assertTrue(response.content.decode("utf-8").startswith("﻿帳號,初始密碼,姓名"))


class SettingsTests(BaseTestCase):
    def setUp(self):
        self.client.force_login(make_admin())

    def test_save_enrollment_period(self):
        self.client.post(reverse("admin_settings"), {
            "enrollment_start": "2026-10-01T09:00", "enrollment_end": "2026-10-10T17:00",
            "semester_start": "2026-09-14", "semester_end": "2027-01-15",
        })
        setting = SystemSetting.load()
        self.assertEqual(setting.enrollment_end, timezone.make_aware(datetime(2026, 10, 10, 17, 0)))
        self.assertEqual(setting.semester_start, date(2026, 9, 14))

    def test_end_before_start_rejected(self):
        response = self.client.post(reverse("admin_settings"), {
            "enrollment_start": "2026-10-10T09:00", "enrollment_end": "2026-10-01T17:00",
            "semester_start": "", "semester_end": "",
        })
        self.assertContains(response, "結束時間必須晚於開始時間")
        self.assertFalse(SystemSetting.objects.exists())

    def test_quick_close_and_open(self):
        self.client.post(reverse("admin_settings"), {"quick": "close"})
        self.assertEqual(SystemSetting.load().enrollment_status(), "ended")
        self.client.post(reverse("admin_settings"), {"quick": "open"})
        self.assertEqual(SystemSetting.load().enrollment_status(), "open")


class PasswordChangeTests(BaseTestCase):
    def test_user_can_change_password(self):
        student = make_student("s1")
        self.client.force_login(student)
        self.assertEqual(self.client.get(reverse("password_change")).status_code, 200)
        response = self.client.post(reverse("password_change"), {
            "old_password": PASSWORD,
            "new_password1": "Another-pass-77",
            "new_password2": "Another-pass-77",
        })
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        student.refresh_from_db()
        self.assertTrue(student.check_password("Another-pass-77"))
        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.PASSWORD_CHANGE, actor=student).exists()
        )

    def test_password_page_requires_login(self):
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])


class SeedDataTests(BaseTestCase):
    def test_seed_keeps_edited_courses_and_resets_demo_passwords(self):
        call_command("seed_data", stdout=StringIO())
        course = Course.objects.get(code="GE1001")
        course.capacity = 5
        course.save()
        student = User.objects.get(username="student1")
        student.set_password("Changed-pass-99")
        student.save()

        call_command("seed_data", stdout=StringIO())

        course.refresh_from_db()
        self.assertEqual(course.capacity, 5)
        self.assertEqual(Course.objects.count(), 14)
        self.assertEqual(Classroom.objects.count(), 13)
        self.assertEqual(Course.objects.get(code="ME3002").allowed_grades, [3, 4])
        student.refresh_from_db()
        self.assertTrue(student.check_password("student123"))
