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

## Appointments

Booking (`/appointments/book/`): find the patient, pick a department/doctor and date, then choose
one of the doctor's free slots and enter the reason for the visit. Free slots come from the
doctor's weekly schedule minus existing bookings; bookings are allowed up to
`APPOINTMENT_BOOKING_WINDOW_DAYS` (default 60) ahead.

| Status | How it gets there | Who |
|--------|-------------------|-----|
| Booked | Appointment is booked (or rescheduled) | Admin, Receptionist |
| Checked in | Patient arrives, on the appointment day | Admin, Receptionist, Nurse |
| Completed | After the consultation | The appointment's own doctor |
| Cancelled | Cancelled with a reason (booked appointments only); frees the slot | Admin, Receptionist |
| No-show | Patient didn't come (after the start time) | Admin, Receptionist |

| Page | Who |
|------|-----|
| Appointment list (per day) and week calendar | Admin, Receptionist, Nurse, Doctor (doctors see only their own) |
| Book, reschedule, cancel, mark no-show | Admin, Receptionist |
| Check in | Admin, Receptionist, Nurse |
| Complete | Doctor (own appointments) |

**Double-booking protection.** The slot is checked when booking, and the database also has
partial unique indexes: a doctor (and a patient) can't have two non-cancelled appointments at the
same date and time. If two people book the same slot at the same moment, the second gets a
friendly "slot was just booked" message instead of an error page. Doctor schedules can't be
changed in a way that leaves upcoming appointments outside working hours.

## Medical records

Workflow for a visit:

1. Receptionist/nurse checks the patient in; a nurse (or the doctor) records **vitals**.
2. The appointment's doctor clicks **Start consultation**. This creates a draft record,
   pre-filled with the reason for the visit.
3. In the draft the doctor writes notes, adds **diagnoses** (exactly one primary, optional
   ICD-10 code), adds **prescription items** and can upload **reports**.
4. **Finalize consultation** locks the record, issues the prescription and completes the
   appointment in one step. An appointment can't be completed without a finalized record.
5. After finalizing, nothing can be edited. Corrections and late results are added as
   **addenda**, and reports can still be attached.

If a medicine's name or generic name appears as a whole word in the patient's allergy notes,
adding it is blocked until the doctor ticks an override. The override is saved on the item.

| Page | Who |
|------|-----|
| Draft consultation (edit) | The appointment's own doctor only |
| Finalized record, treatment history, print summary/prescription | Admin, Doctor, Nurse |
| Record vitals | Nurse, the appointment's doctor |
| Addenda, cancel an issued prescription | The record's own doctor |
| Medicine catalog (`/pharmacy/medicines/`) | Admin, Pharmacist |

**Prescriptions (for the pharmacy module).** One prescription per consultation; each item has
a catalog medicine, dose, frequency (OD, BD, TDS, …), route, duration in days and the quantity to
dispense. Statuses: `DRAFT` (record not finalized) → `ISSUED` (ready to dispense) →
`PARTIALLY_DISPENSED` / `DISPENSED` (set by pharmacy), or `CANCELLED` by the doctor while still
issued.

## Billing

Everything billable is a **charge**: consultation, laboratory, pharmacy, admission or other.
Charges start as *unbilled*; a cashier groups them into an **invoice**, issues it and records
**payments**. Totals (subtotal, total, paid, balance) are always calculated from the charges and
payments, never stored separately.

- Completed consultations are billed automatically at the fee agreed when the appointment was
  booked. They are picked up when an invoice is created.
- Other modules (laboratory, pharmacy, admissions) bill through one function,
  `billing.services.post_charge(..., source_type="lab_order", source_id=12)`. Calling it twice
  for the same source returns the existing charge, so nothing is billed twice.

| Invoice status | Meaning |
|----------------|---------|
| Draft | Being prepared; charges and discount can change |
| Issued | Final; waiting for payment |
| Partially paid / Paid | Updated automatically from payments |
| Void | Cancelled by the admin; its charges become unbilled again |

| Action | Who |
|--------|-----|
| View invoices, patient billing, print invoice and receipts | Admin, Accountant, Receptionist |
| Add charges, create and issue invoices, receive payments | Accountant, Receptionist (cashiers) |
| Edit draft invoices (remove charges, discounts) | Accountant |
| Void an unbilled charge | Accountant, Admin |
| Void invoices and payments | Admin only |

**Separation of duties:** the people who take money can't undo it. Voiding a payment or invoice
needs the admin, and every void records who did it, when and why.

## Laboratory

Workflow:

1. **Request.** The doctor ticks tests in the consultation page's Laboratory section. Walk-in
   patients are requested by reception or lab staff with an external referrer.
2. **Billing at request time.** Each test is posted as a laboratory charge (catalog price at
   that moment). Cancelling an order voids its unpaid charges; if a charge is already on an
   issued invoice, the order can't be cancelled until that invoice is dealt with.
3. **Sample collection** (lab staff) → **result entry** per test (one box per parameter) →
   **release**. Release needs every parameter filled in; released results can't be edited.
4. **Report.** A printable report is available once results are released; lab staff can also
   upload scanned or machine reports (stored as patient documents).

**Flags:** numeric results are compared with the parameter's reference range: **L** (below),
**H** (above) or normal. Values exactly on a limit are normal. The unit and range are copied
onto each result when it's entered, so later catalog changes never alter a released report.

| Action | Who |
|--------|-----|
| Test catalog (tests, parameters, prices) | Admin, Lab Staff |
| Request tests from a consultation | The consultation's doctor |
| Walk-in requests | Receptionist, Lab Staff |
| Worklist and order details | Admin, Doctor, Nurse, Lab Staff |
| Collect sample, enter and release results, upload reports | Lab Staff |
| Cancel an open order | Lab Staff, or the ordering doctor |
| Print released reports | Admin, Doctor, Nurse, Lab Staff, Receptionist |

## Pharmacy

**Stock is kept per batch.** Each delivery is a batch with its own number and expiry date.
Every change to a batch (received, dispensed, adjusted after a stock check, written off when
expired) is recorded as a signed stock movement that is never edited afterwards, so the
movements of a batch always add up to its quantity on hand.

**Dispensing.** Issued prescriptions appear in the pharmacist's queue. The pharmacist enters how
many units of each item to hand over; a prescription can be dispensed in parts and becomes
*Partially dispensed* until everything is given, then *Dispensed*. Stock is taken **first
expiry, first out** (FEFO) from usable batches; expired batches are never used. A dispense is
all-or-nothing: if one item lacks stock, nothing is taken or billed.

**Billing at dispense time.** Each dispensed item is billed as a pharmacy charge at the
medicine's price at that moment, for the quantity actually handed over.

**Alerts** (`/pharmacy/alerts/`): expired batches with stock left, batches expiring within
`PHARMACY_EXPIRY_WARNING_DAYS` (default 90), low stock (at or below the reorder level) and out
of stock.

| Action | Who |
|--------|-----|
| Medicine catalog, inventory, receive / adjust / write off stock, alerts | Admin, Pharmacist |
| Dispensing queue and prescription pages | Pharmacist (Admin can view) |
| Dispense medicines | Pharmacist |

**Layering.** Catalog and stock code (`pharmacy/models.py`, `selectors.py`, `services.py`) doesn't
depend on medical records. Only `pharmacy/dispensing.py` and `dispensing_selectors.py` connect
prescriptions, stock and billing, and prescription statuses are changed through the records
app (`records.services.update_dispensing_status`).

## Admissions (inpatient and outpatient)

- **Outpatients** are the appointments and consultations described above. The Admissions page
  has an "Outpatients today" tab listing today's appointments.
- **Inpatients** are admitted to a bed in a ward, from an outpatient visit ("Admit patient" on the
  appointment), an emergency, a referral or directly. A patient can have only one current
  admission and a bed only one patient; whether a bed is free is worked out from the bed history,
  not stored separately. Nurses and doctors can move a patient to another bed and add progress
  notes; a doctor discharges with a discharge summary, which can be printed.

**Bed charges.** Each bed period is billed per night (counted on Sri Lankan calendar dates) at the
ward's daily rate when the patient entered that bed. The charge is posted when the patient leaves
the bed (transfer or discharge). Every admission is billed at least one day: a stay with no
overnight is charged one day at the last bed's rate.

| Action | Who |
|--------|-----|
| Inpatient list, bed board, admission details | Admin, Doctor, Nurse, Receptionist (progress notes and discharge summaries hidden from reception) |
| Admit a patient | Doctor, Nurse, Receptionist |
| Transfer bed, add progress notes | Doctor, Nurse |
| Discharge | Doctor |
| Print discharge summary | Admin, Doctor, Nurse |
| Wards and beds (set up, rates, activate/deactivate) | Admin |

**Out of scope for this version:** inpatient prescriptions and medication charts, lab orders
raised from an admission (outside a consultation), and interim bills during a long stay.

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
| `APPOINTMENT_BOOKING_WINDOW_DAYS` | How many days ahead appointments can be booked (default 60) |
| `PHARMACY_EXPIRY_WARNING_DAYS` | Days before expiry that a batch counts as "expiring soon" (default 90) |
| `ADMISSION_BACKDATE_DAYS` | How far back admission, transfer and discharge times may be entered (default 7) |

Render sets `RENDER_EXTERNAL_HOSTNAME` and `PORT` itself. The hostname is added to
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` automatically.

Notes:

- The Python version comes from `.python-version`.
- `build.sh` runs `check --deploy`, `collectstatic` and `migrate`, so migrations are applied on
  every deploy.
- To create admin users, run `python manage.py createsuperuser` on your own machine with
  `DATABASE_URL` set to the production database.
