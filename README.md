# Factory Quality Control System

A small Django web app for a factory floor. It makes sure a production lot **cannot be shipped until it has been measured, checked and approved**.

It covers four parts of Zapuza's Factory Operations & Quality Control PRD, built carefully rather than broadly:

| # | Feature | PRD sections |
|---|---------|--------------|
| 1 | **Roles and setup**: login, role permissions, and admin screens for products, machines, jobs and measuring gauges (with calibration expiry) | 2.1, 2.3, 12 |
| 2 | **Inspection**: enter measurements, automatic green/red check, CTQ rule, expired-gauge block, no empty fields, 2 photos when a value fails | 2.3, 4.1, 4.3, 5.1, 5.4 |
| 3 | **Supervisor decision**: Approve, release by Concession, or Quarantine | 6.1 |
| 4 | **Shipping lock**: no gate pass unless the job is approved, with the exact reason shown | 10.2 |

**Video walkthrough:** (https://drive.google.com/file/d/1fVIOp1zpt_caO2wxYX4tYpWlizg81u7D/view?usp=drivesdk)

## Tech stack

Python 3.11+, Django 5.2, SQLite, Pillow (for the photo uploads), Bootstrap 5 (included in the project, so it works offline). No JavaScript framework.

## How to run it

```bash
# 1. create and activate a virtual environment
python -m venv venv
#Windows: venv\Scripts\activate

# 2. install the requirements
pip install -r requirements.txt

# 3. create the database and the demo data
python manage.py migrate
python manage.py seed_demo

# 4. start the site
python manage.py runserver
```

Open http://127.0.0.1:8000 and log in.

### Demo accounts (created by `seed_demo`, for demo only)

| Role | Username | Password |
|------|----------|----------|
| Inspector | `inspector1`, `inspector2` | `demo1234` |
| Supervisor | `supervisor1`, `supervisor2` | `demo1234` |
| Warehouse Clerk | `clerk1` | `demo1234` |
| Admin | `admin` | `admin1234` |

### Run the tests

```bash
python manage.py test
```

## Try it in five minutes

1. Log in as **inspector1**, open *Jobs*, and start an inspection on **JOB-1001** (drive shaft).
2. Type `25.2` in *Shaft diameter*. The box turns **red** as you type (allowed range is 24.980 to 25.050).
3. Pick the gauge **PG-04 (EXPIRED)** and save. You get *Expired Tool Calibration* and nothing is saved.
4. Pick **VC-104**, attach two photos and save. The value is saved as a **FAIL**. Because *Shaft diameter* is **CTQ**, the checklist **freezes** and goes to the supervisor.
5. Log in as **supervisor1**, open *Review queue*. Approve and Concession are greyed out. Only **Quarantine** is allowed, so quarantine it with a short note.
6. Log in as **clerk1**, open *Shipping* and click *Create gate pass* for JOB-1001. It is **blocked** and says the job is quarantined.
7. Start an inspection on **JOB-1002** with good values (for example 25.01, 150.0, 0.5), submit it, approve it as supervisor1, then create its gate pass as clerk1. This time it works.

## The rules (where each one lives)

All rules are in `quality/models.py`, so the pages cannot bypass them.

- **Limits:** allowed range = nominal + lower tolerance up to nominal + upper tolerance. A value exactly *on* a limit passes.
- **Gauge calibration:** a gauge is valid *on* its expiry date and expired the day after. An expired gauge blocks the entry (`Inspection.record_measurement`).
- **Failed value:** needs at least 2 photos before it can be saved.
- **No skipping:** an inspection cannot be submitted while any field is empty (`Inspection.submit`).
- **CTQ failure:** the checklist freezes, the inspection goes straight to the supervisor, and a concession is not allowed.
- **Decisions are final.** Approve needs every value to pass. Concession needs a reason code and a written justification (10+ characters), and only works for non-CTQ failures. Quarantine needs a written reason.
- **Four-eyes rule:** nobody can review their own inspection.
- **Shipping lock:** a gate pass needs the latest inspection to be Approved or released by Concession. A job that was ever quarantined can never ship. One gate pass per job (`Job.shipping_block_reason`).

## Decisions I made where the PRD is not specific

- **Concession counts as a release.** The PRD says the final inspection must be "Approved by Quality" but also calls a concession a "release", so both can ship.
- **Quarantine just locks the lot.** The PRD sends quarantined lots to a Material Review Board (rework, scrap, return to vendor). That is out of scope here, so a quarantined job stays locked.
- **A frozen CTQ checklist is submitted automatically** so the supervisor can act on it, even though some fields are still empty.
- **One gauge per measurement**, chosen from a list instead of scanning a QR code.

## Not included (on purpose)

QR code scanning, offline mode, blueprint pins, email/SMS alerts, raw material intake, traceability, hold timers, first-piece checks and customer QR labels. The test asked for quality over quantity, so these were left out.

## Project layout

```
config/                  project settings and top-level URLs
quality/
  models.py              the data and ALL the business rules
  views.py               thin pages: check role, call a model method, show the result
  forms.py               input checking for the measurement and decision forms
  permissions.py         role checks (Inspector, Supervisor, Warehouse Clerk)
  admin.py               admin screens for setup data (results are read-only there)
  tests.py               60 tests covering every rule
  templates/quality/     the pages
  static/quality/        small script for live green/red + bundled Bootstrap
  management/commands/   seed_demo (demo data)
```
