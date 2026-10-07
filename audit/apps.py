from django.apps import AppConfig


class AuditConfig(AppConfig):
    name = "audit"

    def ready(self):
        # Django sends these signals on every login/logout attempt; connecting here logs
        # them without changing the login views.
        from django.contrib.auth.signals import (
            user_logged_in,
            user_logged_out,
            user_login_failed,
        )

        from audit import signals

        user_logged_in.connect(signals.logged_in, dispatch_uid="audit_logged_in")
        user_logged_out.connect(signals.logged_out, dispatch_uid="audit_logged_out")
        user_login_failed.connect(signals.login_failed, dispatch_uid="audit_login_failed")
