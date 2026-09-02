# GCP foundation (Phase 9a). One module per concern; bigquery/gcs/iam/budget are
# free/near-free and unconditional (ARCHITECTURE §6), composer/spanner are
# count-gated behind enable_* toggles that default false, so a default plan
# creates zero of them (spec invariant 2). Auth is ADC (local `gcloud`) or WIF
# (CI, opt-in via enable_ci_wif) — no service-account key, ever.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }

  # Remote, versioned state on GCS (fix/tf-remote-state, ROADMAP item 2): the
  # teardown path survives a lost laptop, so an `enable_spanner=true` apply that
  # persists beyond one session is recoverable. The state bucket is
  # bootstrap-documented (docs/DEPLOYMENT.md § state-backend bootstrap), NOT
  # managed here: a bucket cannot create the backend that stores its own state,
  # and Terraform must never manage the bucket holding it. It is created by hand
  # (`<project_id>-tfstate`) and is NOT one of the resources below — `module.gcs`
  # manages a separate artifacts/staging bucket.
  #
  # PARTIAL config: the `bucket` is NOT written here (no live project id in a
  # tracked `.tf` — the redaction standard; PROJECT stays the one project
  # input). It is supplied at init time by `make tf-migrate-state PROJECT=<id>`
  # as `-backend-config=bucket=<id>-tfstate`, derived from the validated
  # PROJECT. `tf-validate` inits `-backend=false`, so it ignores this block;
  # `tf-plan`/`tf-apply` read the remote state once migrated (before the first
  # migrate/init, they ask for `terraform init` — the documented flow).
  backend "gcs" {
    prefix = "terraform/state"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  # No `credentials`/keyfile: ADC or WIF only (spec invariant 3).
  # User ADC (a developer's `gcloud auth application-default login`) carries no
  # quota project, and billingbudgets.googleapis.com refuses calls without one
  # (403 SERVICE_DISABLED on consumer "projects/<gcloud default>"). Send our own
  # project as the quota/billing project so no per-machine
  # `set-quota-project` step is needed (ARCHITECTURE §8 Gotchas).
  user_project_override = true
  billing_project       = var.project_id
}

locals {
  # Always derived — never a variable, so it can never be the state bucket
  # (review round 2 #1).
  staging_bucket = "${var.project_id}-ontime"
  # APIs the modules need. `serviceusage`/`cloudresourcemanager` are the two
  # bootstrap APIs that `google_project_service` and `data.google_project`
  # themselves require: on a brand-new project they must be enabled by hand once
  # (docs/DEPLOYMENT.md `gcloud services enable`), after which Terraform keeps the
  # whole set on. disable_on_destroy = false, so teardown never disables a
  # project-wide API.
  required_services = [
    "serviceusage.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
    "sts.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudbilling.googleapis.com",
    "billingbudgets.googleapis.com",
  ]
}

# The project's billing account and number — derived, so `project_id` stays the
# only required var (the budget needs neither as a separate input).
data "google_project" "this" {
  project_id = var.project_id
}

# Enable the APIs before the modules run (a fresh project has them off).
# disable_on_destroy = false: tearing down our stack must not disable a
# project-wide API other things may use.
resource "google_project_service" "required" {
  for_each = toset(local.required_services)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

module "bigquery" {
  source         = "./modules/bigquery"
  project_id     = var.project_id
  region         = var.region
  raw_dataset    = var.raw_dataset
  models_dataset = var.models_dataset

  depends_on = [google_project_service.required]
}

module "gcs" {
  source     = "./modules/gcs"
  project_id = var.project_id
  region     = var.region
  bucket     = local.staging_bucket

  depends_on = [google_project_service.required]
}

module "iam" {
  source             = "./modules/iam"
  project_id         = var.project_id
  raw_dataset        = module.bigquery.raw_dataset_id
  models_dataset     = module.bigquery.models_dataset_id
  bucket             = module.gcs.bucket_name
  enable_ci_wif      = var.enable_ci_wif
  github_repository  = var.github_repository
  github_ref         = var.github_ref
  operator_principal = var.operator_principal

  depends_on = [google_project_service.required]
}

module "budget" {
  source           = "./modules/budget"
  billing_account  = data.google_project.this.billing_account
  project_number   = data.google_project.this.number
  alert_thresholds = var.budget_alert_thresholds
  display_name     = "ontime-${var.project_id}"

  depends_on = [google_project_service.required]
}

# Phase 11: the Cloud Composer orchestration layer (environment, one scoped
# composer.worker grant, the DAG-bucket upload of the committed 8b DAG). Still
# count-gated: a default plan creates zero of these; the environment bills
# ~$300+/mo from creation, so it is applied for one demo-day run (Phase 12) and
# the toggle flipped back is the scoped teardown (docs/DEPLOYMENT.md).
module "composer" {
  source     = "./modules/composer"
  count      = var.enable_composer ? 1 : 0
  project_id = var.project_id
  region     = var.region
  sa_email   = module.iam.service_account_email
}

# Phase 10: the Spanner serving layer (instance, database DDL, federation
# connection + view, scoped grants). Still count-gated: a default plan creates
# zero of these; the PROVISIONED instance bills from the minute an explicit
# `VARS='enable_spanner=true'` apply creates it (Amendment M — no trial clock),
# and the toggle flipped back is the scoped teardown (docs/DEPLOYMENT.md
# carries the dates).
module "spanner" {
  source      = "./modules/spanner"
  count       = var.enable_spanner ? 1 : 0
  project_id  = var.project_id
  region      = var.region
  raw_dataset = module.bigquery.raw_dataset_id
  sa_email    = module.iam.service_account_email
}
