# courses/management/commands/seed_data.py
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from courses.models import Course, StudentProfile, TeacherProfile

User = get_user_model()

COURSES = [
    # code, name, teacher_idx, credits, category, dept, weekday, start, end, room, capacity
    ("GE1001", "大學國文", 0, 2, "ge_required", "通識中心", 1, 1, 2, "E101", 80),
    ("GE1002", "大一英文", 1, 2, "ge_required", "通識中心", 3, 3, 4, "E102", 60),
    ("GE1003", "體育", 1, 1, "ge_required", "體育室", 5, 7, 8, "運動場", 50),
    ("MR2001", "計算機概論", 0, 3, "major_required", "資訊管理系", 2, 1, 3, "B201", 60),
    ("MR2002", "程式設計", 0, 3, "major_required", "資訊管理系", 4, 2, 4, "B305", 50),
    ("MR2003", "資料結構", 1, 3, "major_required", "資訊管理系", 1, 5, 7, "B305", 50),
    ("ME3001", "網頁應用開發", 0, 3, "major_elective", "資訊管理系", 3, 6, 8, "B401", 40),
    ("ME3002", "機器學習", 1, 3, "major_elective", "資訊管理系", 2, 8, 10, "B402", 40),
    ("ME3003", "Linux系統", 1, 2, "major_elective", "資訊管理系", 4, 6, 7, "B403", 35),
    ("GI4001", "藝術欣賞", 0, 2, "ge_elective", "通識中心", 5, 2, 3, "A101", 90),
    ("GI4002", "社會學導論", 1, 2, "ge_elective", "通識中心", 2, 6, 7, "A102", 90),
    ("GI4003", "生態與環境", 0, 2, "ge_elective", "通識中心", 4, 9, 10, "A201", 80),
    ("GI4004", "國際關係概論", 1, 2, "ge_elective", "通識中心", 1, 9, 10, "A202", 80),
    ("GI4005", "天文學入門", 0, 2, "ge_elective", "通識中心", 3, 9, 10, "A203", 70),
]


class Command(BaseCommand):
    help = "灌入假資料：管理員、學生、教師與課程"

    @transaction.atomic
    def handle(self, *args, **options):
        # 管理員
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "role": User.Role.ADMIN,
                "is_staff": True,
                "is_superuser": True,
                "display_name": "教務處管理員",
            },
        )
        admin.set_password("admin123")
        admin.save()

        # 教師
        teachers = []
        for i, (username, name, dept) in enumerate([
            ("teacher1", "王教授", "資訊管理系"),
            ("teacher2", "李教授", "資訊管理系"),
        ], start=1):
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"role": User.Role.TEACHER, "display_name": name},
            )
            user.set_password("teacher123")
            user.save()
            TeacherProfile.objects.get_or_create(
                user=user,
                defaults={"teacher_no": f"T{i:04d}", "department": dept},
            )
            teachers.append(user)

        # 學生
        for i, (username, name) in enumerate([
            ("student1", "陳同學"),
            ("student2", "林同學"),
        ], start=1):
            user, _ = User.objects.get_or_create(
                username=username,
                defaults={"role": User.Role.STUDENT, "display_name": name},
            )
            user.set_password("student123")
            user.save()
            StudentProfile.objects.get_or_create(
                user=user,
                defaults={
                    "student_no": f"S{i:08d}",
                    "department": "資訊管理系",
                },
            )

        # 課程
        for (code, name, t_idx, credits, cat, dept, wd, sp, ep, room, cap) in COURSES:
            Course.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "teacher": teachers[t_idx],
                    "credits": credits,
                    "category": cat,
                    "department": dept,
                    "weekday": wd,
                    "start_period": sp,
                    "end_period": ep,
                    "classroom": room,
                    "capacity": cap,
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f"假資料建立完成：管理員 1、教師 {len(teachers)}、學生 2、課程 {len(COURSES)}"
        ))
        self.stdout.write("帳號密碼：admin/admin123、teacher1/teacher123、teacher2/teacher123、student1/student123、student2/student123")