output "instance" {
  value = google_spanner_instance.this.name
}

output "database" {
  value = google_spanner_database.this.name
}

output "connection_id" {
  value = google_bigquery_connection.spanner_dims.connection_id
}
