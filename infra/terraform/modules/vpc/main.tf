# Three subnet tiers per AZ: public (ALB + NAT), private-app (Fargate),
# private-db (RDS/Redis, no internet route). /20 blocks carved from the VPC CIDR.
locals {
  az_count = length(var.azs)

  # One NAT gateway total (demo) or one per AZ (HA).
  nat_count = var.high_availability ? local.az_count : 1

  public_subnets = [for i, az in var.azs : {
    az   = az
    cidr = cidrsubnet(var.cidr_block, 4, i)
  }]
  app_subnets = [for i, az in var.azs : {
    az   = az
    cidr = cidrsubnet(var.cidr_block, 4, i + local.az_count)
  }]
  db_subnets = [for i, az in var.azs : {
    az   = az
    cidr = cidrsubnet(var.cidr_block, 4, i + (2 * local.az_count))
  }]
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name_prefix}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name_prefix}-igw" }
}

resource "aws_subnet" "public" {
  count             = local.az_count
  vpc_id            = aws_vpc.this.id
  availability_zone = local.public_subnets[count.index].az
  cidr_block        = local.public_subnets[count.index].cidr
  # No auto-assigned public IPs: only the ALB (its own DNS) and NAT (its EIP)
  # live here; nothing is launched needing an auto-assigned address.
  map_public_ip_on_launch = false
  tags                    = { Name = "${var.name_prefix}-public-${count.index}", Tier = "public" }
}

# Lock down the VPC's default security group (deny all in/out) so nothing can
# accidentally rely on it.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name_prefix}-default-deny-all" }
}

resource "aws_subnet" "app" {
  count             = local.az_count
  vpc_id            = aws_vpc.this.id
  availability_zone = local.app_subnets[count.index].az
  cidr_block        = local.app_subnets[count.index].cidr
  tags              = { Name = "${var.name_prefix}-app-${count.index}", Tier = "app" }
}

resource "aws_subnet" "db" {
  count             = local.az_count
  vpc_id            = aws_vpc.this.id
  availability_zone = local.db_subnets[count.index].az
  cidr_block        = local.db_subnets[count.index].cidr
  tags              = { Name = "${var.name_prefix}-db-${count.index}", Tier = "db" }
}

# --- Public routing: straight to the internet gateway ---
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name_prefix}-rt-public" }
}

resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count          = local.az_count
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --- NAT for private-subnet egress (DeepSeek/OpenAI/Tavily/Google) ---
resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"
  tags   = { Name = "${var.name_prefix}-nat-eip-${count.index}" }
}

resource "aws_nat_gateway" "this" {
  count         = local.nat_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = { Name = "${var.name_prefix}-nat-${count.index}" }
  depends_on    = [aws_internet_gateway.this]
}

# One private route table per AZ, each pointing at its NAT (or the single shared
# NAT in demo mode). db subnets share the app route tables for egress at boot.
resource "aws_route_table" "private" {
  count  = local.az_count
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name_prefix}-rt-private-${count.index}" }
}

resource "aws_route" "private_nat" {
  count                  = local.az_count
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  # In HA there's a NAT per AZ; in demo every AZ routes through the single NAT.
  nat_gateway_id = aws_nat_gateway.this[var.high_availability ? count.index : 0].id
}

resource "aws_route_table_association" "app" {
  count          = local.az_count
  subnet_id      = aws_subnet.app[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "db" {
  count          = local.az_count
  subnet_id      = aws_subnet.db[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Free S3 gateway endpoint — keeps ECR image-layer pulls (backed by S3) off the
# metered NAT path. Cost-free and always worth having.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id
  tags              = { Name = "${var.name_prefix}-vpce-s3" }
}

data "aws_region" "current" {}
