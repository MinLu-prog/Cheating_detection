from django import template

register = template.Library()

@register.filter
def divide(value, arg):
    try:
        return round((value / arg) * 100)
    except (ZeroDivisionError, TypeError):
        return 0
