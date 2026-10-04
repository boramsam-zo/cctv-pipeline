terraform {
  required_version = ">= 1.7, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = "ap-northeast-2"
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "boram3-cctv", ManagedBy = "Terraform", Purpose = "cpu-rag-demo" }
  }
}

data "aws_subnet" "selected" {
  id = var.subnet_id
}

locals {
  compose_dir = "${path.module}/../../compose/rag"
  host_files = {
    "/opt/cctv-stack/compose.yaml"    = "${local.compose_dir}/compose.yaml"
    "/opt/cctv-stack/init-db.sql"     = "${local.compose_dir}/init-db.sql"
    "/opt/cctv-infra/prepare-host.py" = "${local.compose_dir}/prepare-host.py"
    "/opt/cctv-infra/bootstrap.sh"    = "${path.module}/scripts/bootstrap.sh"
  }
}

# Pin the reviewed Canonical Ubuntu 24.04 amd64 AMI in tfvars before plan.
data "aws_ami" "ubuntu" {
  owners = ["099720109477"]
  filter {
    name   = "image-id"
    values = [var.ami_id]
  }
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_key_pair" "operator" {
  key_name_prefix = "cctv-cpu-rag-"
  public_key      = var.ssh_public_key
}

resource "aws_security_group" "host" {
  name_prefix = "cctv-cpu-rag-"
  # Keep the immutable description of the existing, unused security group.
  # AccessMode and the actual rule below describe the revised SSH deployment.
  description = "No inbound ports; SSM management and an unpublished Compose database"
  vpc_id      = data.aws_subnet.selected.vpc_id
  tags        = { AccessMode = "ssh-single-ip" }
  ingress {
    description = "SSH from the operator current public IPv4 only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_source_cidr]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Package repositories, SSM, S3 and Gemini calls"
  }
}

resource "aws_instance" "host" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type
  subnet_id                   = data.aws_subnet.selected.id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.host.id]
  key_name                    = aws_key_pair.operator.key_name
  iam_instance_profile        = var.existing_instance_profile_name
  disable_api_termination     = true
  user_data_replace_on_change = true
  tags                        = { Name = "cctv-cpu-rag" }

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = var.metadata_response_hop_limit
    instance_metadata_tags      = "disabled"
  }
  credit_specification {
    cpu_credits = "standard"
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_disk_gib
    encrypted             = true
    delete_on_termination = false
  }

  user_data_base64 = base64gzip("#cloud-config\n${jsonencode({
    write_files = [
      for destination, source in local.host_files : {
        path        = destination
        permissions = "0644"
        owner       = "root:root"
        encoding    = "b64"
        content     = filebase64(source)
      }
    ]
    ssh_pwauth   = false
    disable_root = true
    runcmd       = [["bash", "/opt/cctv-infra/bootstrap.sh", var.evidence_bucket, "false"]]
  })}")

  lifecycle {
    prevent_destroy = true
  }
}

output "instance_id" { value = aws_instance.host.id }
output "selected_ami" { value = data.aws_ami.ubuntu.id }
output "database_location" {
  description = "PG16 bind mount on the EC2 root disk; no separate database EBS resource."
  value       = "/srv/cctv/postgresql"
}
output "public_ip" { value = aws_instance.host.public_ip }
output "ssh_user" { value = "ubuntu" }
output "ssh_key_name" { value = aws_key_pair.operator.key_name }
output "s3_backup_enabled" {
  value = false
}
