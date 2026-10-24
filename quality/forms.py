from django import forms

from .models import CONCESSION_CODES, Gauge


class GaugeChoiceField(forms.ModelChoiceField):
    """Shows expired gauges in the list too, clearly marked, so the block is visible."""

    def label_from_instance(self, gauge):
        label = f"{gauge.gauge_id} - {gauge.name}"
        return f"{label} (EXPIRED)" if gauge.is_expired() else label


class MeasurementForm(forms.Form):
    """One row of the inspection checklist."""

    field_id = forms.IntegerField(widget=forms.HiddenInput)
    value = forms.DecimalField(max_digits=10, decimal_places=3)
    gauge = GaugeChoiceField(queryset=Gauge.objects.all(), empty_label="Choose gauge")
    photo1 = forms.ImageField(required=False)
    photo2 = forms.ImageField(required=False)


class DecisionForm(forms.Form):
    """The supervisor's decision. The real rules are checked in the Inspection model."""

    DECISIONS = [
        ("approve", "Approve"),
        ("concession", "Release by concession"),
        ("quarantine", "Quarantine"),
    ]

    decision = forms.ChoiceField(choices=DECISIONS, widget=forms.RadioSelect)
    concession_code = forms.ChoiceField(
        choices=[("", "Choose a reason code")] + CONCESSION_CODES, required=False
    )
    note = forms.CharField(
        label="Justification / note",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
