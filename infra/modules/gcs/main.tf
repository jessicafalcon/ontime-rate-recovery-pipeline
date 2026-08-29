# One artifacts/staging bucket (NOT the Terraform state bucket — that is
# bootstrap-only, docs/DEPLOYMENT.md). Versioned, uniform access, public access
# prevention enforced (never world-readable even by accident), a lifecycle rule
# that reaps noncurrent versions so versioning does not accrete cost, and
# force_destroy so `tf-destroy` removes it even with objects — nothing billable
# survives (spec invariant 5).

resource "google_storage_bucket" "this" {
  project                     = var.project_id
  name                        = var.bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 3
    }
    action {
      type = "Delete"
    }
  }
}
