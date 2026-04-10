from rest_framework.permissions import BasePermission, SAFE_METHODS

from .models import User


class ActionRolePermission(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if user.is_superuser:
            return True

        action = getattr(view, "action", None)
        action_role_map = getattr(view, "action_role_map", {})
        allowed_roles = action_role_map.get(action)

        if allowed_roles is None:
            return True

        return self._user_matches(user, allowed_roles)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser:
            return True

        obj_firm_id = getattr(obj, "firm_id", None)
        if obj_firm_id and user.firm_id != obj_firm_id:
            return False

        client = getattr(obj, "client", None)
        if request.method not in SAFE_METHODS and client and client.status == client.Status.CLOSED:
            return user.role == User.Role.ADMIN

        if request.method not in SAFE_METHODS and hasattr(obj, "status") and obj.status == obj.Status.CLOSED:
            return user.role == User.Role.ADMIN

        return True

    def _user_matches(self, user, allowed_roles):
        if "*" in allowed_roles:
            return True
        if "approver" in allowed_roles and user.can_approve:
            return True
        return user.role in allowed_roles
