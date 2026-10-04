mock_provider "aws" {}

override_data {
  target = data.aws_subnet.selected
  values = {
    id                = "subnet-0123456789abcdef0"
    vpc_id            = "vpc-0123456789abcdef0"
    availability_zone = "ap-northeast-2a"
  }
}
override_data {
  target = data.aws_ami.ubuntu
  values = { id = "ami-0123456789abcdef0" }
}

variables {
  aws_account_id = "123456789012"
  subnet_id      = "subnet-0123456789abcdef0"
  ami_id         = "ami-0123456789abcdef0"
  ssh_source_cidr = "203.0.113.10/32"
  ssh_public_key  = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestFixture"
}

run "single_host_private_database" {
  command = plan
  assert {
    condition = length(aws_security_group.host.ingress) == 1 && alltrue([
      for rule in aws_security_group.host.ingress :
      rule.from_port == 22 && rule.to_port == 22 && rule.protocol == "tcp" &&
      length(rule.cidr_blocks) == 1 && contains(rule.cidr_blocks, "203.0.113.10/32")
    ])
    error_message = "Only SSH from the selected single IPv4 may be exposed; DB stays internal."
  }
  assert {
    condition     = aws_instance.host.iam_instance_profile == "" && !output.s3_backup_enabled
    error_message = "The SSH deployment must not require an IAM role or enable S3 automatic backup."
  }
  assert {
    condition     = aws_instance.host.root_block_device[0].encrypted && !aws_instance.host.root_block_device[0].delete_on_termination
    error_message = "The root disk holding the DB must be encrypted and retained on termination."
  }
  assert {
    condition     = aws_instance.host.metadata_options[0].http_tokens == "required"
    error_message = "Instance-role access requires IMDSv2."
  }
  assert {
    condition     = aws_instance.host.credit_specification[0].cpu_credits == "standard"
    error_message = "Do not enable extra CPU credit charges without review."
  }
  assert {
    condition     = output.database_location == "/srv/cctv/postgresql"
    error_message = "Store database files in the reviewed root-disk bind mount."
  }
}

run "reject_broad_ssh_ingress" {
  command = plan
  variables { ssh_source_cidr = "0.0.0.0/0" }
  expect_failures = [var.ssh_source_cidr]
}

run "attach_existing_profile_only" {
  command = plan
  variables { existing_instance_profile_name = "reviewed-cctv-instance-profile" }
  assert {
    condition     = aws_instance.host.iam_instance_profile == "reviewed-cctv-instance-profile"
    error_message = "Attach the provided instance profile name; the role name may differ."
  }
  assert {
    condition     = !output.s3_backup_enabled
    error_message = "Attaching a profile must not implicitly start automatic backups."
  }
  assert {
    condition     = aws_instance.host.metadata_options[0].http_tokens == "required" && aws_instance.host.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "Keep the reviewed IMDSv2 host access settings."
  }
}

run "reject_profile_arn" {
  command = plan
  variables { existing_instance_profile_name = "arn:aws:iam::123456789012:instance-profile/example" }
  expect_failures = [var.existing_instance_profile_name]
}

run "container_instance_role_access" {
  command = plan
  variables {
    existing_instance_profile_name = "reviewed-cctv-instance-profile"
    metadata_response_hop_limit    = 2
  }
  assert {
    condition = (
      aws_instance.host.iam_instance_profile == "reviewed-cctv-instance-profile" &&
      aws_instance.host.metadata_options[0].http_tokens == "required" &&
      aws_instance.host.metadata_options[0].http_put_response_hop_limit == 2 &&
      !output.s3_backup_enabled
    )
    error_message = "Docker role access must retain IMDSv2 and must not enable automatic backups."
  }
}

run "reject_excessive_metadata_hops" {
  command = plan
  variables { metadata_response_hop_limit = 3 }
  expect_failures = [var.metadata_response_hop_limit]
}
