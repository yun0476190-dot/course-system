# courses/services.py
"""選課規則：加選、退選、候補遞補與課程異動共用的檢查。"""
from datetime import time

from .models import (
    DEFAULT_MAX_CREDITS, DEFAULT_MIN_CREDITS, ActivityLog, Enrollment, grades_label,
)


PERIODS = range(1, 15)


def period_start(period):
    """第 n 節的上課時間：第 1 節 08:10，之後每節晚一小時。"""
    return time(7 + period, 10)


def period_end(period):
    """第 n 節的下課時間（每節 50 分鐘）。"""
    return time(8 + period, 0)


def credit_limits(student):
    """回傳學生的（學分下限, 學分上限）；沒有學生資料時用預設值。"""
    profile = getattr(student, "student_profile", None)
    if profile is None:
        return DEFAULT_MIN_CREDITS, DEFAULT_MAX_CREDITS
    return profile.min_credits, profile.max_credits


def credit_summary(student, courses):
    """目前學分與上下限，給頁面顯示提醒用。"""
    low, high = credit_limits(student)
    total = sum(c.credits for c in courses)
    return {
        "total": total,
        "min": low,
        "max": high,
        "below_min": total < low,
        "missing": max(low - total, 0),
    }


def enrolled_courses(student):
    """學生目前已選的課程清單。"""
    return [
        e.course
        for e in Enrollment.objects.filter(student=student).select_related("course")
    ]


def waitlist_positions(student):
    """{課程 id: 順位}：學生登記候補的課程與目前順位。"""
    return {entry.course_id: entry.position for entry in student.waitlist_entries.all()}


def find_conflict(course, others):
    """回傳 others 中與 course 衝堂的第一門課；沒有衝堂回傳 None。"""
    return next((o for o in others if o.pk != course.pk and course.conflicts_with(o)), None)


def eligibility_error(student, course):
    """不符合修課資格（系所、年級）時回傳原因。"""
    profile = getattr(student, "student_profile", None)
    if course.allowed_departments and (
        profile is None or profile.department not in course.allowed_departments
    ):
        return f"本課程限{'、'.join(course.allowed_departments)}學生修習。"
    if course.allowed_grades and (
        profile is None or profile.grade not in course.allowed_grades
    ):
        return f"本課程限{grades_label(course.allowed_grades)}修習。"
    return None


def enroll_block_reason(student, course, current):
    """加選或登記候補前的檢查（不含人數上限）；可以選時回傳 None。

    current 是學生目前已選的課程清單。
    """
    if any(c.pk == course.pk for c in current):
        return "你已選過這門課。"
    reason = eligibility_error(student, course)
    if reason:
        return reason
    clash = find_conflict(course, current)
    if clash:
        return f"與「{clash.name}」衝堂（{clash.time_label}）。"
    _, high = credit_limits(student)
    total = sum(c.credits for c in current) + course.credits
    if total > high:
        return f"加選後學分為 {total}，超過上限 {high} 學分。"
    return None


def drop_block_reason(student, course, current):
    """退選前的檢查：已達學分下限的學生，退選後不能低於下限。

    還沒達到下限的學生可以自由退選，避免排課途中卡住。
    """
    low, _ = credit_limits(student)
    total = sum(c.credits for c in current)
    after = total - course.credits
    if total >= low > after:
        return f"退選後學分為 {after}，會低於下限 {low} 學分。請先加選其他課程再退選。"
    return None


def promote_waitlist(course):
    """課程有空位時，依登記順序把候補學生加入課程，回傳遞補成功的學生。

    衝堂、學分超過上限、資格不符或帳號停用的學生保留順位、直接跳過。
    呼叫端需在交易中執行。
    """
    promoted = []
    enrolled = course.enrollments.count()
    entries = course.waitlist_entries.select_related("student__student_profile").order_by(
        "created_at", "id"
    )
    for entry in entries:
        if enrolled >= course.capacity:
            break
        student = entry.student
        if Enrollment.objects.filter(student=student, course=course).exists():
            entry.delete()
            continue
        if not student.is_active or enroll_block_reason(student, course, enrolled_courses(student)):
            continue
        Enrollment.objects.create(student=student, course=course)
        entry.delete()
        enrolled += 1
        ActivityLog.record(
            ActivityLog.Action.WAITLIST_PROMOTE,
            target=f"{student.username} → {course.code} {course.name}",
            detail="有名額空出，系統依候補順序自動加選",
        )
        promoted.append(student)
    return promoted


def course_change_problems(course_pk, candidate):
    """課程改成 candidate 的設定後，已選學生會遇到的問題：[(學生, 原因), ...]。

    candidate 是帶有新設定、尚未儲存的 Course。
    """
    problems = []
    enrollments = Enrollment.objects.filter(course_id=course_pk).select_related(
        "student__student_profile"
    )
    for enrollment in enrollments:
        student = enrollment.student
        others = [c for c in enrolled_courses(student) if c.pk != course_pk]
        if eligibility_error(student, candidate):
            problems.append((student, "不符合新的修課資格"))
            continue
        clash = find_conflict(candidate, others)
        if clash:
            problems.append((student, f"與「{clash.name}」衝堂"))
            continue
        _, high = credit_limits(student)
        if sum(c.credits for c in others) + candidate.credits > high:
            problems.append((student, f"學分會超過上限 {high}"))
    return problems
