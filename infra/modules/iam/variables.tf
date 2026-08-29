variable "project_id" {
  type = string
}

variable "raw_dataset" {
  type = string
}

variable "models_dataset" {
  type = string
}

variable "bucket" {
  type = string
}

variable "enable_ci_wif" {
  type = bool
}

variable "github_repository" {
  type = string
}

variable "github_ref" {
  type = string
}
