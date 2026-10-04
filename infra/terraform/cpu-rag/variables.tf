variable "aws_account_id" {
  type        = string
  description = "Account reviewed by the operator; prevents applying in another profile's account."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Set the intended 12-digit AWS account ID locally."
  }
}
variable "subnet_id" {
  type        = string
  description = "Existing Seoul public subnet with an Internet Gateway route, DNS and outbound access."
  validation {
    condition     = can(regex("^subnet-[0-9a-f]+$", var.subnet_id))
    error_message = "Choose an existing subnet; this module does not create a VPC."
  }
}
variable "ami_id" {
  type        = string
  description = "Reviewed Canonical Ubuntu 24.04 amd64 AMI in Seoul; immutable input for the saved plan."
  validation {
    condition     = can(regex("^ami-[0-9a-f]+$", var.ami_id))
    error_message = "Set a reviewed Ubuntu AMI ID."
  }
}
variable "evidence_bucket" {
  type    = string
  default = "boram3-cctv-evidence-80bee9db7c3bca316ef395375a"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.evidence_bucket))
    error_message = "Use the existing bucket's name, without a URL or path."
  }
}
variable "instance_type" {
  type        = string
  default     = "t3.small"
  description = "2 vCPU / 2 GiB for the first DB/RAG test; confirmed free-tier-eligible type. Full service capacity is not benchmarked."
  validation {
    condition     = contains(["t3.small", "t3.medium", "t3.large"], var.instance_type)
    error_message = "This Ubuntu amd64 draft is tested for the T3 family only."
  }
}
variable "root_disk_gib" {
  type    = number
  default = 20
  validation {
    condition     = var.root_disk_gib >= 20 && var.root_disk_gib <= 100 && floor(var.root_disk_gib) == var.root_disk_gib
    error_message = "Choose 20–100 GiB for OS, container images and the small initial database together."
  }
}

variable "ssh_source_cidr" {
  type        = string
  description = "Operator's current public IPv4 with /32; do not allow broad SSH ingress."
  validation {
    condition     = can(cidrnetmask(var.ssh_source_cidr)) && endswith(var.ssh_source_cidr, "/32")
    error_message = "SSH must be limited to a single IPv4 address with /32."
  }
}

variable "ssh_public_key" {
  type        = string
  description = "OpenSSH Ed25519 public key only; the private key stays on the operator's Mac."
  validation {
    condition     = can(regex("^ssh-ed25519 [A-Za-z0-9+/=]+( .*)?$", trimspace(var.ssh_public_key)))
    error_message = "Provide one OpenSSH Ed25519 public key, never a private key."
  }
}

variable "metadata_response_hop_limit" {
  type        = number
  default     = 1
  nullable    = false
  description = "IMDSv2 response hop limit. Use 2 for the approved Docker BE instance-role access."
  validation {
    condition     = contains([1, 2], var.metadata_response_hop_limit)
    error_message = "Use 1 for host access or 2 for Docker container access."
  }
}

variable "existing_instance_profile_name" {
  type        = string
  default     = ""
  nullable    = false
  description = "Existing administrator-managed EC2 instance profile name, not a role/policy ARN. Empty preserves the initial deployment without a role."
  validation {
    condition     = var.existing_instance_profile_name == "" || can(regex("^[A-Za-z0-9_+=,.@-]{1,128}$", var.existing_instance_profile_name))
    error_message = "Use the verified instance profile name (1–128 characters), or an empty string; do not pass an ARN or path."
  }
}
