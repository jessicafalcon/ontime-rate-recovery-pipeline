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
#
# The currency is the BILLING ACCOUNT's (a budget in any other currency is a
# 400 "invalid argument" — found on the first live apply, ARCHITECTURE §8), read
# here at apply time (the module `depends_on` the API enablement, so a fresh
# project still plans). The thresholds are therefore numbers in that currency;
# the "$50/$150" in the records assumes a USD account.

data "google_billing_account" "this" {
  billing_account = var.billing_account
}

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
      currency_code = data.google_billing_account.this.currency_code
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
