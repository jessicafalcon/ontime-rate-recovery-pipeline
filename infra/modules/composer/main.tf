# Phase 11: the Cloud Composer orchestration layer, count-gated from the root
# (`module "composer" { count = var.enable_composer ? 1 : 0 }`), so a default
# plan/apply creates NOTHING here. Cloud Composer is GCP's managed Apache
# Airflow: it runs the same DAG the local Docker Airflow runs (Phase 8b),
# scheduling `dbt build → write-back` on a data-interval-aware daily cadence with
# retries and on-demand backfill. It is used here instead of a cron job because
# the batch path is a real DAG (a quality gate that must block downstream models,
# explicit backfill, no auto-catch-up). It has a hard cost floor (~$300+/mo,
# billed continuously) that no config removes — so it is written now, proven
# plan-clean, and applied for ONE demo-day run (Phase 12), then destroyed the
# same session. The scoped teardown is the toggle flipped back
# (`VARS='enable_composer=false'` re-apply): count → 0 destroys exactly these
# resources — no MODULE variable, no -target. `tf-apply` plans first and refuses
# a destroying plan without ALLOW_DESTROY=yes (Amendment N1), so an apply that
# omits the toggle while Composer is up stops with the addresses printed.
#
# Contents: the Composer API enablement, the smallest environment (running as the
# existing least-privilege pipeline SA, not a broad default Compute SA), one
# scoped `roles/composer.worker` grant (the documented minimum an environment's
# service account needs), and the DAG-bucket upload of the committed Phase 8b DAG.

# The Composer API, enabled with the module and kept on at destroy
# (disable_on_destroy = false, like the root and Spanner sets: a project-wide API
# another workload may use is never disabled by our teardown; enablement is free).
resource "google_project_service" "composer" {
  project            = var.project_id
  service            = "composer.googleapis.com"
  disable_on_destroy = false
}

# The environment's service account needs roles/composer.worker (the documented
# minimum). We grant it to the EXISTING pipeline SA (module.iam) rather than let
# Composer fall back to the default Compute Engine SA, which carries broad
# project-wide access we never want the orchestrator to have. Project-scoped
# because composer.worker is a project role; the member is the one SA.
# There is intentionally NO grant to the Composer Service Agent here (the Spanner
# §8 lesson — a service agent is not ours to grant; the API enablement provisions
# it, and Phase 12's live apply is where any missing service-agent edge would
# surface, cost-free at plan time).
resource "google_project_iam_member" "worker" {
  project = var.project_id
  role    = "roles/composer.worker"
  member  = "serviceAccount:${var.sa_email}"

  depends_on = [google_project_service.composer]
}

# The smallest environment (ENVIRONMENT_SIZE_SMALL) on the current Composer
# image, running as the pipeline SA. The exact image build is resolved at apply
# (Phase 12) — the alias here pins the major line; tf-validate checks the schema,
# not the image value (an apply-time API lookup). node_config sets the runtime
# identity; the worker grant above must exist first (depends_on).
resource "google_composer_environment" "this" {
  name    = "ontime"
  project = var.project_id
  region  = var.region

  config {
    environment_size = "ENVIRONMENT_SIZE_SMALL"

    software_config {
      image_version = "composer-3-airflow-2"
    }

    node_config {
      service_account = var.sa_email
    }
  }

  depends_on = [google_project_iam_member.worker]
}

locals {
  # The environment creates its own DAG bucket; `dag_gcs_prefix` is
  # `gs://<bucket>/dags`. Split to the bucket name for the object upload:
  # ["gs:", "", "<bucket>", "dags"][2].
  dag_bucket = split("/", google_composer_environment.this.config[0].dag_gcs_prefix)[2]
}

# Upload the committed Phase 8b DAG (and its task manifest) into the
# environment's DAG bucket. `source` points at the repo file, never an inline
# heredoc, so the deployed DAG cannot drift from the reviewed one. Whether the
# Composer workers can EXECUTE the `make` targets the DAG shells out to (repo,
# uv, dbt present on the workers) is Phase 12's live concern, with the Docker
# Airflow → BigQuery fallback; Phase 11 proves the upload PLANS.
resource "google_storage_bucket_object" "dag" {
  name   = "dags/pipeline_dag.py"
  bucket = local.dag_bucket
  source = "${path.module}/../../../orchestration/dags/pipeline_dag.py"
}

resource "google_storage_bucket_object" "tasks" {
  name   = "dags/tasks.py"
  bucket = local.dag_bucket
  source = "${path.module}/../../../orchestration/tasks.py"
}
