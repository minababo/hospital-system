"""Redirects that land on a section of a page (e.g. the consultation's #diagnoses card),
so after saving a form the user sees the part they just changed instead of the page top."""

from django.http import HttpResponseRedirect
from django.urls import reverse


def section_url(url_name, section, *args):
    """reverse(url_name, args) + "#section"."""
    return f"{reverse(url_name, args=args)}#{section}"


def redirect_to_section(url_name, section, *args):
    return HttpResponseRedirect(section_url(url_name, section, *args))
