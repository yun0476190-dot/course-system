# courses/templatetags/timetable_tags.py
from django import template

register = template.Library()


@register.filter
def get_item(mapping, key):
    """從字典取值，找不到時回傳 None，供模板使用。"""
    if mapping is None:
        return None
    return mapping.get(key)