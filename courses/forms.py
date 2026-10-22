# courses/forms.py
from django import forms

from .models import Course, User


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = [
            "code", "name", "teacher", "credits", "category", "department",
            "weekday", "start_period", "end_period", "classroom", "capacity",
            "syllabus",
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
            # 教師不需選擇授課教師，系統自動指定為自己
            self.fields.pop("teacher")
        else:
            self.fields["teacher"].queryset = User.objects.filter(role=User.Role.TEACHER)
        for field in self.fields.values():
            field.widget.attrs.setdefault(
                "class",
                "w-full border dark:border-slate-600 dark:bg-slate-700 rounded-xl px-3 py-2",
            )

    def clean(self):
        cleaned = super().clean()
        weekday = cleaned.get("weekday")
        start = cleaned.get("start_period")
        end = cleaned.get("end_period")
        if start and end and end < start:
            raise forms.ValidationError("結束節次不可早於開始節次。")

        teacher = cleaned.get("teacher") or self.user
        if weekday and start and end and teacher:
            clashes = Course.objects.filter(
                teacher=teacher, weekday=weekday,
                start_period__lte=end, end_period__gte=start,
            )
            if self.instance.pk:
                clashes = clashes.exclude(pk=self.instance.pk)
            if clashes.exists():
                raise forms.ValidationError(
                    f"與教師已開的「{clashes.first().name}」時間衝堂。"
                )
        return cleaned