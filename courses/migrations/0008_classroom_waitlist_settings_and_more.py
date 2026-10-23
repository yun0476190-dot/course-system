# 新增教室、候補、系統設定、修課資格與學分下限。
# 課程原本的教室是文字欄位，這裡把它轉成教室資料並改成外鍵。

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def fix_min_credits(apps, schema_editor):
    """既有學生的學分上限若低於預設下限，下限改成和上限相同。"""
    StudentProfile = apps.get_model("courses", "StudentProfile")
    StudentProfile.objects.filter(min_credits__gt=models.F("max_credits")).update(
        min_credits=models.F("max_credits")
    )


def classrooms_forward(apps, schema_editor):
    """依課程的教室文字建立教室，容納人數取該教室課程的最大人數上限。"""
    Course = apps.get_model("courses", "Course")
    Classroom = apps.get_model("courses", "Classroom")
    for course in Course.objects.all():
        name = course.classroom.strip() or "未指定教室"
        room, created = Classroom.objects.get_or_create(
            name=name, defaults={"capacity": max(course.capacity, 1)}
        )
        if not created and room.capacity < course.capacity:
            room.capacity = course.capacity
            room.save(update_fields=["capacity"])
        course.classroom_ref = room
        course.save(update_fields=["classroom_ref"])


def classrooms_backward(apps, schema_editor):
    Course = apps.get_model("courses", "Course")
    for course in Course.objects.select_related("classroom_ref"):
        course.classroom = course.classroom_ref.name if course.classroom_ref_id else ""
        course.save(update_fields=["classroom"])


class Migration(migrations.Migration):

    dependencies = [
        ("courses", "0007_alter_user_managers_alter_activitylog_action_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 學生：年級與學分下限
        migrations.AddField(
            model_name="studentprofile",
            name="grade",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "一年級"), (2, "二年級"), (3, "三年級"), (4, "四年級")],
                default=1,
                verbose_name="年級",
            ),
        ),
        migrations.AddField(
            model_name="studentprofile",
            name="min_credits",
            field=models.PositiveSmallIntegerField(
                default=16,
                validators=[django.core.validators.MaxValueValidator(40)],
                verbose_name="學期學分下限",
            ),
        ),
        migrations.RunPython(fix_min_credits, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="studentprofile",
            constraint=models.CheckConstraint(
                condition=models.Q(("min_credits__lte", models.F("max_credits"))),
                name="min_credits_lte_max_credits",
                violation_error_message="學分下限不可高於學分上限。",
            ),
        ),
        # 教室：建立教室資料，再把課程的教室文字換成外鍵
        migrations.CreateModel(
            name="Classroom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=50, unique=True, verbose_name="教室")),
                ("capacity", models.PositiveSmallIntegerField(
                    validators=[django.core.validators.MinValueValidator(1)],
                    verbose_name="容納人數",
                )),
            ],
            options={
                "verbose_name": "教室",
                "verbose_name_plural": "教室",
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="course",
            name="classroom_ref",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="courses.classroom",
            ),
        ),
        # 先給舊欄位預設值，讓這個 migration 可以倒回去
        migrations.AlterField(
            model_name="course",
            name="classroom",
            field=models.CharField(default="", max_length=50, verbose_name="教室"),
        ),
        migrations.RunPython(classrooms_forward, classrooms_backward),
        migrations.RemoveField(model_name="course", name="classroom"),
        migrations.RenameField(model_name="course", old_name="classroom_ref", new_name="classroom"),
        migrations.AlterField(
            model_name="course",
            name="classroom",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="courses",
                to="courses.classroom",
                verbose_name="教室",
            ),
        ),
        # 修課資格
        migrations.AddField(
            model_name="course",
            name="allowed_departments",
            field=models.JSONField(blank=True, default=list, verbose_name="限修系所"),
        ),
        migrations.AddField(
            model_name="course",
            name="allowed_grades",
            field=models.JSONField(blank=True, default=list, verbose_name="限修年級"),
        ),
        # 候補
        migrations.CreateModel(
            name="WaitlistEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="登記時間")),
                ("course", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="waitlist_entries",
                    to="courses.course",
                    verbose_name="課程",
                )),
                ("student", models.ForeignKey(
                    limit_choices_to={"role": "student"},
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="waitlist_entries",
                    to=settings.AUTH_USER_MODEL,
                    verbose_name="學生",
                )),
            ],
            options={
                "verbose_name": "候補登記",
                "verbose_name_plural": "候補登記",
                "ordering": ["created_at", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("student", "course"), name="unique_waitlist_student_course"
                    ),
                ],
            },
        ),
        # 系統設定（加退選期間、學期起訖日）
        migrations.CreateModel(
            name="SystemSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("enrollment_start", models.DateTimeField(blank=True, null=True, verbose_name="加退選開始時間")),
                ("enrollment_end", models.DateTimeField(blank=True, null=True, verbose_name="加退選結束時間")),
                ("semester_start", models.DateField(blank=True, null=True, verbose_name="學期第一天")),
                ("semester_end", models.DateField(blank=True, null=True, verbose_name="學期最後一天")),
            ],
            options={
                "verbose_name": "系統設定",
                "verbose_name_plural": "系統設定",
            },
        ),
        migrations.AlterField(
            model_name="activitylog",
            name="action",
            field=models.CharField(
                choices=[
                    ("login", "登入"), ("enroll", "加選"), ("drop", "退選"),
                    ("course_create", "開課"), ("course_update", "編輯課程"),
                    ("course_delete", "刪除課程"), ("force_drop", "強制退選"),
                    ("credit_update", "調整學分上下限"), ("student_create", "新增學生"),
                    ("student_update", "編輯學生資料"), ("account_disable", "停用帳號"),
                    ("account_enable", "啟用帳號"), ("teacher_create", "新增教師"),
                    ("teacher_update", "編輯教師資料"), ("password_change", "修改密碼"),
                    ("password_reset", "重設密碼"), ("waitlist_join", "登記候補"),
                    ("waitlist_leave", "取消候補"), ("waitlist_promote", "候補遞補"),
                    ("classroom_create", "新增教室"), ("classroom_update", "編輯教室"),
                    ("classroom_delete", "刪除教室"), ("settings_update", "修改系統設定"),
                    ("bulk_import", "批次匯入"),
                ],
                max_length=20,
                verbose_name="操作類型",
            ),
        ),
    ]
