import json
from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """{{ my_dict|get_item:key }} — safely gets a dict value by dynamic key."""
    if dictionary is None:
        return ""
    return dictionary.get(key, "")


@register.filter
def parse_json(value):
    """{{ json_string|parse_json }} — parse a JSON string to a Python object."""
    try:
        return json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
