variable "billing_account" {
  type = string
}

variable "project_number" {
  type = string
}

variable "alert_thresholds" {
  type = list(number)
}

variable "display_name" {
  type = string
}
