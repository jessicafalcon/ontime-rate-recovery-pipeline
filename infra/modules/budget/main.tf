# A monthly budget with alert thresholds (default $50 and $150). A budget
# NOTIFIES; it does not stop spend — the real kill-switch (Pub/Sub -> Cloud
# Function disabling billing) is documented as optional in docs/DEPLOYMENT.md and
# left unbuilt (the meter is off by default, so there is nothing to run away).
#
# The budget amount is the SMALLEST threshold, so every threshold_percent is a
# whole multiple (50 -> 1.0, 150 -> 3.0) — no repeating decimal (0.333…) to drift
# the plan on every run. Thresholds > amount are valid ("over 100%") alerts.
# With no notification channel, GCP sends the alerts to the billing-account
# admins (docs/DEPLOYMENT.md).

locals {
  budget_amount = min(var.alert_thresholds...)
}

resource "google_billing_budget" "this" {
  billing_account = var.billing_account
  display_name    = var.display_name

  budget_filter {
    projects = ["projects/${var.project_number}"]
  }

  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(local.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = var.alert_thresholds
    content {
      threshold_percent = threshold_rules.value / local.budget_amount
    }
  }
}
