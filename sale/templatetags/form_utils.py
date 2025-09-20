# yourapp/templatetags/form_utils.py
from django import template

register = template.Library()

@register.filter
def form_field(form, name):
    """
    Return a BoundField by dynamic name from a Form in templates.
    Usage: {{ form|form_field:"field_name" }} or {{ form|form_field:var_name }}
    """
    try:
        return form[name]
    except Exception:
        return ""
