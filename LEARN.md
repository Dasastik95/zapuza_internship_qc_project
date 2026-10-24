# Learn this project (read this before you record your video)

This guide explains how the project works in plain language, so you can explain it in your own words. Read the code in the order below. It takes about two hours.

## 1. How a request travels through Django

```
Browser  ->  config/urls.py  ->  quality/urls.py  ->  a view in views.py
                                                         |-- checks your role (permissions.py)
                                                         |-- checks the form (forms.py)
                                                         |-- calls a MODEL method (models.py)  <- rules live here
                                                         `-- shows a template (templates/)
```

The key idea: **views are thin, models hold the rules.** If a view forgets a check, the model still refuses. That is why the tests talk to the models directly.

## 2. Read `quality/models.py` (the most important file)

Read it top to bottom:

1. **`RuleError`** is our own error for "that is not allowed". Its message is written for users, and views show it in a red box.
2. **Setup models** (`Machine`, `Product`, `Gauge`, `InspectionField`). An admin fills these in once.
   - `InspectionField` stores `nominal`, `upper_tol` and `lower_tol`. The allowed range is `nominal + lower_tol` to `nominal + upper_tol`.
   - We use `DecimalField`, not floats. Floats are slightly inexact (`0.1 + 0.2` is not exactly `0.3`), which would break "exactly on the limit passes".
3. **`Job`** is a production lot. `shipping_block_reason()` returns the reason it cannot ship, or `None`.
4. **`Inspection`** is the heart of the app. Read these in order:
   - `start()` : opens an inspection (refuses quarantined jobs and double inspections)
   - `record_measurement()` : the gauge check, the limit check, the 2-photo rule, then the CTQ freeze
   - `submit()` : refuses empty fields
   - `approve()`, `concede()`, `quarantine()` : the supervisor's three decisions
5. **`Measurement`, `DefectPhoto`, `GatePass`** just store results.

### Status flow of an inspection

```
IN_PROGRESS --submit()--------------> PENDING_REVIEW --approve()-----> APPROVED
     |                                      ^         --concede()----> CONCESSION
     `--CTQ value fails (automatic)---------'         --quarantine()-> QUARANTINED
```

After a decision there is no way back. That is deliberate.

## 3. Then read `views.py`, `forms.py`, `permissions.py`

- `role_required("Inspector")` is a decorator. It wraps a view and returns a 403 page if the user is not in that group. Superusers pass everywhere.
- Every view that changes data uses `@require_POST` (GET requests must never change anything) and Django's CSRF token in the form.
- Look at the pattern in `add_measurement`: validate the form, call the model inside `try`, and on `RuleError` show the message with `messages.error`. Then redirect (the "post/redirect/get" pattern, so refreshing does not resubmit).

## 4. Templates and the small script

- `base.html` holds the menu and the message boxes. The other templates `extend` it.
- `inspection_detail.html` shows one card per field: either the saved result or a form.
- `static/quality/measure.js` only gives live green/red feedback. **The server checks again**, because browser code can be bypassed. Be ready to say this in the video.

## 5. Read `tests.py`

Run `python manage.py test`. The tests are grouped like the features. Pick two and read them closely, for example `test_value_exactly_on_upper_limit_passes` and `test_quarantine_blocks_shipping_even_if_an_older_inspection_was_approved`. Each one sets up a situation and checks that the rule holds.

## 6. Questions you may be asked (with short answers)

1. **Why are the rules in the model and not the view?** One place, always enforced, easy to test.
2. **Why DecimalField?** Floats are inexact, and limits like 25.050 must compare exactly.
3. **What happens when a gauge expires today?** It is still valid today and expires tomorrow. That is a decision I wrote in the README.
4. **How do roles work?** Django Groups. A decorator checks the group, and superusers bypass it.
5. **What stops a supervisor approving a failed inspection by sending a hand-made request?** `approve()` checks `has_failure()` in the model and raises `RuleError`.
6. **Why can't a concession be given for a CTQ failure?** The PRD (section 4.3) says CTQ failures prevent concessions. `concede()` enforces it.
7. **What is `unique_together` / `UniqueConstraint` for?** It guarantees one measurement per field per inspection at the database level.
8. **What is a migration?** A file that describes a change to the database tables. `makemigrations` writes it, `migrate` applies it. `0002_create_roles` creates the three groups.
9. **Why redirect after a POST?** So pressing refresh does not submit the form twice.
10. **Why is the Inspection read-only in the admin?** So nobody can change a result and bypass the rules.
11. **What would you do next?** Material Review Board dispositions, email alerts for CTQ failures, QR scanning, and the other two shipping checks (operations signed off, hold times).
12. **What is not covered?** See "Not included" in the README.

## 7. A 5-minute video plan

1. (30s) What the app does and the four features.
2. (60s) Show the models and explain "rules live in the model".
3. (2 min) Live demo: red value, expired gauge, CTQ freeze, supervisor options, blocked shipping, then a successful approval and gate pass.
4. (60s) Show two tests and run `python manage.py test`.
5. (30s) Decisions you made and what you would add next.

Practise the demo once before recording, and say each step out loud in your own words.
