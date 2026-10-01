from django.http import HttpResponse
from django.shortcuts import render


def home(request):
    return render(request, "home.html")


def healthz(request):
    # No auth or DB query so Render's health check stays cheap.
    return HttpResponse("ok", content_type="text/plain")
