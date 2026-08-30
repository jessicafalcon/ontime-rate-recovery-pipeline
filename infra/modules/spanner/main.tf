# Phase 10: the Spanner serving layer, count-gated from the root
# (`module "spanner" { count = var.enable_spanner ? 1 : 0 }`), so a default
# plan/apply creates NOTHING here and the 90-day trial clock only starts on an
# explicit `VARS='enable_spanner=true'` apply (ask-first; the teardown date is
# recorded in docs/DEPLOYMENT.md the same day). The scoped teardown is the
# toggle flipped back (`VARS='enable_spanner=false'` re-apply): count → 0
# destroys exactly these resources — no MODULE variable, no -target.
#
# Contents: the smallest instance (100 processing units), one database whose
# DDL holds `dim_user` (SCD2 — the production dims home, §2.3; rendered from
# generator/models.py and pinned by tests/test_dbt_sources.py) and
# `send_schedule` (the §2.9 nine-column serving table the DuckDB stand-in
# mirrors), the BigQuery connection + `raw.dim_user_spanner` federation view
# (EXTERNAL_QUERY — §3.3's source swap), and two scoped grants. DDL is
# inlined here, not a side file: `tf-freeze`'s manifest pins *.tf only, and a
# file it does not pin could drift under the frozen tree.

# The two APIs this layer needs, enabled with it and kept on at destroy
# (disable_on_destroy = false, like the root set: a project-wide API another
# workload may use is never disabled by our teardown; enablement is free).
resource "google_project_service" "spanner" {
  for_each = toset([
    "spanner.googleapis.com",
    "bigqueryconnection.googleapis.com",
  ])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_spanner_instance" "this" {
  project      = var.project_id
  name         = "ontime"
  config       = "regional-${var.region}"
  display_name = "ontime"
  # The smallest provisionable size. Whether a Terraform-created instance sits
  # inside the 90-day free trial is verified on apply day (stack risk in the
  # Phase 10 spec); either way the teardown date in docs/DEPLOYMENT.md bounds it.
  processing_units = 100

  depends_on = [google_project_service.spanner]
}

locals {
  # PINNED to the contract render: tests/test_dbt_sources.py renders this DDL
  # from generator/models.py (scripts/gen_dbt_sources.py::spanner_dim_user_ddl)
  # and fails when the two differ. `make gen-sources` does NOT write here —
  # the repair is to paste the render, then `make tf-freeze CONFIRM=yes`.
  dim_user_ddl = <<EOT
create table dim_user (
    user_id string(max) not null,
    tz string(max) not null,
    cohort_id string(max) not null,
    signup_date date not null,
    valid_from timestamp not null,
    valid_to timestamp
) primary key (user_id, valid_from)
EOT

  # The §2.9 nine columns, in order — the same list as serving/ddl.sql and
  # serving/spanner.py::COLUMNS (pinned by tests/test_dbt_sources.py).
  send_schedule_ddl = <<EOT
create table send_schedule (
    user_id string(max) not null,
    cohort_id string(max) not null,
    send_hour_local int64 not null,
    send_minute_local int64 not null,
    tz string(max) not null,
    confidence float64 not null,
    model_version string(max) not null,
    computed_as_of timestamp not null,
    written_at timestamp not null
) primary key (user_id)
EOT

  # PINNED to the contract render (federation_view_sql), the same way. Each
  # column is cast to the generated BigQuery landing schema's type, so the
  # view's shape is raw.dim_user's by construction — not Spanner's type map.
  dim_user_view_sql = <<EOT
select
    cast(user_id as string) as user_id,
    cast(tz as string) as tz,
    cast(cohort_id as string) as cohort_id,
    cast(signup_date as date) as signup_date,
    cast(valid_from as timestamp) as valid_from,
    cast(valid_to as timestamp) as valid_to
from external_query(
    'projects/${var.project_id}/locations/${var.region}/connections/spanner_dims',
    'select user_id, tz, cohort_id, signup_date, valid_from, valid_to from dim_user'
)
EOT
}

resource "google_spanner_database" "this" {
  project  = var.project_id
  instance = google_spanner_instance.this.name
  name     = "ontime"
  ddl      = [trimspace(local.dim_user_ddl), trimspace(local.send_schedule_ddl)]
  # The toggle-flip re-apply IS the sanctioned destroy path and it is
  # CONFIRM-gated ($(origin) — infra/cli.py); provider-side protection here
  # would turn that one path into a two-apply dance. The flip side: while
  # Spanner is up, EVERY `tf-apply` must carry VARS='enable_spanner=true' —
  # the toggle defaults false, so an apply that omits it IS the teardown
  # (-auto-approve, no protection). docs/DEPLOYMENT.md's runbook says so;
  # `tf-plan` first shows the `destroy` lines.
  deletion_protection = false
}

# The federation connection (EXTERNAL_QUERY reads Spanner through it).
resource "google_bigquery_connection" "spanner_dims" {
  project       = var.project_id
  connection_id = "spanner_dims"
  location      = var.region

  cloud_spanner {
    database = "projects/${var.project_id}/instances/${google_spanner_instance.this.name}/databases/${google_spanner_database.this.name}"
  }

  # The connection API is enabled in this module; without the edge a fresh
  # apply races the not-yet-enabled API (the root modules' pattern).
  depends_on = [google_project_service.spanner]
}

# There is NO service-agent identity on the Spanner federation path (found
# live on the first apply, docs checked — ARCHITECTURE §8): EXTERNAL_QUERY over
# a Cloud Spanner connection runs as the QUERYING principal, which needs
# spanner.databaseReader on the database and bigquery.connectionUser on the
# connection. Both are the pipeline SA's grants below (databaseUser ⊇
# databaseReader). A grant to service-<number>@gcp-sa-bigqueryconnection was
# a grant to an identity that never participates — and one that does not
# exist until something else provisions it (the first apply failed on it).

# The pipeline SA writes send_schedule (the write-back), lands dim_user, and
# is the principal the federated read runs as: databaseUser on the ONE
# database — no instance/database admin.
resource "google_spanner_database_iam_member" "pipeline_user" {
  project  = var.project_id
  instance = google_spanner_instance.this.name
  database = google_spanner_database.this.name
  role     = "roles/spanner.databaseUser"
  member   = "serviceAccount:${var.sa_email}"
}

# Querying a view over EXTERNAL_QUERY needs use rights on the connection —
# granted to the pipeline SA on the ONE connection, not project-wide.
resource "google_bigquery_connection_iam_member" "pipeline_connection_user" {
  project       = var.project_id
  location      = var.region
  connection_id = google_bigquery_connection.spanner_dims.connection_id
  role          = "roles/bigquery.connectionUser"
  member        = "serviceAccount:${var.sa_email}"
}

# The federation view, in the raw dataset beside the landed dim_user table it
# can stand in for (the generated sources.yml's `dim_user_identifier` var is
# the swap — default unchanged, so free-tier builds never touch Spanner).
resource "google_bigquery_table" "dim_user_spanner" {
  project             = var.project_id
  dataset_id          = var.raw_dataset
  table_id            = "dim_user_spanner"
  deletion_protection = false

  view {
    query          = trimspace(local.dim_user_view_sql)
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_connection.spanner_dims]
}
