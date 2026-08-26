# A monthly budget with alert thresholds (default $50 and $150). A budget
# NOTIFIES; it does not stop spend — the real kill-switch (Pub/Sub -> Cloud
# Function disabling billing) is documented as optional in docs/DEPLOYMENT.md and
# left unbuilt (the meter is off by default, so there is nothing to run away).

locals {
  budget_total = max(var.alert_thresholds...)
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
      units         = tostring(local.budget_total)
    }
  }

  dynamic "threshold_rules" {
    for_each = var.alert_thresholds
    content {
      threshold_percent = threshold_rules.value / local.budget_total
    }
  }
}
