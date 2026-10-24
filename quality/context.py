"""Makes the user's roles available in every template (used by the menu bar)."""

from .permissions import CLERK, INSPECTOR, SUPERVISOR, has_role


def roles(request):
    user = request.user
    return {
        "is_inspector": has_role(user, INSPECTOR),
        "is_supervisor": has_role(user, SUPERVISOR),
        "is_clerk": has_role(user, CLERK),
    }
