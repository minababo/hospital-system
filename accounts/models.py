from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model, set up before the first migration so fields (e.g. role) can be
    added later without swapping AUTH_USER_MODEL mid-project."""
