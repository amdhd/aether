"""
Aether AWS Architecture Diagram — uses diagrams library for real AWS icons.
Run:  uvx --with diagrams python3 gen_diagram.py

Changes from previous version:
  1. Single HA ALB (spans both AZs) — not one per AZ
  2. WAF moved to regional scope in front of ALB (not at CloudFront edge)
  3. VPC Endpoints cluster added (S3 gateway + ECR api/dkr + Secrets Manager + CW Logs)
  4. ALB anchored in the public-subnet tier (matches Terraform subnets = public_subnet_ids)
  5. Explicit internet-facing inbound path IGW → ALB; WAF shown attached to the ALB
     (regional WAF is associated with the ALB, not a separate network hop)
"""

from diagrams import Diagram, Cluster, Edge
from diagrams.aws.network import (
    Route53, CloudFront, ElbApplicationLoadBalancer,
    InternetGateway, NATGateway, Privatelink,
)
from diagrams.aws.security import ACM, WAF, SecretsManager
from diagrams.aws.storage import SimpleStorageServiceS3
from diagrams.aws.compute import Fargate, ECR
from diagrams.aws.database import RDSPostgresqlInstance, ElasticacheForRedis
from diagrams.aws.management import Cloudwatch
from diagrams.aws.integration import SNS
from diagrams.aws.cost import Budgets
from diagrams.aws.general import Client

OUTPUT = "aether_aws_architecture"

GRAPH = dict(
    fontsize="13",
    bgcolor="white",
    pad="1.5",
    splines="ortho",
    nodesep="1.0",
    ranksep="1.8",
    fontname="Helvetica Bold",
    overlap="false",
    concentrate="false",
)

BLUE   = "#1565C0"; ORANGE = "#E65100"; GREEN  = "#2E7D32"
PURPLE = "#6A1B9A"; RED    = "#B71C1C"; DKGRN  = "#1B5E20"
TEAL   = "#006064"; GREY   = "#9E9E9E"

def ef(lbl="", color=BLUE, bold=False, dashed=False):
    return Edge(
        label=lbl, color=color,
        style="bold" if bold else ("dashed" if dashed else "solid"),
    )

def c(**kw):
    base = dict(style="rounded", fontname="Helvetica Bold", penwidth="2.0")
    base.update(kw)
    return base

with Diagram(
    "Aether — Production AWS Architecture  (High Availability · Multi-AZ)",
    filename=OUTPUT,
    show=False,
    direction="LR",
    graph_attr=GRAPH,
):

    users = Client("Users")

    # ── EDGE & DNS  (CloudFront edge only — no WAF here) ──────────────────────
    with Cluster("Edge & DNS", graph_attr=c(
            bgcolor="#E3F2FD", pencolor=BLUE, fontcolor=BLUE, fontsize="14")):
        dns = Route53("Route 53\nDNS")
        acm = ACM("ACM\nTLS Certificates")
        cf  = CloudFront("CloudFront\nSPA · HTTPS")
        s3  = SimpleStorageServiceS3("S3\nStatic SPA Build\n(OAC)")

    # ── VPC ───────────────────────────────────────────────────────────────────
    with Cluster("VPC  (2 Availability Zones)", graph_attr=c(
            bgcolor="#F1F8E9", pencolor=GREEN, fontcolor=GREEN,
            fontsize="15", penwidth="3.0")):

        igw = InternetGateway("Internet\nGateway")

        # ── PUBLIC-SUBNET INGRESS TIER: single internet-facing HA ALB that
        #    spans public subnet A + B; regional WAF attached to it ──────────
        with Cluster("Public Subnets A + B  ·  Ingress (internet-facing)", graph_attr=c(
                bgcolor="#FFF8E1", pencolor="#F57F17", fontsize="13")):
            waf = WAF("AWS WAF\nRegional · attached to ALB\nRate-limit + Managed Rules")
            alb = ElbApplicationLoadBalancer(
                "Application Load Balancer\nInternet-facing · HTTPS 443\nspans public subnet A + B")

        # ── AZ A ──────────────────────────────────────────────────────────────
        with Cluster("Availability Zone A", graph_attr=c(
                bgcolor="#FFFDE7", style="dashed",
                pencolor="#F57F17", fontcolor="#E65100", fontsize="13")):

            with Cluster("Public Subnet A", graph_attr=c(
                    bgcolor="#FFF9C4", pencolor="#F9A825", fontsize="12")):
                nat_a = NATGateway("NAT Gateway A")

            with Cluster("Private App Subnet A", graph_attr=c(
                    bgcolor="#E8F5E9", pencolor="#388E3C", fontsize="12")):
                fargate_a = Fargate("ECS Fargate\nFastAPI · ARM64\nAZ A")
                redis_a   = ElasticacheForRedis("ElastiCache Redis\nPRIMARY · AZ A")

            with Cluster("Private DB Subnet A", graph_attr=c(
                    bgcolor="#FCE4EC", pencolor="#C62828", fontsize="12")):
                rds_a = RDSPostgresqlInstance("RDS PostgreSQL\n+ pgvector\nPRIMARY · AZ A")

        # ── AZ B ──────────────────────────────────────────────────────────────
        with Cluster("Availability Zone B", graph_attr=c(
                bgcolor="#E8EAF6", style="dashed",
                pencolor="#283593", fontcolor="#1A237E", fontsize="13")):

            with Cluster("Public Subnet B", graph_attr=c(
                    bgcolor="#E8EAF6", pencolor="#3949AB", fontsize="12")):
                nat_b = NATGateway("NAT Gateway B")

            with Cluster("Private App Subnet B", graph_attr=c(
                    bgcolor="#E8F5E9", pencolor="#388E3C", fontsize="12")):
                fargate_b = Fargate("ECS Fargate\nFastAPI · ARM64\nAZ B")
                redis_b   = ElasticacheForRedis("ElastiCache Redis\nREPLICA · AZ B")

            with Cluster("Private DB Subnet B", graph_attr=c(
                    bgcolor="#FCE4EC", pencolor="#C62828", fontsize="12")):
                rds_b = RDSPostgresqlInstance("RDS PostgreSQL\nSTANDBY · AZ B\n(Multi-AZ)")

        # ── VPC ENDPOINTS (PrivateLink — stays on AWS backbone) ───────────────
        with Cluster("VPC Endpoints  (PrivateLink — AWS backbone)", graph_attr=c(
                bgcolor="#F3E5F5", pencolor="#6A1B9A", fontcolor="#4A148C", fontsize="13")):
            ep_ecr = Privatelink("Interface Endpoint\nECR api + dkr")
            ep_sm  = SecretsManager("Interface Endpoint\nSecrets Manager")
            ep_cw  = Privatelink("Interface Endpoint\nCloudWatch Logs")
            ep_s3  = Privatelink("Gateway Endpoint\nS3")
            ecr    = ECR("Amazon ECR\n(scan-on-push)")

    # ── OBSERVABILITY ─────────────────────────────────────────────────────────
    with Cluster("Observability & Cost", graph_attr=c(
            bgcolor="#E0F7FA", pencolor=TEAL, fontcolor=TEAL, fontsize="14")):
        cw      = Cloudwatch("CloudWatch\nLogs · Metrics · Alarms\nDashboard")
        sns     = SNS("SNS\nAlarm Notifications")
        budgets = Budgets("AWS Budgets\nmonitors billing\n→ email 80% / 100%")

    # ── EXTERNAL APIs ─────────────────────────────────────────────────────────
    with Cluster("External APIs  (internet · via NAT → IGW)", graph_attr=c(
            bgcolor="#FFF3E0", pencolor=ORANGE, fontcolor="#BF360C", fontsize="14")):
        deepseek = Client("DeepSeek\nLLM")
        openai   = Client("OpenAI\nEmbeddings")
        tavily   = Client("Tavily\nWeb Search")
        gcal     = Client("Google\nCalendar API")

    # ═════════════════════════════════════════════════════════════════════════
    # FLOW EDGES
    # ═════════════════════════════════════════════════════════════════════════

    # ① SPA load: User → R53 → CloudFront → S3
    users >> ef("① DNS",           BLUE, bold=True) >> dns
    dns   >> ef("① SPA (HTTPS)",   BLUE, bold=True) >> cf
    cf    >> ef("① OAC · private", BLUE, dashed=True) >> s3
    acm   >> ef("TLS cert",        PURPLE, dashed=True) >> cf

    # ② API: User → R53 → IGW → ALB (public subnets, both AZs) → Fargate.
    #    WAF is associated with the ALB and inspects at it (not a separate hop).
    dns   >> ef("② API (HTTPS)",         ORANGE, bold=True) >> igw
    igw   >> ef("② inbound → ALB",       ORANGE, bold=True) >> alb
    waf   >> ef("attached · inspects",   ORANGE, dashed=True) >> alb
    acm   >> ef("TLS cert",              PURPLE, dashed=True) >> alb
    alb   >> ef("② → target group AZ A", ORANGE, bold=True) >> fargate_a
    alb   >> ef("② → target group AZ B", ORANGE, bold=True) >> fargate_b

    # ③ Data: Fargate → RDS PRIMARY (both tasks) + Redis PRIMARY (both tasks)
    fargate_a >> ef("③ SQL + pgvector", RED,   bold=True) >> rds_a
    fargate_b >> ef("③ SQL + pgvector", RED,   bold=True) >> rds_a
    fargate_a >> ef("③ cache r/w",      DKGRN, bold=True) >> redis_a
    fargate_b >> ef("③ cache r/w",      DKGRN, bold=True) >> redis_a

    # ④ PrivateLink: Fargate → VPC Endpoints (stays on AWS backbone, no NAT)
    fargate_a >> ef("④ pull image",     PURPLE, dashed=True) >> ep_ecr
    fargate_b >> ef("④ pull image",     PURPLE, dashed=True) >> ep_ecr
    ep_ecr    >> ef("",                 PURPLE, dashed=True) >> ecr
    fargate_a >> ef("④ secrets",        PURPLE, dashed=True) >> ep_sm
    fargate_b >> ef("④ secrets",        PURPLE, dashed=True) >> ep_sm
    fargate_a >> ef("④ ship logs",      PURPLE, dashed=True) >> ep_cw
    fargate_b >> ef("④ ship logs",      PURPLE, dashed=True) >> ep_cw
    fargate_a >> ef("④ S3 gateway",     PURPLE, dashed=True) >> ep_s3
    fargate_b >> ef("④ S3 gateway",     PURPLE, dashed=True) >> ep_s3
    ep_cw     >> ef("",                 TEAL,   dashed=True) >> cw

    # ⑤ Egress: Fargate (private subnet) → NAT (public subnet) → IGW → internet
    fargate_a >> ef("⑤ LLM / tool calls", ORANGE) >> nat_a
    fargate_b >> ef("⑤ LLM / tool calls", ORANGE) >> nat_b
    nat_a     >> ef("⑤ outbound HTTPS",   ORANGE) >> igw
    nat_b     >> ef("⑤ outbound HTTPS",   ORANGE) >> igw
    igw >> ef("⑤ DeepSeek", ORANGE) >> deepseek
    igw >> ef("⑤ OpenAI",   ORANGE) >> openai
    igw >> ef("⑤ Tavily",   ORANGE) >> tavily
    igw >> ef("⑤ Google",   ORANGE) >> gcal

    # ⑥ Multi-AZ replication
    rds_a >> ef("⑥ synchronous replication (Multi-AZ failover)", RED,   dashed=True) >> rds_b
    redis_a >> Edge(
        label="⑥ async replication\n(replication group)",
        color=DKGRN, style="dashed", constraint="false",
    ) >> redis_b

    # ⑦ Observability: Fargate → CloudWatch alarms → SNS. AWS Budgets is an
    #    independent billing monitor (emails directly; not fed by CloudWatch) —
    #    invisible edge only anchors it in the cluster.
    fargate_a >> ef("⑦ logs + metrics", TEAL, dashed=True) >> cw
    fargate_b >> ef("⑦ logs + metrics", TEAL, dashed=True) >> cw
    cw >> ef("⑦ alarms",  TEAL) >> sns
    cw >> Edge(style="invis") >> budgets


print("✅  aether_aws_architecture.png written")
