"""
Views. They stay thin: check the role, call a model method, show the result.
All the quality rules are in models.py.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import DecisionForm, MeasurementForm
from .models import (
    Gauge, GatePass, Inspection, InspectionField, Job, RuleError,
)
from .permissions import CLERK, INSPECTOR, SUPERVISOR, role_required


def _show_form_errors(request, form):
    for field, errors in form.errors.items():
        for error in errors:
            name = "" if field == "__all__" else f"{field.replace('_', ' ').title()}: "
            messages.error(request, f"{name}{error}")


def _own_inspection(request, pk):
    """Fetch an inspection, but only for the inspector who started it."""
    inspection = get_object_or_404(Inspection, pk=pk)
    if not request.user.is_superuser and inspection.inspector_id != request.user.id:
        raise PermissionDenied("This is another inspector's inspection.")
    return inspection


# ---------------------------------------------------------------------------
# Home
# ---------------------------------------------------------------------------

@login_required
def home(request):
    context = {
        "open_inspections": Inspection.objects.filter(
            inspector=request.user, status=Inspection.IN_PROGRESS
        ).count(),
        "pending_reviews": Inspection.objects.filter(status=Inspection.PENDING_REVIEW).count(),
    }
    return render(request, "quality/home.html", context)


# ---------------------------------------------------------------------------
# Inspector
# ---------------------------------------------------------------------------

@role_required(INSPECTOR)
def job_list(request):
    rows = []
    for job in Job.objects.select_related("product", "machine"):
        rows.append({
            "job": job,
            "latest": job.latest_inspection(),
            "open": job.open_inspection(),
            "quarantined": job.is_quarantined(),
        })
    return render(request, "quality/job_list.html", {"rows": rows})


@role_required(INSPECTOR)
@require_POST
def start_inspection(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    try:
        inspection = Inspection.start(job, request.user)
    except RuleError as error:
        messages.error(request, str(error))
        return redirect("job_list")
    return redirect("inspection_detail", pk=inspection.pk)


@role_required(INSPECTOR)
def inspection_detail(request, pk):
    inspection = _own_inspection(request, pk)
    measured = {m.field_id: m for m in inspection.measurements.prefetch_related("photos")}
    rows = [
        {"field": field, "measurement": measured.get(field.id)}
        for field in inspection.job.product.fields.all()
    ]
    context = {
        "inspection": inspection,
        "rows": rows,
        "gauges": Gauge.objects.all(),
        "editable": inspection.status == Inspection.IN_PROGRESS,
        "frozen": inspection.status != Inspection.IN_PROGRESS and inspection.has_ctq_failure(),
    }
    return render(request, "quality/inspection_detail.html", context)


@role_required(INSPECTOR)
@require_POST
def add_measurement(request, pk):
    inspection = _own_inspection(request, pk)
    form = MeasurementForm(request.POST, request.FILES)
    if not form.is_valid():
        _show_form_errors(request, form)
        return redirect("inspection_detail", pk=pk)

    data = form.cleaned_data
    field = get_object_or_404(InspectionField, pk=data["field_id"])
    photos = [p for p in (data["photo1"], data["photo2"]) if p]

    try:
        measurement = inspection.record_measurement(field, data["value"], data["gauge"], photos)
    except RuleError as error:
        messages.error(request, str(error))
    else:
        if measurement.passed:
            messages.success(request, f"{field.label}: {measurement.value} is within limits (green).")
        elif field.is_ctq:
            messages.error(
                request,
                f"CTQ FAILED on '{field.label}'. The checklist is frozen and has been sent to the supervisor.",
            )
        else:
            messages.warning(request, f"{field.label}: {measurement.value} is out of limits (red). Photos saved.")
    return redirect("inspection_detail", pk=pk)


@role_required(INSPECTOR)
@require_POST
def submit_inspection(request, pk):
    inspection = _own_inspection(request, pk)
    try:
        inspection.submit()
    except RuleError as error:
        messages.error(request, str(error))
        return redirect("inspection_detail", pk=pk)
    messages.success(request, "Inspection submitted. It is now waiting for a supervisor.")
    return redirect("job_list")


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

@role_required(SUPERVISOR)
def review_queue(request):
    pending = Inspection.objects.filter(status=Inspection.PENDING_REVIEW).select_related(
        "job__product", "inspector"
    ).order_by("submitted_at")
    decided = Inspection.objects.exclude(
        status__in=[Inspection.IN_PROGRESS, Inspection.PENDING_REVIEW]
    ).select_related("job__product", "reviewed_by")[:10]
    return render(request, "quality/review_queue.html", {"pending": pending, "decided": decided})


@role_required(SUPERVISOR)
def review_detail(request, pk):
    inspection = get_object_or_404(Inspection.objects.select_related("job__product", "inspector"), pk=pk)
    form = DecisionForm()

    if request.method == "POST":
        form = DecisionForm(request.POST)
        if form.is_valid():
            decision = form.cleaned_data["decision"]
            note = form.cleaned_data["note"]
            try:
                if decision == "approve":
                    inspection.approve(request.user, note)
                elif decision == "concession":
                    inspection.concede(request.user, form.cleaned_data["concession_code"], note)
                else:
                    inspection.quarantine(request.user, note)
            except RuleError as error:
                messages.error(request, str(error))
            else:
                messages.success(request, f"{inspection} is now: {inspection.get_status_display()}.")
                return redirect("review_queue")

    measured = {m.field_id: m for m in inspection.measurements.prefetch_related("photos")}
    rows = [
        {"field": field, "measurement": measured.get(field.id)}
        for field in inspection.job.product.fields.all()
    ]
    context = {
        "inspection": inspection,
        "form": form,
        "rows": rows,
        "has_ctq_failure": inspection.has_ctq_failure(),
        "has_failure": inspection.has_failure(),
        "can_decide": inspection.status == Inspection.PENDING_REVIEW,
    }
    return render(request, "quality/review_detail.html", context)


# ---------------------------------------------------------------------------
# Warehouse clerk: the shipping lock
# ---------------------------------------------------------------------------

@role_required(CLERK)
def shipping_list(request):
    rows = []
    for job in Job.objects.select_related("product"):
        rows.append({
            "job": job,
            "latest": job.latest_inspection(),
            "reason": job.shipping_block_reason(),
            "gate_pass": GatePass.objects.filter(job=job).first(),
        })
    return render(request, "quality/shipping_list.html", {"rows": rows})


@role_required(CLERK)
@require_POST
def create_gate_pass(request, job_id):
    job = get_object_or_404(Job, pk=job_id)
    try:
        gate_pass = job.create_gate_pass(request.user)
    except RuleError as error:
        messages.error(request, f"SHIPPING BLOCKED for {job}: {error}")
        return redirect("shipping_list")
    messages.success(request, f"Gate pass created for {job}.")
    return redirect("gate_pass_detail", pk=gate_pass.pk)


@role_required(CLERK)
def gate_pass_detail(request, pk):
    gate_pass = get_object_or_404(GatePass.objects.select_related("job__product", "inspection", "created_by"), pk=pk)
    return render(request, "quality/gate_pass.html", {"gate_pass": gate_pass})
