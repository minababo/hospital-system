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

## Roles and access

Staff log in with a username and password; there is no patient login. Every user has exactly one
role: **Admin, Doctor, Nurse, Receptionist, Lab Staff, Pharmacist** or **Accountant**. Each role
has its own dashboard and sidebar. Only Admins can manage user accounts.

Access control is enforced on the server for every request:

- `LoginRequiredMiddleware` sends anyone who is not logged in to the login page. The only public
  pages are the login page and `/healthz/`.
- Each view declares the roles it allows (`RoleRequiredMixin` / `role_required` in
  `accounts/permissions.py`). A logged-in user with any other role gets a 403 page.
- Hiding sidebar links is only cosmetic; it is not what protects a page.

Sessions end after `SESSION_IDLE_TIMEOUT_MINUTES` of inactivity (default 30) and when the browser
closes.

## Deployment (Render + Supabase)

The app runs as a Render web service backed by a Supabase PostgreSQL database.

| Render setting    | Value                                                     |
|-------------------|-----------------------------------------------------------|
| Build command     | `bash build.sh`                                           |
| Start command     | `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT`   |
| Health check path | `/healthz/`                                               |

Environment variables set on Render:

| Name              | Purpose                                                                 |
|-------------------|-------------------------------------------------------------------------|
| `SECRET_KEY`      | Django secret key (long random value, unique to production)             |
| `DEBUG`           | Must be `False`                                                         |
| `DATABASE_URL`    | Supabase session pooler connection string, ending in `?sslmode=require` |
| `WEB_CONCURRENCY` | Number of gunicorn worker processes                                     |
| `LOG_LEVEL`       | Logging level (e.g. `INFO`)                                             |
| `SESSION_IDLE_TIMEOUT_MINUTES` | Minutes of inactivity before a user is logged out (default 30) |

Render sets `RENDER_EXTERNAL_HOSTNAME` and `PORT` itself. The hostname is added to
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` automatically.

Notes:

- The Python version comes from `.python-version`.
- `build.sh` runs `check --deploy`, `collectstatic` and `migrate`, so migrations are applied on
  every deploy.
- To create admin users, run `python manage.py createsuperuser` on your own machine with
  `DATABASE_URL` set to the production database.
