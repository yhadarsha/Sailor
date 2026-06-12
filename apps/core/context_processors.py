"""
Sailor template context processors.
"""


def sailor_auth_user(request):
    """
    Inject the logged-in user dict into every template context.

    Available in templates as:
        {{ sailor_user.display_name }}
        {{ sailor_user.email }}
        {{ sailor_user.id }}
    """
    return {
        "sailor_user": request.session.get("sailor_user"),
    }
