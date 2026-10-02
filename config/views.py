from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.shortcuts import redirect


def home(request):
    # Anonymous users never get here: LoginRequiredMiddleware sends them to login.
    return redirect("dashboard")


@login_not_required
def healthz(request):
    # No auth or DB query so Render's health check stays cheap.
    return HttpResponse("ok", content_type="text/plain")
