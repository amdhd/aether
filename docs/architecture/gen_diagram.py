"""
Aether AWS Architecture Diagram
Run:  uvx --with "graphviz" python3 gen_diagram.py
"""

import graphviz

dot = graphviz.Digraph(
    "Aether",
    filename="aether_aws_architecture",
    format="png",
    engine="dot",
)

dot.attr(
    rankdir="TB",
    bgcolor="white",
    fontname="Helvetica",
    fontsize="14",
    pad="1.0",
    nodesep="0.65",
    ranksep="0.9",
    splines="ortho",
    compound="true",
    newrank="true",
    size="26,18!",
    dpi="150",
)

def node(g, name, label, fillcolor, fontcolor="white"):
    g.node(name, label=label,
           shape="box", style="rounded,filled",
           fillcolor=fillcolor, fontcolor=fontcolor,
           fontname="Helvetica", fontsize="11",
           width="1.65", height="0.82", margin="0.12,0.07")

DNS_C="#8D4FDB"; ACM_C="#DD344C"; CF_C="#8D4FDB"; S3_C="#3F8624"
WAF_C="#DD344C"; IGW_C="#8C4FFF"; NAT_C="#8C4FFF"; ALB_C="#E7157B"
FG_C="#ED7100";  RD_C="#C7131F";  RDS_C="#3F48CC"; ECR_C="#ED7100"
CW_C="#E7157B";  SNS_C="#E7157B"; BUD_C="#3F8624"; EXT_C="#7D7D7D"

BLUE="#1565C0"; ORANGE="#E65100"; GREEN="#2E7D32"; PURPLE="#6A1B9A"
RED="#B71C1C";  DKGRN="#1B5E20"; TEAL="#006064"

def e(src, dst, label="", color="#555", style="solid",
      bold=False, fs="10", constraint="true", w="1"):
    dot.edge(src, dst,
             xlabel=label, color=color, fontcolor=color,
             fontname="Helvetica", fontsize=fs,
             style="bold" if bold else style,
             constraint=constraint, arrowsize="0.75",
             penwidth="1.8" if bold else "1.2",
             weight=w)

# ─────────────────────────────────────────────────────────────────────────────
dot.node("users", "Users", shape="plaintext",
         fontsize="14", fontname="Helvetica Bold")

# ─────────────────────────────────────────────────────────────────────────────
# EDGE & DNS
# ─────────────────────────────────────────────────────────────────────────────
with dot.subgraph(name="cluster_edge") as g:
    g.attr(label="Edge & DNS", style="rounded,filled", fillcolor="#E3F2FD",
           pencolor="#1565C0", fontcolor="#1565C0",
           fontname="Helvetica Bold", fontsize="14", penwidth="2")
    node(g, "dns", "Route 53\nDNS",                   DNS_C)
    node(g, "acm", "ACM\nTLS Certificates",            ACM_C)
    node(g, "waf", "AWS WAF\nRate-limit + Rules",      WAF_C)
    node(g, "cf",  "CloudFront\nSPA · HTTPS",          CF_C)
    node(g, "s3",  "S3  Static SPA\n(private · OAC)",  S3_C)

# ─────────────────────────────────────────────────────────────────────────────
# VPC
# ─────────────────────────────────────────────────────────────────────────────
with dot.subgraph(name="cluster_vpc") as vpc:
    vpc.attr(label="VPC  (2 Availability Zones)", style="rounded,filled",
             fillcolor="#F1F8E9", pencolor="#2E7D32", fontcolor="#1B5E20",
             fontname="Helvetica Bold", fontsize="15", penwidth="3")

    node(vpc, "igw", "Internet\nGateway", IGW_C)
    node(vpc, "ecr", "Amazon ECR\n(scan-on-push)", ECR_C)

    # AZ A
    with vpc.subgraph(name="cluster_az_a") as az:
        az.attr(label="Availability Zone A", style="dashed,rounded",
                fillcolor="#FFFDE7", pencolor="#F57F17", fontcolor="#E65100",
                fontname="Helvetica Bold", fontsize="13", penwidth="2")

        with az.subgraph(name="cluster_pub_a") as sg:
            sg.attr(label="Public Subnet A", style="rounded,filled",
                    fillcolor="#FFF9C4", pencolor="#F9A825",
                    fontname="Helvetica Bold", fontsize="12")
            node(sg, "alb_a", "ALB Node AZ A\nHTTPS 443", ALB_C)
            node(sg, "nat_a", "NAT Gateway A",             NAT_C)

        with az.subgraph(name="cluster_app_a") as sg:
            sg.attr(label="Private App Subnet A", style="rounded,filled",
                    fillcolor="#E8F5E9", pencolor="#388E3C",
                    fontname="Helvetica Bold", fontsize="12")
            node(sg, "fargate_a", "ECS Fargate\nFastAPI · ARM64\nAZ A", FG_C)
            node(sg, "redis_a",   "ElastiCache Redis\nPRIMARY · AZ A",  RD_C)

        with az.subgraph(name="cluster_db_a") as sg:
            sg.attr(label="Private DB Subnet A", style="rounded,filled",
                    fillcolor="#FCE4EC", pencolor="#C62828",
                    fontname="Helvetica Bold", fontsize="12")
            node(sg, "rds_a", "RDS PostgreSQL\n+ pgvector\nPRIMARY · AZ A", RDS_C)

    # AZ B
    with vpc.subgraph(name="cluster_az_b") as az:
        az.attr(label="Availability Zone B", style="dashed,rounded",
                fillcolor="#E8EAF6", pencolor="#283593", fontcolor="#1A237E",
                fontname="Helvetica Bold", fontsize="13", penwidth="2")

        with az.subgraph(name="cluster_pub_b") as sg:
            sg.attr(label="Public Subnet B", style="rounded,filled",
                    fillcolor="#E8EAF6", pencolor="#3949AB",
                    fontname="Helvetica Bold", fontsize="12")
            node(sg, "alb_b", "ALB Node AZ B\nHTTPS 443", ALB_C)
            node(sg, "nat_b", "NAT Gateway B",             NAT_C)

        with az.subgraph(name="cluster_app_b") as sg:
            sg.attr(label="Private App Subnet B", style="rounded,filled",
                    fillcolor="#E8F5E9", pencolor="#388E3C",
                    fontname="Helvetica Bold", fontsize="12")
            node(sg, "fargate_b", "ECS Fargate\nFastAPI · ARM64\nAZ B", FG_C)
            node(sg, "redis_b",   "ElastiCache Redis\nREPLICA · AZ B",  RD_C)

        with az.subgraph(name="cluster_db_b") as sg:
            sg.attr(label="Private DB Subnet B", style="rounded,filled",
                    fillcolor="#FCE4EC", pencolor="#C62828",
                    fontname="Helvetica Bold", fontsize="12")
            node(sg, "rds_b", "RDS PostgreSQL\nSTANDBY · AZ B\n(Multi-AZ)", RDS_C)

# ─────────────────────────────────────────────────────────────────────────────
# OBSERVABILITY
# ─────────────────────────────────────────────────────────────────────────────
with dot.subgraph(name="cluster_obs") as g:
    g.attr(label="Observability & Cost", style="rounded,filled",
           fillcolor="#E0F7FA", pencolor="#00838F", fontcolor="#004D40",
           fontname="Helvetica Bold", fontsize="14", penwidth="2")
    node(g, "cw",      "CloudWatch\nLogs · Metrics\nAlarms · Dashboard", CW_C)
    node(g, "sns",     "SNS\nAlarm Notifications",                        SNS_C)
    node(g, "budgets", "AWS Budgets\nCost Alert",                         BUD_C)

# ─────────────────────────────────────────────────────────────────────────────
# EXTERNAL APIs
# ─────────────────────────────────────────────────────────────────────────────
with dot.subgraph(name="cluster_ext") as g:
    g.attr(label="External APIs  (internet · via NAT → IGW)",
           style="rounded,filled", fillcolor="#FFF3E0",
           pencolor="#E65100", fontcolor="#BF360C",
           fontname="Helvetica Bold", fontsize="14", penwidth="2")
    node(g, "deepseek", "DeepSeek\nLLM",       EXT_C)
    node(g, "openai",   "OpenAI\nEmbeddings",   EXT_C)
    node(g, "tavily",   "Tavily\nWeb Search",   EXT_C)
    node(g, "gcal",     "Google\nCalendar API", EXT_C)

# ─────────────────────────────────────────────────────────────────────────────
# RANK CONSTRAINTS
# Each node in AT MOST ONE rank= group.
# No invisible edge creates a cycle with these rank groups.
# ─────────────────────────────────────────────────────────────────────────────

# ALB + NAT all in the same rank — keeps both inside the public subnet tier
with dot.subgraph() as s:
    s.attr(rank="same")
    s.node("alb_a"); s.node("alb_b")
    s.node("nat_a"); s.node("nat_b")

# Fargate + ECR at same level
with dot.subgraph() as s:
    s.attr(rank="same")
    s.node("fargate_a"); s.node("fargate_b"); s.node("ecr")

# Redis pair
with dot.subgraph() as s:
    s.attr(rank="same"); s.node("redis_a"); s.node("redis_b")

# RDS pair
with dot.subgraph() as s:
    s.attr(rank="same"); s.node("rds_a"); s.node("rds_b")

# AZ A left of AZ B — safe invisible edges (same rank, no vertical conflict)
dot.edge("alb_a",    "alb_b",     style="invis", weight="5")
dot.edge("fargate_a","fargate_b", style="invis", weight="5")
dot.edge("rds_a",    "rds_b",     style="invis", weight="5")

# Tier ordering within AZ A — only downward, no upward edges
# alb → nat already in same rank so skip; just use real flow edges for ordering
# Extra weight on alb→fargate and fargate→rds real edges to strengthen ordering
# (handled via high weight on real flow edges below)

# ─────────────────────────────────────────────────────────────────────────────
# FLOW EDGES
# ─────────────────────────────────────────────────────────────────────────────

# ① SPA load
e("users","dns", "① DNS lookup",        BLUE,   bold=True)
e("dns",  "cf",  "① SPA (HTTPS)",        BLUE,   bold=True)
e("cf",   "s3",  "① OAC · private read", BLUE,   style="dashed")
e("acm",  "cf",  "TLS cert",             PURPLE, style="dashed", fs="9")

# ② API path
e("dns",   "waf",      "② API (HTTPS)",    ORANGE, bold=True)
e("waf",   "alb_a",    "② inspect",        ORANGE, w="3")
e("waf",   "alb_b",    "② inspect",        ORANGE, w="3")
e("acm",   "alb_a",    "TLS cert",         PURPLE, style="dashed", fs="9")
e("acm",   "alb_b",    "TLS cert",         PURPLE, style="dashed", fs="9")
e("igw",   "alb_a",    "inbound",          GREEN,  style="dashed", fs="9")
e("igw",   "alb_b",    "inbound",          GREEN,  style="dashed", fs="9")
# High-weight edges that also enforce Public→App tier ordering
e("alb_a", "fargate_a","② → target group", ORANGE, bold=True, w="8")
e("alb_b", "fargate_b","② → target group", ORANGE, bold=True, w="8")

# ③ Data — high weight enforces App→DB ordering
e("fargate_a","rds_a",   "③ SQL + pgvector", RED,   bold=True, w="8")
e("fargate_b","rds_a",   "③ SQL + pgvector", RED,   bold=True, w="3")
e("fargate_a","redis_a", "③ cache r/w",      DKGRN, bold=True, w="5")
e("fargate_b","redis_a", "③ cache r/w",      DKGRN, bold=True, w="3")

# ④ ECR pull
e("fargate_a","ecr","④ pull image", PURPLE, style="dashed")
e("fargate_b","ecr","④ pull image", PURPLE, style="dashed")

# ⑤ Egress — Private App Subnet → NAT Gateway (Public Subnet) → IGW → Internet
e("fargate_a","nat_a","⑤ LLM / tool calls", ORANGE, constraint="false")
e("fargate_b","nat_b","⑤ LLM / tool calls", ORANGE, constraint="false")
e("nat_a",    "igw",  "⑤ outbound HTTPS",   ORANGE, constraint="false")
e("nat_b",    "igw",  "⑤ outbound HTTPS",   ORANGE, constraint="false")
e("igw","deepseek","⑤ DeepSeek", ORANGE, fs="9")
e("igw","openai",  "⑤ OpenAI",   ORANGE, fs="9")
e("igw","tavily",  "⑤ Tavily",   ORANGE, fs="9")
e("igw","gcal",    "⑤ Google",   ORANGE, fs="9")

# ⑥ Replication
e("rds_a",  "rds_b",
  "⑥ synchronous replication (Multi-AZ failover)", RED,   style="dashed")
e("redis_a","redis_b",
  "⑥ async replication (replication group)",       DKGRN, style="dashed")

# ⑦ Observability
e("fargate_a","cw","⑦ logs + metrics", TEAL, style="dashed", constraint="false")
e("fargate_b","cw","⑦ logs + metrics", TEAL, style="dashed", constraint="false")
e("cw","sns",    "⑦ alarms",  TEAL)
e("cw","budgets","cost alarm", TEAL, style="dashed")

# ─────────────────────────────────────────────────────────────────────────────
dot.render(cleanup=True)
print("✅  aether_aws_architecture.png written")
