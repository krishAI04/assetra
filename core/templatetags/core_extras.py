from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def initials(value):
    parts = [part for part in str(value).split() if part]
    return "".join(part[0].upper() for part in parts[:2]) or "A"


@register.filter
def money(value):
    if value in (None, ""):
        return "$0.00"
    return f"${Decimal(value):,.2f}"


@register.filter
def status_class(value):
    mapping = {
        "active": "badge-success",
        "deceased": "badge-danger",
        "estate_processing": "badge-warning",
        "closed": "badge-neutral",
        "pending": "badge-warning",
        "approved": "badge-success",
        "rejected": "badge-danger",
        "admin": "badge-primary",
        "lawyer": "badge-info",
        "intern": "badge-neutral",
    }
    return mapping.get(str(value), "badge-neutral")


@register.filter
def yes_no_label(value):
    return "Yes" if value else "No"
