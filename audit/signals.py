from audit.models import Action
from audit.services import log_action


def logged_in(sender, request, user, **kwargs):
    log_action(actor=user, action=Action.LOGIN, event="accounts.user.logged_in", obj=user)


def logged_out(sender, request, user, **kwargs):
    if user is not None:
        log_action(actor=user, action=Action.LOGOUT, event="accounts.user.logged_out", obj=user)


def login_failed(sender, credentials, request=None, **kwargs):
    # Only the username is kept; the password (masked by Django anyway) never is.
    username = str(credentials.get("username", ""))[:100]
    log_action(
        actor=None,
        action=Action.LOGIN_FAILED,
        event="accounts.user.login_failed",
        message=f"Failed login for '{username}'",
    )
