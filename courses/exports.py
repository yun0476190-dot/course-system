# courses/exports.py
"""匯出：CSV 下載與課表行事曆（.ics，可匯入 Google 日曆）。"""
import csv
import io
from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.http import HttpResponse
from django.utils import timezone
from django.utils.http import content_disposition_header

from .services import period_end, period_start


def _safe_cell(value):
    """以 = + - @ 開頭的文字在 Excel 會被當成公式執行，前面加上單引號讓它保持純文字。"""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def csv_response(filename, header, rows):
    """產生 CSV 下載；加上 BOM，Excel 開啟時中文才不會變亂碼。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows([_safe_cell(cell) for cell in row] for row in rows)
    response = HttpResponse("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(True, filename)
    return response


def _escape(text):
    """iCalendar 文字欄位需要跳脫反斜線、逗號、分號與換行。"""
    return (
        str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
        .replace("\r\n", "\\n").replace("\n", "\\n")
    )


def _fold(line):
    """iCalendar 每行最多 75 bytes，超過要折行（續行以空白開頭），且不能切斷中文字。"""
    parts, current, size = [], "", 0
    for char in line:
        char_size = len(char.encode("utf-8"))
        if size + char_size > 75:
            parts.append(current)
            current, size = " ", 1
        current += char
        size += char_size
    parts.append(current)
    return "\r\n".join(parts)


def timetable_ics(student, courses, semester_start, semester_end):
    """把課表轉成學期內每週重複的行事曆事件。"""
    stamp = datetime.now(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # 重複規則的結束時間要用 UTC：學期最後一天 23:59:59（台北時間）
    until_local = timezone.make_aware(datetime.combine(semester_end, time(23, 59, 59)))
    until = until_local.astimezone(dt_timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Course System//Timetable//ZH-TW",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:我的課表",
        "X-WR-TIMEZONE:Asia/Taipei",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Taipei",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for course in courses:
        # 學期開始後第一次上這門課的日期（weekday 1 = 週一）
        first_day = semester_start + timedelta(days=(course.weekday - 1 - semester_start.weekday()) % 7)
        if first_day > semester_end:
            continue
        start = datetime.combine(first_day, period_start(course.start_period))
        end = datetime.combine(first_day, period_end(course.end_period))
        teacher = course.teacher.display_name or course.teacher.username
        description = f"{course.code}｜{teacher}｜{course.credits} 學分"
        lines += [
            "BEGIN:VEVENT",
            f"UID:course-{course.pk}-student-{student.pk}@course-system",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Asia/Taipei:{start:%Y%m%dT%H%M%S}",
            f"DTEND;TZID=Asia/Taipei:{end:%Y%m%dT%H%M%S}",
            f"RRULE:FREQ=WEEKLY;UNTIL={until}",
            f"SUMMARY:{_escape(course.name)}",
            f"LOCATION:{_escape(course.classroom.name)}",
            f"DESCRIPTION:{_escape(description)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def ics_response(content, filename="my-timetable.ics"):
    response = HttpResponse(content, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = content_disposition_header(True, filename)
    return response
