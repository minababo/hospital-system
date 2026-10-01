# Hospital Management System

A web-based Hospital Management System (HMS) built with Django for managing day-to-day hospital
operations: patients, doctors and departments, appointments, electronic medical records,
laboratory, pharmacy, billing, admissions and staff. Each staff role (Admin, Doctor, Nurse,
Receptionist, Lab Staff, Pharmacist, Accountant) gets its own dashboard, and access is enforced
server-side with role-based access control.

## Live demo

_Coming soon._

## Demo logins

_Coming soon._

## Tech stack

- Python 3.14, Django 6.1
- Server-rendered Django templates + Tailwind CSS (CDN)
- PostgreSQL (Supabase) in production; SQLite for local development
- Hosting: Render (gunicorn + WhiteNoise)
- Config via environment variables (django-environ)
- Tests: pytest + pytest-django; lint/format: ruff; CI: GitHub Actions

## Local setup (Windows, Git Bash)

```bash
# 1. Create and activate a virtual environment
py -3.14 -m venv .venv
source .venv/Scripts/activate

# 2. Install dependencies
python -m pip install -r requirements-dev.txt

# 3. Create your local environment file
cp .env.example .env

# 4. Generate a secret key and paste it into SECRET_KEY in .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# 5. Create the database tables and an admin account
python manage.py migrate
python manage.py createsuperuser

# 6. Start the development server at http://127.0.0.1:8000/
python manage.py runserver
```

## Tests and linting

```bash
python -m pytest                                    # run the test suite
ruff check . && ruff format --check .               # lint and format check
python manage.py makemigrations --check --dry-run   # confirm no missing migrations
```
