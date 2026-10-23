# courses/imports.py
"""批次匯入：讀取 CSV，逐列用既有表單驗證，全部正確才寫入。"""
import csv
import io

from django.contrib.auth.hashers import make_password
from django.db import transaction

from .forms import CourseForm, StudentCreateForm, TeacherCreateForm
from .models import DEFAULT_MAX_CREDITS, DEFAULT_MIN_CREDITS, Classroom, Course, User


class ImportFileError(Exception):
    """整個檔案無法處理（編碼、標題列、筆數）時使用。"""


# 每種匯入的欄位：(表單欄位, 中文標題, 是否必填)
IMPORT_KINDS = {
    "students": {
        "label": "學生",
        "max_rows": 100,
        "columns": [
            ("username", "帳號", True),
            ("password", "初始密碼", True),
            ("display_name", "姓名", True),
            ("student_no", "學號", True),
            ("department", "系所", True),
            ("grade", "年級", False),
            ("min_credits", "學分下限", False),
            ("max_credits", "學分上限", False),
            ("phone", "聯絡電話", False),
            ("email_contact", "聯絡 Email", False),
        ],
        "example": [
            ["s1130001", "Welcome-2026", "王小明", "S1130001", "資訊管理系", "1", "16", "25",
             "0912345678", "s1130001@example.com"],
            ["s1130002", "Welcome-2026", "李小華", "S1130002", "企業管理系", "2", "", "", "", ""],
        ],
    },
    "teachers": {
        "label": "教師",
        "max_rows": 100,
        "columns": [
            ("username", "帳號", True),
            ("password", "初始密碼", True),
            ("display_name", "姓名", True),
            ("teacher_no", "教師編號", True),
            ("department", "系所", True),
        ],
        "example": [["t1130001", "Welcome-2026", "陳教授", "T1130001", "資訊管理系"]],
    },
    "courses": {
        "label": "課程",
        "max_rows": 300,
        "columns": [
            ("code", "課程代碼", True),
            ("name", "課程名稱", True),
            ("teacher", "授課教師帳號", True),
            ("credits", "學分數", True),
            ("category", "課程類別", True),
            ("department", "開課系所", False),
            ("weekday", "星期", True),
            ("start_period", "開始節次", True),
            ("end_period", "結束節次", True),
            ("classroom", "教室", True),
            ("capacity", "修課人數上限", True),
            ("allowed_departments", "限修系所", False),
            ("allowed_grades", "限修年級", False),
            ("syllabus", "課程大綱", False),
        ],
        "example": [
            ["IM3101", "資料庫系統", "teacher1", "3", "專業選修", "資訊管理系", "週三", "2", "4",
             "B401", "40", "資訊管理系", "3、4", ""],
        ],
    },
}

CATEGORY_LOOKUP = {}
for value, label in Course.Category.choices:
    CATEGORY_LOOKUP[value] = value
    CATEGORY_LOOKUP[label] = value

WEEKDAY_LOOKUP = {}
for value, label in Course.Weekday.choices:  # label 例如「週一」
    for key in (str(value), label, label[-1], f"星期{label[-1]}"):
        WEEKDAY_LOOKUP[key] = str(value)

GRADE_LOOKUP = {"一": "1", "二": "2", "三": "3", "四": "4"}


def template_rows(kind):
    """範例檔內容：標題列加上範例資料。"""
    spec = IMPORT_KINDS[kind]
    return [label for _, label, _ in spec["columns"]], spec["example"]


def _split_list(text):
    """「資訊管理系、企業管理系」或「3,4」→ 清單。"""
    for sep in ("、", "，", ",", "/", "；", ";"):
        text = text.replace(sep, " ")
    return [part for part in text.split() if part]


def read_rows(uploaded_file, kind):
    """讀取 CSV，回傳 [(列號, {欄位: 值}), ...]。"""
    spec = IMPORT_KINDS[kind]
    raw = uploaded_file.read()
    # Excel 存的 CSV 可能是 UTF-8（含 BOM）或 Big5
    for encoding in ("utf-8-sig", "cp950"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportFileError("無法辨識檔案編碼，請用 Excel「另存新檔」選擇「CSV UTF-8」格式。")

    rows = [
        (number, row)
        for number, row in enumerate(csv.reader(io.StringIO(text)), start=1)
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        raise ImportFileError("檔案是空的。")

    lookup = {}
    for key, label, _ in spec["columns"]:
        lookup[key.lower()] = key
        lookup[label.lower()] = key
    _, header = rows[0]
    keys = [lookup.get(cell.strip().lower()) for cell in header]
    missing = [label for key, label, required in spec["columns"] if required and key not in keys]
    if missing:
        raise ImportFileError(f"缺少欄位：{'、'.join(missing)}。請使用範例檔的標題列。")

    data_rows = rows[1:]
    if not data_rows:
        raise ImportFileError("檔案只有標題列，沒有資料。")
    if len(data_rows) > spec["max_rows"]:
        raise ImportFileError(
            f"一次最多匯入 {spec['max_rows']} 筆，這個檔案有 {len(data_rows)} 筆，請分批匯入。"
        )
    result = []
    for number, row in data_rows:
        values = {key: cell.strip() for key, cell in zip(keys, row) if key}
        result.append((number, values))
    return result


def _errors_text(form):
    """把表單錯誤整理成一行文字，例如「學號：此學號已存在。」。"""
    parts = []
    for field, errors in form.errors.items():
        text = "；".join(errors)
        label = form.fields[field].label if field in form.fields else ""
        parts.append(f"{label}：{text}" if label else text)
    return " / ".join(parts)


def _account_form(kind, values):
    if kind == "students":
        data = dict(values)
        data["grade"] = data.get("grade") or "1"
        data["min_credits"] = data.get("min_credits") or str(DEFAULT_MIN_CREDITS)
        data["max_credits"] = data.get("max_credits") or str(DEFAULT_MAX_CREDITS)
        return StudentCreateForm(data)
    return TeacherCreateForm(values)


def _import_accounts(kind, rows):
    """先驗證全部資料列（包含檔案內重複），沒有錯誤才寫入。"""
    number_field = "student_no" if kind == "students" else "teacher_no"
    errors, forms = [], []
    seen_usernames, seen_numbers = set(), set()
    for number, values in rows:
        form = _account_form(kind, values)
        if not form.is_valid():
            errors.append((number, _errors_text(form)))
            continue
        username = form.cleaned_data["username"].lower()
        account_no = form.cleaned_data[number_field]
        if username in seen_usernames:
            errors.append((number, f"帳號「{form.cleaned_data['username']}」在檔案中重複。"))
            continue
        if account_no in seen_numbers:
            errors.append((number, f"編號「{account_no}」在檔案中重複。"))
            continue
        seen_usernames.add(username)
        seen_numbers.add(account_no)
        forms.append(form)
    if errors:
        return 0, errors

    hashes = {}  # 相同的初始密碼只加密一次，加快大量匯入
    with transaction.atomic():
        for form in forms:
            password = form.cleaned_data["password"]
            if password not in hashes:
                hashes[password] = make_password(password)
            form.save(password_hash=hashes[password])
    return len(forms), []


def _course_form(values, admin_user):
    """把 CSV 的文字（教師帳號、教室名稱、中文類別與星期）換成表單需要的值。"""
    data = dict(values)
    username = values.get("teacher", "")
    teacher = User.objects.filter(username__iexact=username, role=User.Role.TEACHER).first()
    if teacher is None:
        return None, f"找不到教師帳號「{username}」。"
    room_name = values.get("classroom", "")
    room = Classroom.objects.filter(name=room_name).first()
    if room is None:
        return None, f"找不到教室「{room_name}」，請先到「教室」頁面新增。"
    data["teacher"] = str(teacher.pk)
    data["classroom"] = str(room.pk)
    data["category"] = CATEGORY_LOOKUP.get(values.get("category", ""), values.get("category", ""))
    data["weekday"] = WEEKDAY_LOOKUP.get(values.get("weekday", ""), values.get("weekday", ""))
    data["allowed_departments"] = _split_list(values.get("allowed_departments", ""))
    data["allowed_grades"] = [
        GRADE_LOOKUP.get(g.removesuffix("年級"), g.removesuffix("年級"))
        for g in _split_list(values.get("allowed_grades", ""))
    ]
    return CourseForm(data, user=admin_user), None


def _import_courses(rows, admin_user):
    """逐列驗證並寫入（後面的列會和前面已匯入的課程比對衝堂），有錯就全部取消。"""
    errors, created = [], 0
    with transaction.atomic():
        for number, values in rows:
            form, error = _course_form(values, admin_user)
            if error:
                errors.append((number, error))
            elif form.is_valid():
                form.save()
                created += 1
            else:
                errors.append((number, _errors_text(form)))
        if errors:
            transaction.set_rollback(True)
            created = 0
    return created, errors


def run_import(kind, uploaded_file, admin_user):
    """匯入 CSV，回傳（成功筆數, [(列號, 錯誤說明), ...]）；有錯誤時不會寫入任何資料。"""
    rows = read_rows(uploaded_file, kind)
    if kind == "courses":
        return _import_courses(rows, admin_user)
    return _import_accounts(kind, rows)
