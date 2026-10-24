"""
Role checks. The three roles are plain Django groups (created by a migration).
Superusers (the admin) are allowed everywhere.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

INSPECTOR = "Inspector"
SUPERVISOR = "Supervisor"
CLERK = "Warehouse Clerk"
ALL_ROLES = [INSPECTOR, SUPERVISOR, CLERK]


def has_role(user, role):
    if not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name=role).exists()


def role_required(role):
    """Only let users in `role` open the view. Others get a 403 page."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not has_role(request.user, role):
                raise PermissionDenied(f"This page is only for the {role} role.")
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
