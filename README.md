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

## Modules

| Module | URL | Who can use it |
|--------|-----|----------------|
| Users | `/accounts/users/` | Admin |
| Patients | `/patients/` | View, search, upload documents: Admin, Receptionist, Doctor, Nurse. Register/edit: Admin, Receptionist. Medical history: Admin, Doctor, Nurse. Delete documents: Admin |
| Departments | `/doctors/departments/` | Admin (create, edit, activate/deactivate) |
| Doctors | `/doctors/` | View: Admin, Receptionist, Nurse, Doctor. Add/edit doctors and schedules: Admin |
| My profile | `/doctors/me/` | Doctor (own profile and weekly schedule) |

Doctors are added under **Doctors**, not **Users**: adding a doctor creates their login account
and doctor profile together. A doctor's weekly schedule is made of blocks (e.g. Monday 09:00–12:00
in 15-minute slots) that cannot overlap.

Patients get a medical record number (MRN) such as `P000123`. Search accepts a name
("kamal perera"), MRN, NIC or phone number in any common format.

## File storage

Patient documents (PDF, JPG, PNG up to `MAX_UPLOAD_MB`) are stored in a **private** Supabase
Storage bucket through its S3-compatible API.

- Files are never linked directly. They are downloaded through `/patients/<id>/documents/<doc>/`,
  which checks the user's role first and streams the file.
- Each file's type is checked from its actual bytes, not its name. Files are stored under a
  random name (`patients/<patient id>/<random>.pdf`); the original name is only kept in the
  database.
- Configuration: `SUPABASE_S3_ENDPOINT`, `SUPABASE_S3_REGION`, `SUPABASE_S3_BUCKET`,
  `SUPABASE_S3_ACCESS_KEY_ID`, `SUPABASE_S3_SECRET_ACCESS_KEY` (all five, or none).
- With none set locally, files are saved to `./media` instead.
- On Render (`RENDER=true`), the app refuses to start without Supabase Storage, because Render's
  disk is wiped on every deploy.

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
| `SUPABASE_S3_*` (5 variables) | Supabase Storage connection for uploaded documents (see File storage) |
| `MAX_UPLOAD_MB` | Largest upload allowed in MB (default 5) |

Render sets `RENDER_EXTERNAL_HOSTNAME` and `PORT` itself. The hostname is added to
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` automatically.

Notes:

- The Python version comes from `.python-version`.
- `build.sh` runs `check --deploy`, `collectstatic` and `migrate`, so migrations are applied on
  every deploy.
- To create admin users, run `python manage.py createsuperuser` on your own machine with
  `DATABASE_URL` set to the production database.
