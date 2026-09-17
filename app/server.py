#!/usr/bin/env python3
"""
IBM Software Hub & Cloud Pak for Data: Usage Metering, Licensing & Sizing Telemetry Dashboard
------------------------------------------------------------------------------------------
Standalone Carbon-inspired dashboard for customer-facing sizing and license metering reviews.
It treats watsonx.data standard/non-premium as the default installed posture, and keeps Premium
as a separate reference entitlement because IBM publishes distinct License Information documents.
It covers IBM Software Hub / Cloud Pak for Data 5.3 and 5.4 with watsonx 2.3 and 2.4 services:
  - IBM Software Hub / CPD Standard & Enterprise Editions
  - IBM watsonx.data standard/non-premium vs. watsonx.data Premium as separate license rows
  - Common Core Services (CCS) universal foundation
  - WKC, DataStage, and MANTA lineage entitlement boundaries
  - OpenShift Prometheus / Thanos core & memory limits vs. live usage
  - IBM License Service APIs for product, bundle, service, health, status, and audit snapshot data
"""

import os
import sys
import json
import base64
import ssl
import time
import urllib.request
import urllib.parse
import urllib.error
import subprocess
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional, List

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

DEFAULT_PORT = 8088
DEFAULT_HOST = "0.0.0.0"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR_DIR = os.path.join(ROOT_DIR, "app", "vendor")

def _load_env_file(env_path: str):
    """Load key-value pairs from .env into os.environ if not already set."""
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception as e:
        print(f"Notice: Could not parse {env_path}: {e}")

# Automatically look for .env in current working dir and repository root
_load_env_file(os.path.join(os.getcwd(), '.env'))
_load_env_file(os.path.join(ROOT_DIR, '.env'))


# IBM Software Hub & watsonx.data release and licensing reference.
# Keep this conservative: IBM License Service metricName is the source of truth for VPC/RU/etc.
IBM_LICENSE_TERMS_INFO = {
    "release_matrix": {
        "name": "IBM Software Hub / Cloud Pak for Data release matrix",
        "releases": [
            {
                "software_hub": "5.3.x",
                "cloud_pak_for_data": "5.3.x",
                "watsonx": "2.3.x",
                "watsonx_data": "IBM watsonx.data 2.3",
                "watsonx_data_premium": "IBM watsonx.data Premium 2.3",
                "wkc": "IBM Knowledge Catalog; IBM Knowledge Catalog Premium is a separate uplift",
                "datastage": "IBM DataStage Enterprise Cartridge 15.3",
                "lineage": "IBM Manta Data Lineage Cartridge 5.3",
                "source": "https://www.ibm.com/support/pages/license-information-ibm-software-hub-53-and-related-services"
            },
            {
                "software_hub": "5.4.x",
                "cloud_pak_for_data": "5.4.x",
                "watsonx": "2.4.x",
                "watsonx_data": "IBM watsonx.data 2.4",
                "watsonx_data_premium": "IBM watsonx.data Premium 2.4",
                "wkc": "IBM Knowledge Catalog; IBM Knowledge Catalog Premium is a separate uplift",
                "datastage": "IBM DataStage Enterprise Cartridge 15.4",
                "lineage": "IBM Manta Data Lineage Cartridge",
                "source": "https://www.ibm.com/support/pages/license-information-ibm-software-hub-54-and-related-services"
            }
        ]
    },
    "software_hub_positioning": {
        "name": "Software Hub, CPD, and cartridge positioning",
        "metric": "Do not infer metric from marketing name; use IBM License Service product rows",
        "terms_highlights": [
            "IBM support license pages list IBM Software Hub license documents separately from Cloud Pak for Data Standard and Enterprise documents.",
            "The same pages list IBM Software Hub AI Assistant Cartridge and IBM Software Hub Premium Cartridge as Software Hub license documents.",
            "Cloud Pak for Data Standard and Enterprise are separate solution license documents on the same release pages.",
            "Therefore, this dashboard treats Software Hub / Software Hub Premium / CPD Standard / CPD Enterprise as entitlement context, not as a conversion rule for service metrics."
        ],
        "sources": [
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-53-and-related-services",
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-54-and-related-services"
        ]
    },
    "dependency_policy": {
        "name": "Dependency and Common Core Services policy",
        "metric": "Dependencies explain installed pods; products explain license usage",
        "terms_highlights": [
            "Common Core Services has component ID ccs and can be automatically installed as a dependency.",
            "Do not classify CCS itself as Standard or Premium in this dashboard. Classify the parent entitlement/product row instead.",
            "Automatically installed dependencies such as ccs, OpenSearch, Analytics Engine, or DataStage must be shown as attribution context and counted only if IBM License Service reports a product or bundled-product metric row.",
            "This prevents double counting foundational components while still explaining why pods exist in the cluster."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=manage-component-ids",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=requirements-software",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=planning-operator-operand-versions"
        ]
    },
    "knowledge_catalog_positioning": {
        "name": "IBM Knowledge Catalog positioning",
        "metric": "Use the License Service product row: Knowledge Catalog, Standard, or Premium",
        "terms_highlights": [
            "IBM release license pages publish separate Knowledge Catalog Standard and Knowledge Catalog Premium license documents.",
            "The dashboard service card is intentionally labeled IBM Knowledge Catalog unless License Service reports Standard or Premium.",
            "Premium/model-driven features are separate from the base installed service and IBM docs reference enableModelsOn options for model placement.",
            "DataStage pods that appear due to data quality execution should be interpreted as dependency/attribution unless separately licensed."
        ],
        "sources": [
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-53-and-related-services",
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-54-and-related-services",
            "https://www.ibm.com/docs/en/software-hub/5.3.x?topic=catalog-preparing-install",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=services-specifying-installation-options"
        ]
    },
    "watsonx_data_standard": {
        "doc_id": "IBM watsonx.data 2.3 / 2.4 (base doc L-HHZC-6FEVQJ for v2.4)",
        "name": "IBM watsonx.data standard / non-premium",
        "program_name": "IBM watsonx.data",
        "metric": "Use IBM License Service metricName as reported: VIRTUAL_PROCESSOR_CORE (VPC) or RESOURCE_UNIT (RU)",
        "terms_highlights": [
            "Default posture for this dashboard: standard/non-premium watsonx.data. \"A single watsonx.data license is equal to 1 VPC.\"",
            "Do not infer Premium features or Premium license terms from a standard watsonx.data CR.",
            "IBM publishes documented engine conversion ratios — Presto (Java) 1:1, Presto (C++) 1:2, Spark 1:1, base-edition Milvus 3 GPGPU/3VPC/1VPC — read them from IBM License Service's own /bundled_products.metricConversion and metricConvertedQuantity fields rather than re-deriving client-side.",
            "Core (platform services required by every watsonx.data engine) is a FLAT 20 VPC / 20 CPU / 17GB entitlement floor, not a per-engine scaling ratio — do not sum it as if it scaled 1:1 with engine count.",
            "Milvus is licensed in BOTH the base and Premium editions, just metered differently (base: flat VPC-denominated ratio; Premium: RU-pool ratio) — it is not a Premium-exclusive feature; do not state otherwise.",
            "Track CR sizing, pod limits, and live CPU/memory as operational evidence, not as a replacement for IBM License Service audit data."
        ],
        "sources": [
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-53-and-related-services",
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-54-and-related-services",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=new-watsonxdata",
            "https://www.ibm.com/docs/en/watsonxdata/standard/2.1.x?topic=software-licensing-entitlements"
        ]
    },
    "watsonx_data_premium_reference": {
        "doc_id": "L-PCPF-BJV4WW",
        "name": "IBM watsonx.data Premium Edition 2.4.x Update 1 reference only",
        "program_name": "IBM watsonx.data Premium",
        "metric": "Use the License Information document and License Service metricName",
        "terms_highlights": [
            "Premium is a separate IBM offering and must not be assumed for a non-premium installation.",
            "L-PCPF-BJV4WW (Program 5900-BQE) is confirmed Active as of 2026-06-24 via IBM's live CSOL system — its full text confirms the Milvus ratio (6 GPGPU/6VPC/1RU), the Databand/watsonx.data-integration scoping language, and the Prohibited-Components clause (\"Licensee is not authorized to use ... IBM Software Hub Premium and IBM Software Hub AI Assistant features\").",
            "Use Premium references only when the customer entitlement and License Service product row identify Premium."
        ],
        "sources": [
            "https://www.ibm.com/support/customer/csol/terms/?id=L-PCPF-BJV4WW&lc=de",
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-54-and-related-services"
        ]
    },
    "superseded_license_documents": {
        "name": "Known-superseded CSOL document IDs — verify before citing",
        "metric": "IBM's public license-information index pages can lag behind CSOL's live system by months",
        "terms_highlights": [
            "As of 2026-09-17: IBM Software Hub Premium Cartridge V5.4 is L-BFHU-57RB3P (Update 1, published 2026-06-24) — the previously-published L-DDRJ-KNN7UL no longer resolves.",
            "As of 2026-09-17: IBM Software Hub AI Assistant Cartridge V5.4 is L-UMNG-S3KFK8 (Update 1, published 2026-06-24) — the previously-published L-ANNR-GU54BZ no longer resolves.",
            "The canonical 'worked example' PDFs historically cited for VPC/RU definitions — ibm.com/about/software-licensing/assets/guides_pdf/CloudPaks.pdf and .../Container_Licensing.pdf — both return HTTP 404 as of 2026-09-17. Do not cite them; use the containerfaqov/subcaplicensing pages or a live CSOL document instead.",
            "Always re-check a document ID against IBM's live CSOL search (ibm.com/support/customer/csol/terms) before presenting it as current — a document that was correct last quarter can be silently superseded."
        ],
        "sources": [
            "https://www.ibm.com/support/customer/csol/terms/?id=L-BFHU-57RB3P",
            "https://www.ibm.com/support/customer/csol/terms/?id=L-UMNG-S3KFK8",
            "https://www.ibm.com/support/pages/node/7275162"
        ]
    },
    "entitlement_application_and_node_pinning": {
        "name": "Applying entitlements and node pinning (cpd-cli manage apply-entitlement)",
        "metric": "Entitlement application governs which License Service metric rows a component is measured against; node pinning governs which nodes' capacity counts toward which entitlement",
        "terms_highlights": [
            "`cpd-cli manage apply-entitlement --cpd_instance_ns=<ns> --entitlement=<value> [--production=false]` tells License Service which purchased entitlement applies to a component before or after install; run it once per solution you plan to install.",
            "Confirmed current --entitlement values include: cpd-enterprise, cpd-standard, datastage, datastage-plus, ikc-standard, ikc-premium, data-lineage, data-lineage-reserved, watsonx-ai, watsonx-data, watsonx-data-reserved, watsonx-data-premium, watsonx-data-premium-reserved, watsonx-dataintegration, watsonx-dataintegration-reserved, watsonx-dataintelligence / watsonx-dataintelligence-vpc (plus -reserved and -transition variants), watsonx-gov-mm, watsonx-gov-rc, watsonx-orchestrate, cognos-analytics, data-product-hub, openpages, planning-analytics, product-master, watson-discovery, watson-assistant, speech-to-text, text-to-speech, watsonx-code-assistant(-ansible|-z), watsonx-bi-premium(-ca)(-vpc). Append --production=false for non-production entitlements.",
            "Node pinning (a SEPARATE, optional step) uses node affinity so pods for different solutions land on distinct, labeled nodes — label key isc-entitlement with values chargeable-components / non-chargeable-components / chargeable-gpu-components (`oc label node <name> isc-entitlement=chargeable-components`). It is 'optional but strongly recommended if you plan to install multiple solutions in a single instance' — not required for a single-solution instance.",
            "Enforcement has two levels: unenforced (default, preferredDuringSchedulingIgnoredDuringExecution — a soft preference) vs. enforced (requiredDuringSchedulingIgnoredDuringExecution — pods won't schedule off-label).",
            "This dashboard checks live node labels for isc-entitlement and reports whether node pinning is configured — this is a real, checkable compliance-readiness signal, not an inferred one."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=entitlements-applying-your-without-node-pinning",
            "https://www.ibm.com/docs/en/SSNFH6_5.1.x/hub/plan/node-planning.html",
            "https://www.ibm.com/docs/en/software-hub/5.2.x?topic=manage-apply-entitlement"
        ]
    },
    "container_licensing_rules": {
        "name": "IBM container licensing: VPC/RU mechanics and compliance obligations",
        "metric": "Pod vCPU Limit (potential capacity), not runtime usage — measured and reported by IBM License Service",
        "terms_highlights": [
            "\"Generally, 1 VPC = 1 physical core or 1 virtual core.\" \"The vCPU capacity of a pod is the sum of the CPU limits for all containers within that pod\" — capped at worker-node capacity and aggregated cluster-wide. \"IBM licenses containers based on their potential capacity rather than actual usage,\" not live utilization.",
            "\"Any Product that has not been enabled to run and be tracked by IBM License Service will not be eligible for Container Licensing\" — IBM License Service is mandatory, no exceptions, for containerized IBM software on Kubernetes.",
            "Customers must \"generate and maintain for two years, IBM Use Reports each quarter\" — the audit window is a fixed calendar quarter (first day of the first month through the last day of the last month), not a rolling 90-day lookback.",
            "New container-licensing customers get a 90-day grace period from first Eligible Container Product deployment to implement IBM License Service.",
            "Non-compliance consequence: \"Customers who are not in compliance ... will be charged for all cores in the entire cluster\" (full-capacity, not sub-capacity, billing).",
            "Sub-capacity/ILMT has been mandatory since May 10, 2022 for VM-based VPC licensing (phased cutover: new customers Feb 1 2023, existing customers May 1 2023) — container workloads use License Service instead of ILMT for the equivalent obligation; License Service Reporter can roll up both sources (\"ILMT for VMs, License Service for containers\") across a multi-cluster estate."
        ],
        "sources": [
            "https://www.ibm.com/software/passportadvantage/containerfaqov",
            "https://www.ibm.com/software/passportadvantage/subcaplicensing",
            "https://www.ibm.com/docs/en/cloud-paks/foundational-services/4.13.0?topic=license-service-reporter"
        ]
    },
    "license_service_api": {
        "name": "IBM License Service API endpoints used by this dashboard",
        "metric": "Reported per endpoint; products are authoritative for product usage",
        "terms_highlights": [
            "/products and /bundled_products return raw JSON ARRAYS (not {\"products\":[...]}-wrapped objects) of metricName/metricQuantity rows — confirmed against a live 4.2.20 instance; a collector that assumes a wrapper object will silently see zero rows.",
            "metricName is the full ILMT metric code name — VIRTUAL_PROCESSOR_CORE (ILMT metric ID 5844) or RESOURCE_UNIT (ILMT metric ID 10251) — not the short VPC/RU labels this dashboard displays; normalize before aggregating.",
            "/bundled_products additionally reports metricConversion (e.g. \"3:1\") and metricConvertedQuantity per sub-component — consume these directly instead of re-deriving conversion ratios client-side.",
            "/services returns service contribution to products and is useful for drill-down attribution (can legitimately be an empty array).",
            "/status returns an HTML dashboard page, not JSON — link out to it rather than parsing it for data.",
            "/snapshot returns a signed ZIP archive (CSVs + signature.rsa + pub_key.pem + checksum.txt), not JSON — this is IBM's own audit-evidence package; wire it directly into an export action.",
            "Authentication is via a Kubernetes ServiceAccount token in the Authorization header (the exact secret name is cluster/version-specific — confirmed working on this cluster: ibm-licensing-default-reader-token in the ibm-licensing namespace) or a ?token= URL parameter (disable-able by policy)."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/cloud-paks/foundational-services/3.23?topic=pcfls-apis",
            "https://www.ibm.com/docs/en/SSRV9V_4.6/license-service/API_authentication.html",
            "https://www.ibm.com/docs/en/license-metric-tool/9.2.0?topic=v2-metric-ids-code-names"
        ]
    },
    "license_document_cli": {
        "name": "License document retrieval with cpd-cli",
        "metric": "License URL discovery, not runtime usage metering",
        "terms_highlights": [
            "cpd-cli manage get-license returns license URLs for a Software Hub release and can be scoped by component/license type.",
            "Use get-license during planning and entitlement review to confirm which license documents apply.",
            "Use IBM License Service products/bundled_products for live usage quantities.",
            "This dashboard keeps those two evidence streams separate: terms documents explain entitlement, License Service explains measured use.",
            "Caveat: IBM's software-hub doc URLs are not stable across point releases — the 5.3.x apply-entitlement topic returns HTTP 403 today while the equivalent 5.2.x page works; verify each 5.4.x URL individually (or fall back a version) before relying on it."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=manage-get-license",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=planning-licenses-entitlements",
            "https://www.ibm.com/docs/en/software-hub/5.2.x?topic=manage-apply-entitlement"
        ]
    },
    "tracking_reporting_policy": {
        "name": "Tracking and reporting use against license terms",
        "metric": "License Service measures use; apply/remove entitlements when services change",
        "terms_highlights": [
            "IBM states that License Service measures Software Hub use against license terms.",
            "When services are added, apply-entitlement might need to be rerun if the service is not covered by an existing license.",
            "When services are removed, remove-entitlement should only be run when no remaining service uses that license.",
            "The audit/reporting window is a fixed calendar quarter, not a rolling lookback window — see container_licensing_rules.",
            "This dashboard therefore separates selected services, installed dependencies, applied entitlements, and measured License Service output.",
            "Caveat: verify each 5.4.x doc URL individually before relying on it — see license_document_cli's caveat about unstable point-release URLs."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=1-tracking-reporting-use-against-license-terms",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=entitlements-applying-your-without-node-pinning"
        ]
    }
}

INSTALL_OPTIONS_REFERENCE = {
    "install_options_policy": {
        "name": "Software Hub installation options policy",
        "metric": "Install options change deployed capabilities; License Service rows still decide metering",
        "terms_highlights": [
            "Service-specific install settings are passed through an install-options YAML parameter file.",
            "For this repo, watsonx.data uses config/install-options.watsonx-data-no-gpu.yml and WATSONX_DATA_INSTALL_OPTIONS.",
            "Feature toggles such as lite Milvus, Knowledge Catalog data quality, knowledge graph, lineage, and model placement explain installed dependencies.",
            "An enabled option is not itself a license metric; use IBM License Service products/bundled_products for the measured unit."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/software-hub/5.3.x?topic=services-specifying-installation-options",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=services-specifying-installation-options",
            "https://www.ibm.com/docs/en/software-hub/5.3.x?topic=installing-changing-installation-options-services"
        ]
    }
}

INSTALL_OPTIONS_CATALOG = [
    {
        "id": "watsonx_data",
        "label": "watsonx.data",
        "component": "watsonx_data",
        "option": "non_olm.watsonxData.scaleConfig",
        "configured_when": "scaleConfig",
        "configured_value_key": "scaleConfig",
        "default_status": "configured",
        "dependency_impact": "Auto-installs Analytics Engine and platform dependencies. Metric row remains watsonx.data in IBM License Service.",
        "license_boundary": "Standard/non-premium unless License Service reports watsonx.data Premium."
    },
    {
        "id": "lite_milvus",
        "label": "Lite Milvus",
        "component": "watsonx_data",
        "option": "non_olm.watsonxData.enable_lite_milvus",
        "configured_when": "enable_lite_milvus: true",
        "disabled_when": "enable_lite_milvus: false",
        "default_status": "not enabled",
        "dependency_impact": "Milvus/vector capability can be installed with watsonx.data options. GPU-dependent Milvus indexes require GPU planning.",
        "license_boundary": "Do not infer Premium from Milvus presence; verify product row and terms."
    },
    {
        "id": "opensearch",
        "label": "OpenSearch",
        "component": "opencontent_opensearch",
        "option": "dependency",
        "default_status": "dependency",
        "dependency_impact": "Search/indexing support is auto-expanded by platform or parent services in many installs.",
        "license_boundary": "Treat as dependency/supporting program unless License Service reports a billable row."
    },
    {
        "id": "data_quality",
        "label": "Knowledge Catalog data quality",
        "component": "wkc / ikc_*",
        "option": "non_olm.wkc.enableDataQuality",
        "configured_when": "enableDataQuality: true",
        "disabled_when": "enableDataQuality: false",
        "default_status": "TBD",
        "dependency_impact": "Can pull DataStage Enterprise components for quality rule execution.",
        "license_boundary": "If DataStage is only present for data quality, treat it as restricted dependency unless separately licensed."
    },
    {
        "id": "knowledge_graph",
        "label": "Knowledge graph",
        "component": "wkc / ikc_*",
        "option": "non_olm.wkc.enableKnowledgeGraph",
        "configured_when": "enableKnowledgeGraph: true",
        "disabled_when": "enableKnowledgeGraph: false",
        "default_status": "TBD",
        "dependency_impact": "Enables graph-style catalog relationships and may add graph storage/runtime dependencies.",
        "license_boundary": "Confirm entitlement row for Knowledge Catalog, Standard, or Premium before presenting as included."
    },
    {
        "id": "lineage",
        "label": "Data lineage / MANTA",
        "component": "datalineage / mantaflow",
        "option": "COMPONENTS includes datalineage or mantaflow",
        "component_markers": ["datalineage", "mantaflow"],
        "default_status": "TBD",
        "dependency_impact": "Requires a Knowledge Catalog family parent and installs lineage scanners/runtime.",
        "license_boundary": "Track as separate lineage capability and verify License Service row."
    },
    {
        "id": "neo4j",
        "label": "Neo4j / graph backend",
        "component": "TBD",
        "option": "TBD by release/feature",
        "default_status": "TBD",
        "dependency_impact": "Use only if the selected release and install option explicitly require or expose it.",
        "license_boundary": "Do not assume Neo4j entitlement or metering without IBM product/terms confirmation."
    },
    {
        "id": "models",
        "label": "IKC semantic/model features",
        "component": "ikc_premium / model placement",
        "option": "enableModelsOn: gpu | remote | cpu",
        "configured_when": "enableModelsOn:",
        "default_status": "TBD",
        "dependency_impact": "Model-driven catalog features can require GPU, remote watsonx.ai, or CPU placement depending on release and option.",
        "license_boundary": "Treat as Premium/model uplift unless License Service and IBM terms confirm base entitlement."
    }
]

SUPPORTED_SERVICES_CATALOG = [
    {"id": "factsheet", "name": "AI Factsheets", "category": "AI Governance", "notes": "Auto-installed by watsonx.governance enableFactsheet.", "dependencies": ["watsonx_governance"], "default_limit_vpc": 8},
    {"id": "analyticsengine", "name": "Analytics Engine powered by Apache Spark", "category": "Analytics Compute", "notes": "Auto-installed by WKC, watsonx.data, data product, data lineage, and other parents.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "bigsql", "name": "Db2 Big SQL", "category": "Data Management", "notes": "Installable data engine.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "cognos_analytics", "name": "Cognos Analytics", "category": "Business Intelligence", "notes": "Cognos cartridge/service.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "dashboard", "name": "Cognos Dashboards", "category": "Business Intelligence", "notes": "Dashboarding service.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 8},
    {"id": "datagate", "name": "Data Gate", "category": "Data Management", "notes": "Prerequisite: Db2 OLTP or Db2 Warehouse.", "dependencies": ["db2oltp", "db2wh"], "default_limit_vpc": 12},
    {"id": "dp", "name": "Data Privacy", "category": "Governance & Privacy", "notes": "Prerequisite: WKC or Knowledge Catalog Premium; Analytics Engine auto-installed.", "dependencies": ["wkc", "analyticsengine"], "default_limit_vpc": 12},
    {"id": "dataproduct", "name": "Data Product Hub", "category": "Data Products", "notes": "Auto-installs Analytics Engine and Data Refinery.", "dependencies": ["analyticsengine", "datarefinery"], "default_limit_vpc": 12},
    {"id": "datarefinery", "name": "Data Refinery", "category": "Data Preparation", "notes": "Do not usually specify directly; parent services install/upgrade it.", "dependencies": ["ws", "wkc"], "default_limit_vpc": 8},
    {"id": "replication",      "name": "Data Replication",        "category": "Data Integration", "notes": "Bundled in WXD_INTEGRATION and WXD_INTELLIGENCE (RU); standalone requires separate VPC license.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "databand",         "name": "IBM DataBand",            "category": "Data Integration", "notes": "Pipeline observability. Bundled in WXD_INTEGRATION and WXD_INTELLIGENCE (RU); not available as standalone CPD cartridge.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 8},
    {"id": "datastage_ent",    "name": "DataStage Enterprise",    "category": "Data Integration", "notes": "VPC standalone or WKC DQ restricted dependency. Bundled as RU in WXD_INTEGRATION and WXD_INTELLIGENCE — no separate VPC row.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 24},
    {"id": "datastage_ent_plus","name": "DataStage Enterprise Plus","category": "Data Integration","notes": "Plus edition; verify entitlement before presenting.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 32},
    {"id": "dv", "name": "Data Virtualization", "category": "Data Management", "notes": "Auto-installs Db2 Data Management Console.", "dependencies": ["dmc"], "default_limit_vpc": 16},
    {"id": "db2oltp", "name": "Db2", "category": "Database", "notes": "Db2 cartridge.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "dmc", "name": "Db2 Data Management Console", "category": "Database Tools", "notes": "Auto-installed by Data Virtualization; installable standalone.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 8},
    {"id": "db2wh", "name": "Db2 Warehouse", "category": "Data Warehouse", "notes": "Db2 Warehouse cartridge.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 24},
    {"id": "dods", "name": "Decision Optimization", "category": "AI & Optimization", "notes": "Prerequisite: Watson Machine Learning and Watson Studio.", "dependencies": ["wml", "ws"], "default_limit_vpc": 12},
    {"id": "edb_cp4d", "name": "EDB Postgres", "category": "Database", "notes": "PostgreSQL dependency auto-expanded.", "dependencies": ["postgresql"], "default_limit_vpc": 12},
    {"id": "hee", "name": "Execution Engine for Apache Hadoop", "category": "Analytics Compute", "notes": "Prerequisite: Watson Studio.", "dependencies": ["ws"], "default_limit_vpc": 16},
    {"id": "wkc", "name": "IBM Knowledge Catalog", "category": "Governance & Quality", "notes": "Auto-installs Analytics Engine and Data Refinery; DataStage only when data quality is enabled.", "dependencies": ["analyticsengine", "datarefinery"], "default_limit_vpc": 28},
    {"id": "ikc_premium", "name": "IBM Knowledge Catalog Premium", "category": "Governance & Quality", "notes": "Premium uplift; verify entitlement and model/GPU options.", "dependencies": ["analyticsengine", "datarefinery"], "default_limit_vpc": 32},
    {"id": "ikc_standard", "name": "IBM Knowledge Catalog Standard", "category": "Governance & Quality", "notes": "Standard cartridge; verify entitlement row before presenting.", "dependencies": ["analyticsengine", "datarefinery"], "default_limit_vpc": 28},
    {"id": "datalineage", "name": "IBM Manta Data Lineage", "category": "Metadata & Lineage", "notes": "Prerequisite: WKC, IKC Premium, or IKC Standard.", "dependencies": ["wkc"], "default_limit_vpc": 16},
    {"id": "match360", "name": "IBM Master Data Management", "category": "Master Data", "notes": "MDM service.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "streamsets",       "name": "IBM StreamSets",          "category": "Data Integration", "notes": "Data collector/transformer. Bundled in WXD_INTEGRATION and WXD_INTELLIGENCE (RU); standalone VPC license also available.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "ibm-streamsets-sdi","name": "IBM StreamSets SDI",     "category": "Data Integration", "notes": "StreamSets SDI component.", "dependencies": ["streamsets"], "default_limit_vpc": 12},
    {"id": "informix_cp4d", "name": "Informix", "category": "Database", "notes": "Informix dependency auto-expanded.", "dependencies": ["informix"], "default_limit_vpc": 12},
    {"id": "mantaflow", "name": "MANTA Automated Data Lineage", "category": "Metadata & Lineage", "notes": "Prerequisite: WKC or IKC family service.", "dependencies": ["wkc"], "default_limit_vpc": 16},
    {"id": "mongodb_cp4d", "name": "MongoDB", "category": "Database", "notes": "MongoDB dependency auto-expanded.", "dependencies": ["mongodb"], "default_limit_vpc": 12},
    {"id": "openpages", "name": "OpenPages", "category": "Governance Risk Compliance", "notes": "Auto-installed by watsonx.governance enableOpenpages.", "dependencies": ["watsonx_governance"], "default_limit_vpc": 16},
    {"id": "ws_pipelines", "name": "Orchestration Pipelines", "category": "Automation", "notes": "Pipeline orchestration service.", "dependencies": ["ws"], "default_limit_vpc": 8},
    {"id": "planning_analytics", "name": "Planning Analytics", "category": "Planning", "notes": "Planning Analytics cartridge.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "productmaster", "name": "Product Master", "category": "Master Data", "notes": "Product Master cartridge.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "rstudio", "name": "RStudio Server Runtimes", "category": "Data Science", "notes": "Prerequisite: Watson Studio.", "dependencies": ["ws"], "default_limit_vpc": 8},
    {"id": "spss", "name": "SPSS Modeler", "category": "Data Science", "notes": "Prerequisite: Watson Studio.", "dependencies": ["ws"], "default_limit_vpc": 12},
    {"id": "syntheticdata", "name": "Synthetic Data Generator", "category": "AI & Data", "notes": "Prerequisite: watsonx.ai.", "dependencies": ["watsonx_ai"], "default_limit_vpc": 12},
    {"id": "udp", "name": "Unstructured Data Integration", "category": "Gen AI Data", "notes": "Prerequisite: watsonx.ai, watsonx.data, watsonx.data intelligence; auto by watsonx.data Premium.", "dependencies": ["watsonx_ai", "watsonx_data", "watsonx_dataintelligence"], "default_limit_vpc": 16},
    {"id": "voice_gateway", "name": "Voice Gateway", "category": "Voice", "notes": "Prerequisite: watson Assistant and Watson Speech.", "dependencies": ["watson_assistant", "watson_speech"], "default_limit_vpc": 8},
    {"id": "watson_discovery", "name": "Watson Discovery", "category": "Search & Discovery", "notes": "Watson Discovery cartridge.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "wml", "name": "Watson Machine Learning", "category": "AI & ML", "notes": "Deep Learning needs scheduler; AutoAI builder needs Watson Studio.", "dependencies": ["scheduler", "ws"], "default_limit_vpc": 16},
    {"id": "openscale", "name": "Watson OpenScale", "category": "AI Governance", "notes": "Prerequisite: external DB or Db2/EDB; auto by watsonx.governance.", "dependencies": ["db2oltp", "edb_cp4d"], "default_limit_vpc": 12},
    {"id": "watson_speech", "name": "Watson Speech services", "category": "Speech", "notes": "Watson Speech services.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "ws", "name": "Watson Studio", "category": "Data Science", "notes": "Auto-installs Data Refinery and default Watson Studio runtimes.", "dependencies": ["datarefinery", "ws_runtimes"], "default_limit_vpc": 16},
    {"id": "ws_runtimes", "name": "Watson Studio Runtimes", "category": "Data Science", "notes": "Fresh install: normally do not specify directly; default runtime is auto-installed.", "dependencies": ["ws"], "default_limit_vpc": 12},
    {"id": "watsonx_ai", "name": "watsonx.ai", "category": "AI & ML", "notes": "Auto-installs Watson Studio and Watson Machine Learning.", "dependencies": ["ws", "wml"], "default_limit_vpc": 24},
    {"id": "model_gateway", "name": "watsonx.ai model gateway", "category": "AI Gateway", "notes": "Alternative/companion to watsonx.ai for remote or third-party models.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 8},
    {"id": "watson_assistant", "name": "watsonx Assistant", "category": "Assistant", "notes": "Assistant service.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 16},
    {"id": "watsonx_bi_assistant", "name": "watsonx BI", "category": "Business Intelligence", "notes": "Prerequisite: watsonx.data intelligence.", "dependencies": ["watsonx_dataintelligence"], "default_limit_vpc": 12},
    {"id": "wca", "name": "watsonx Code Assistant", "category": "Code Assistant", "notes": "watsonx Code Assistant software.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 12},
    {"id": "wca_ansible", "name": "watsonx Code Assistant for Ansible Lightspeed", "category": "Code Assistant", "notes": "Auto-installs watsonx.ai.", "dependencies": ["watsonx_ai"], "default_limit_vpc": 12},
    {"id": "wca_z", "name": "watsonx Code Assistant for Z", "category": "Code Assistant", "notes": "Prerequisite for Z agentic components.", "dependencies": ["ccs", "zen"], "default_limit_vpc": 12},
    {"id": "wca_z_agentic", "name": "watsonx Code Assistant for Z Agentic", "category": "Code Assistant", "notes": "Prerequisite: wca_z. Auto-installs code explanation/generation.", "dependencies": ["wca_z", "wca_z_ce", "wca_z_cg"], "default_limit_vpc": 12},
    {"id": "wca_z_ce", "name": "watsonx Code Assistant for Z Code Explanation", "category": "Code Assistant", "notes": "Z code explanation component.", "dependencies": ["wca_z"], "default_limit_vpc": 8},
    {"id": "wca_z_cg", "name": "watsonx Code Assistant for Z Code Generation", "category": "Code Assistant", "notes": "Z code generation component.", "dependencies": ["wca_z"], "default_limit_vpc": 8},
    {"id": "wca_z_understand", "name": "watsonx Code Assistant for Z Understand", "category": "Code Assistant", "notes": "Z understand component.", "dependencies": ["wca_z"], "default_limit_vpc": 8},
    {"id": "watsonx_data",           "name": "watsonx.data (Lakehouse)",      "category": "Lakehouse",        "notes": "Base lakehouse: Presto, Iceberg, MinIO/COS, OpenSearch. Auto-installs Analytics Engine. RU metric.", "dependencies": ["analyticsengine", "opensearch", "ccs", "zen"], "default_limit_vpc": 48},
    {"id": "watsonx_data_premium",   "name": "watsonx.data Premium",          "category": "Lakehouse",        "notes": "Adds Milvus vector DB to Lakehouse. Governed by L-PCPF-BJV4WW. Does NOT include DataStage or IKC — those are in WXD_INTEGRATION/WXD_INTELLIGENCE.", "dependencies": ["watsonx_data"], "default_limit_vpc": 56},
    {"id": "watsonx_dataintegration","name": "watsonx.data Integration",      "category": "Data Integration", "notes": "Superset of Lakehouse. Bundles DataStage, Data Replication, DataBand, StreamSets as RU — no separate VPC rows for these components.", "dependencies": ["watsonx_data", "datastage_ent", "replication", "databand", "streamsets"], "default_limit_vpc": 72},
    {"id": "watsonx_dataintelligence","name": "watsonx.data Intelligence",    "category": "Data Intelligence", "notes": "Superset of Integration. Full Data Fabric stack: adds IKC/WKC, IKC Premium, MANTA Lineage, Analytics Engine, Data Product Hub — all bundled as RU.", "dependencies": ["watsonx_dataintegration", "wkc", "ikc_premium", "datalineage", "analyticsengine", "dataproduct"], "default_limit_vpc": 96},
    {"id": "watsonx_governance", "name": "watsonx.governance", "category": "AI Governance", "notes": "Auto-installs AI Factsheets, OpenPages, WML, and OpenScale.", "dependencies": ["factsheet", "openpages", "wml", "openscale"], "default_limit_vpc": 24},
    {"id": "watsonx_orchestrate", "name": "watsonx Orchestrate", "category": "Automation", "notes": "Auto-installs watson Assistant and watsonx.data.", "dependencies": ["watson_assistant", "watsonx_data"], "default_limit_vpc": 24}
]

DEPENDENCY_EXPLORER_CATALOG = {
    "nodes": [
        # Platform foundation
        {"id": "software_hub",           "label": "IBM Software Hub / CPD",          "type": "platform",                    "metering": "Entitlement context; not a conversion rule",                                          "certainty": "IBM license pages"},
        {"id": "license_service",        "label": "IBM License Service",              "type": "metering",                    "metering": "Authoritative product metric rows",                                                   "certainty": "IBM API docs"},
        {"id": "ccs",                    "label": "Common Core Services",             "type": "platform_dependency",         "metering": "Foundational service; attribute to parent products unless reported",                   "certainty": "IBM component dependency context"},
        {"id": "zen",                    "label": "Software Hub Control Plane",       "type": "platform_dependency",         "metering": "Platform foundation; not a product conversion rule",                                  "certainty": "IBM component dependency context"},
        {"id": "opensearch",             "label": "OpenSearch",                       "type": "platform_dependency",         "metering": "Supporting dependency unless reported",                                               "certainty": "platform dependency context"},
        # watsonx.data editions (4 distinct PA entitlements)
        {"id": "watsonx_data",           "label": "watsonx.data (Lakehouse)",         "type": "core_product",                "metering": "RU — base lakehouse (Presto, Iceberg, MinIO, OpenSearch)",                            "certainty": "IBM release license pages"},
        {"id": "watsonx_data_premium",   "label": "watsonx.data Premium",             "type": "premium_reference",           "metering": "RU (higher tier) — adds Milvus vector DB; governed by L-PCPF-BJV4WW",                "certainty": "IBM Premium LI reference"},
        {"id": "wxd_integration",        "label": "watsonx.data Integration",         "type": "core_product",                "metering": "RU — bundles DataStage + Replication + DataBand + StreamSets; no separate VPC rows",   "certainty": "IBM PA edition / release pages"},
        {"id": "watsonx_dataintelligence","label": "watsonx.data Intelligence",        "type": "core_product",                "metering": "RU — full Data Fabric: Integration bundle + IKC + MANTA + Spark + Data Product Hub",   "certainty": "IBM component ID pages / release pages"},
        # Lakehouse options
        {"id": "lite_milvus",            "label": "Lite Milvus",                      "type": "optional_install_option",     "metering": "Do not infer Premium from Milvus presence alone",                                    "certainty": "install-options YAML / IBM install options"},
        {"id": "analyticsengine",        "label": "Analytics Engine (Spark)",         "type": "dependency",                  "metering": "VPC standalone; RU when bundled in WXD_INTELLIGENCE; count only if License Service reports row", "certainty": "component dependency table"},
        # WXD_INTEGRATION bundled components (metered as RU, no VPC rows)
        {"id": "datastage_ent",          "label": "DataStage Enterprise",             "type": "dependency_or_product",       "metering": "VPC standalone or WKC DQ; RU when bundled in WXD_INTEGRATION/WXD_INTELLIGENCE",       "certainty": "IBM component catalog + PA edition context"},
        {"id": "replication",            "label": "Data Replication",                 "type": "integration_component",       "metering": "Bundled RU in WXD_INTEGRATION/WXD_INTELLIGENCE; VPC if standalone",                    "certainty": "IBM PA edition / release pages"},
        {"id": "databand",               "label": "IBM DataBand (Pipeline Observability)", "type": "integration_component",  "metering": "Bundled RU in WXD_INTEGRATION/WXD_INTELLIGENCE; pipeline observability",               "certainty": "IBM PA edition / release pages"},
        {"id": "streamsets",             "label": "IBM StreamSets",                   "type": "integration_component",       "metering": "Bundled RU in WXD_INTEGRATION/WXD_INTELLIGENCE; data collector/transformer",            "certainty": "IBM PA edition / release pages"},
        # WXD_INTELLIGENCE additional bundled components
        {"id": "wkc",                    "label": "IBM Knowledge Catalog",            "type": "core_product",                "metering": "VPC standalone; RU when bundled in WXD_INTELLIGENCE; Standard/Premium per License Service", "certainty": "IBM license pages"},
        {"id": "ikc_premium",            "label": "IBM Knowledge Catalog Premium",    "type": "premium_reference",           "metering": "VPC standalone Premium uplift; RU when bundled in WXD_INTELLIGENCE",                   "certainty": "IBM license pages"},
        {"id": "datalineage",            "label": "Data Lineage / MANTA",             "type": "integrated_add_on",           "metering": "VPC standalone; RU when bundled in WXD_INTELLIGENCE",                                 "certainty": "IBM component ID pages / license pages"},
        {"id": "dataproduct",            "label": "Data Product Hub",                 "type": "integrated_add_on_dependency","metering": "Bundled in WXD_INTELLIGENCE; count only if reported standalone",                       "certainty": "component dependency context"},
        # WKC install options
        {"id": "data_quality",           "label": "Data Quality (WKC option)",        "type": "optional_install_option",     "metering": "Can pull DataStage; not standalone unless separately entitled",                        "certainty": "IBM install option name + local repo template"},
        {"id": "knowledge_graph",        "label": "Knowledge Graph (WKC option)",     "type": "optional_install_option",     "metering": "Catalog option; confirm entitlement/product row",                                    "certainty": "IBM install option name + local repo template"},
        # Premium / Gen AI add-ons
        {"id": "udp",                    "label": "Unstructured Data Integration",    "type": "genai_data_add_on",           "metering": "Separate add-on; requires watsonx.ai + watsonx.data intelligence",                    "certainty": "component dependency context"},
        {"id": "watsonx_ai",             "label": "watsonx.ai",                       "type": "separate_product",            "metering": "Separate service/product row (VPC)",                                                  "certainty": "component dependency context"},
    ],
    "edges": [
        # Platform backbone
        {"from": "software_hub",          "to": "license_service",       "relationship": "measured_by",             "license_boundary": "License Service measures use against license terms."},
        {"from": "software_hub",          "to": "ccs",                   "relationship": "platform_dependency",     "license_boundary": "All Software Hub services share Common Core Services as foundation."},
        {"from": "software_hub",          "to": "zen",                   "relationship": "platform_dependency",     "license_boundary": "Zen provides the Software Hub control plane and shared UI."},
        {"from": "ccs",                   "to": "zen",                   "relationship": "foundation_dependency",   "license_boundary": "Common Core Services are part of the shared platform foundation."},
        # watsonx.data Lakehouse (base)
        {"from": "software_hub",          "to": "watsonx_data",          "relationship": "deploys_product",         "license_boundary": "Standard/non-premium by default; RU metric."},
        {"from": "watsonx_data",          "to": "opensearch",            "relationship": "platform_dependency",     "license_boundary": "Supporting service for metadata/audit; avoid double counting."},
        {"from": "watsonx_data",          "to": "analyticsengine",       "relationship": "dependency",              "license_boundary": "Operational dependency; count only if License Service reports a row."},
        {"from": "watsonx_data",          "to": "ccs",                   "relationship": "platform_dependency",     "license_boundary": "watsonx.data uses Common Core Services as a shared platform dependency."},
        {"from": "watsonx_data",          "to": "zen",                   "relationship": "platform_dependency",     "license_boundary": "watsonx.data runs inside the Software Hub control plane."},
        {"from": "watsonx_data",          "to": "lite_milvus",           "relationship": "optional_install_option", "license_boundary": "Vector/Milvus option is not Premium evidence by itself."},
        # watsonx.data Premium (Milvus tier — does NOT include DataStage or IKC)
        {"from": "watsonx_data",          "to": "watsonx_data_premium",  "relationship": "edition_uplift",          "license_boundary": "Premium adds Milvus vector DB; governed by L-PCPF-BJV4WW. Does NOT bundle DataStage or IKC."},
        # watsonx.data Integration (DataStage + Replication + DataBand + StreamSets bundled as RU)
        {"from": "watsonx_data",          "to": "wxd_integration",       "relationship": "edition_uplift",          "license_boundary": "Integration edition bundles ETL/replication stack as RU; no separate VPC rows for bundled components."},
        {"from": "wxd_integration",       "to": "datastage_ent",         "relationship": "bundled_as_ru",           "license_boundary": "DataStage bundled in WXD_INTEGRATION — RU metric, no standalone VPC row."},
        {"from": "wxd_integration",       "to": "replication",           "relationship": "bundled_as_ru",           "license_boundary": "Data Replication bundled in WXD_INTEGRATION — RU metric."},
        {"from": "wxd_integration",       "to": "databand",              "relationship": "bundled_as_ru",           "license_boundary": "DataBand pipeline observability bundled in WXD_INTEGRATION — RU metric."},
        {"from": "wxd_integration",       "to": "streamsets",            "relationship": "bundled_as_ru",           "license_boundary": "StreamSets bundled in WXD_INTEGRATION — RU metric."},
        # watsonx.data Intelligence (superset of Integration, adds Data Fabric governance stack)
        {"from": "wxd_integration",       "to": "watsonx_dataintelligence", "relationship": "edition_uplift",       "license_boundary": "Intelligence is a superset of Integration; adds IKC, MANTA, Spark, Data Product Hub."},
        {"from": "watsonx_dataintelligence", "to": "wkc",               "relationship": "bundled_as_ru",           "license_boundary": "IKC/WKC bundled in WXD_INTELLIGENCE — RU metric, no separate VPC row."},
        {"from": "watsonx_dataintelligence", "to": "ikc_premium",       "relationship": "bundled_as_ru",           "license_boundary": "IKC Premium bundled in WXD_INTELLIGENCE — RU metric."},
        {"from": "watsonx_dataintelligence", "to": "datalineage",       "relationship": "bundled_as_ru",           "license_boundary": "MANTA Lineage bundled in WXD_INTELLIGENCE — RU metric, no separate VPC row."},
        {"from": "watsonx_dataintelligence", "to": "analyticsengine",   "relationship": "bundled_as_ru",           "license_boundary": "Analytics Engine bundled in WXD_INTELLIGENCE — RU metric."},
        {"from": "watsonx_dataintelligence", "to": "dataproduct",       "relationship": "bundled_as_ru",           "license_boundary": "Data Product Hub bundled in WXD_INTELLIGENCE — RU metric."},
        # Standalone DataStage — deployed directly under Software Hub as a VPC product
        {"from": "software_hub",          "to": "datastage_ent",         "relationship": "deploys_product",         "license_boundary": "Standalone DataStage Enterprise Cartridge → VPC. Used for ETL/ELT pipelines independently of WKC or watsonx.data editions."},
        # Standalone WKC (VPC-based, not bundled)
        {"from": "software_hub",          "to": "wkc",                   "relationship": "deploys_product",         "license_boundary": "Standalone WKC → VPC. Do not double-count if already covered by WXD_INTELLIGENCE."},
        {"from": "wkc",                   "to": "data_quality",          "relationship": "optional_install_option", "license_boundary": "enableDataQuality: true pulls DataStage as restricted DQ dependency."},
        {"from": "wkc",                   "to": "datastage_ent",         "relationship": "optional_install_option", "license_boundary": "DataStage auto-installed when enableDataQuality: true — restricted to DQ workloads unless separately licensed for standalone ETL."},
        {"from": "wkc",                   "to": "knowledge_graph",       "relationship": "optional_install_option", "license_boundary": "Catalog graph option; confirm entitlement and product row."},
        {"from": "data_quality",          "to": "datastage_ent",         "relationship": "restricted_dependency",   "license_boundary": "DataStage present for WKC DQ only — not a standalone ETL license."},
        {"from": "wkc",                   "to": "datalineage",           "relationship": "optional_add_on",         "license_boundary": "Standalone MANTA requires WKC/IKC family parent; separate VPC row."},
        # Analytics Engine shared dependency
        {"from": "analyticsengine",       "to": "ccs",                   "relationship": "platform_dependency",     "license_boundary": "Spark/Analytics Engine relies on shared platform services."},
        {"from": "analyticsengine",       "to": "zen",                   "relationship": "platform_dependency",     "license_boundary": "Spark/Analytics Engine is operated through the Software Hub control plane."},
        # Premium Gen AI stack
        {"from": "watsonx_data_premium",  "to": "udp",                   "relationship": "premium_reference_stack", "license_boundary": "UDP requires watsonx.ai + watsonx.data intelligence; do not attach to standard lakehouse."},
        {"from": "udp",                   "to": "watsonx_ai",            "relationship": "genai_dependency_context","license_boundary": "watsonx.ai is a separate VPC-metered service/product."},
        {"from": "udp",                   "to": "watsonx_dataintelligence","relationship": "genai_dependency_context","license_boundary": "UDP also requires watsonx.data intelligence stack."},
    ],
    "sources": [
        "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=services-specifying-installation-options",
        "https://www.ibm.com/docs/en/software-hub/5.3.x?topic=services-specifying-installation-options",
        "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=planning-determining-which-components-install",
        "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=manage-component-ids",
        "https://www.ibm.com/support/customer/csol/terms/?id=L-PCPF-BJV4WW&lc=de"
    ]
}

# Catalog of services with IBM licensing terms, dependencies, and CRD specs
KNOWN_SERVICES_CATALOG = {
    "watsonx_data": {
        "name": "IBM watsonx.data",
        "cr_kind": "Wxd",
        "cr_group": "watsonxdata.ibm.com",
        "cr_plural": "wxds.watsonxdata.ibm.com",
        "sibling_crds": ["wxdengines.watsonxdata.ibm.com (kind WxdEngine — Presto/Milvus/Spark engine instances)", "wxdaddons.watsonxdata.ibm.com (kind WxdAddon)"],
        "verified": "Confirmed live via `oc get crd` on a running Software Hub 5.4 cluster, 2026-09-17. Not documented in any public IBM CRD reference found; re-verify per cluster/version.",
        "category": "Lakehouse & Query Engine",
        "tier": "Core Service",
        "edition_options": ["Standard / non-premium", "Premium only when IBM entitlement says Premium"],
        "license_rule": "Default dashboard assumption: watsonx.data standard/non-premium. IBM publishes documented engine conversion ratios (Presto Java 1:1, Presto C++ 1:2, Spark 1:1, Milvus base 3 GPGPU/3VPC/1VPC, Milvus Premium 6 GPGPU/6VPC/1RU) — read IBM License Service's own /bundled_products.metricConversion and metricConvertedQuantity rather than re-deriving these client-side. Core (platform services required by every engine) is a flat 20 VPC / 20 CPU / 17GB entitlement floor, not a scaling per-unit consumer — do not sum it as if it scaled 1:1 with engine count. Do not label this Premium unless the installed entitlement row says Premium.",
        "dependencies": ["ccs", "zen", "opencontent_opensearch", "analyticsengine"],
        "default_limit_vpc": 48,
        "default_scale": "small_mincpureq",
        "pod_regex": "ibm-lh-.*|ibm-lh-postgres-.*|lhconsole-.*|lhingest-.*|milvus[0-9]*-.*|presto[0-9]*-.*",
        "desc": "Open lakehouse service (Presto/Iceberg query engines, MinIO/COS, optional Milvus vector DB). CR sizing and pod limits are operational indicators; IBM License Service is the metering source."
    },
    "wkc": {
        "name": "IBM Knowledge Catalog (WKC)",
        "cr_kind": "WKC",
        "cr_group": "wkc.cpd.ibm.com",
        "cr_plural": "wkc.wkc.cpd.ibm.com",
        "sub_components": ["Knowledgegraph", "DataQuality", "Enrichment", "Finley", "Glossary", "MetadataImports", "Policy", "Profiling", "Workflow", "Wkcgovui"],
        "verified": "Group confirmed live via `oc get crd`, 2026-09-17. Note: the CRD's plural repeats the group (wkc.wkc.cpd.ibm.com) — `oc get wkc.cpd.ibm.com` fails, must be `oc get wkc.wkc.cpd.ibm.com`. WKC installs 10 further CRDs under the same wkc.cpd.ibm.com/v1beta1 group, each its own CR instance, all rolled into one WKC license row.",
        "category": "Governance & Quality",
        "tier": "Core Service",
        "edition_options": ["Knowledge Catalog", "Knowledge Catalog Standard", "Knowledge Catalog Premium only when explicitly entitled"],
        "license_rule": "Display the installed service as IBM Knowledge Catalog unless IBM License Service reports a Standard or Premium cartridge product row. Premium/model-driven capabilities are separate and involve enableModelsOn: gpu/remote/cpu options. DataStage is only a dependency when enableDataQuality: true — do not list it as an unconditional dependency. VPC standalone; RU only when bundled in an edition with WXD_EDITION=WXD_INTELLIGENCE (no separate VPC row then).",
        "dependencies": ["ccs", "zen", "opencontent_opensearch"],
        "conditional_dependencies": {"enableDataQuality": "datastage"},
        "default_limit_vpc": 28,
        "default_scale": "small",
        "pod_regex": "wkc-.*|wdp-.*|metadata-discovery-.*|knowledge-accelerators-.*|finley-public-.*|ikc-.*-postgres-.*",
        "desc": "Automated data discovery, data quality evaluation rules, policy enforcement, and business glossary."
    },
    "datastage": {
        "name": "IBM DataStage Enterprise Cartridge",
        "cr_kind": "DataStage",
        "cr_group": "ds.cpd.ibm.com",
        "cr_plural": "datastages.ds.cpd.ibm.com",
        "sibling_crds": ["pxruntimes.ds.cpd.ibm.com (kind PXRuntime — the compute-heavy parallel engine, tracked separately from the DataStage CR itself)"],
        "verified": "Group confirmed both live (`oc get crd`) and in IBM docs: https://www.ibm.com/docs/en/software-hub/5.1.x?topic=troubleshooting-datastage shows apiVersion ds.cpd.ibm.com/v1, kind PXRuntime.",
        "category": "Data Integration",
        "tier": "Core / Cartridge",
        "edition_options": [
            "Standalone Enterprise Cartridge (VPC)",
            "WKC Data Quality Bundle — restricted to DQ workloads (VPC, no standalone ETL)",
            "WXD_INTEGRATION bundle — metered as RU, no separate VPC row",
            "WXD_INTELLIGENCE bundle — metered as RU, no separate VPC row"
        ],
        "license_rule": "Metric depends on deployment context: (1) Standalone Enterprise Cartridge → VPC (--entitlement=datastage per cpd-cli manage apply-entitlement). (2) WKC data quality only → VPC but restricted to DQ workloads; not a standalone ETL license. (3) Bundled in watsonx.data integration or intelligence (WXD_EDITION=WXD_INTEGRATION/WXD_INTELLIGENCE) → RU, no separate VPC row emitted by License Service. Check WXD_EDITION and the License Service metricName before displaying.",
        "dependencies": ["ccs", "zen"],
        "default_limit_vpc": 24,
        "default_scale": "small",
        "pod_regex": "datastage-ibm-datastage-.*|ds-px-.*-ibm-datastage-.*|.*-ibm-datastage-px-(compute|runtime)-.*",
        "desc": "Parallel data pipelines, ETL transformation runtime, and high-performance PX compute cluster."
    },
    "lineage": {
        "name": "IBM MANTA Automated Data Lineage",
        "cr_kind": "MantaDataLineage",
        "cr_group": "manta.cpd.ibm.com",
        "category": "Metadata & Lineage",
        "tier": "Add-on Cartridge",
        "edition_options": [
            "Standalone (VPC) — requires WKC, IKC Standard, or IKC Premium",
            "WXD_INTELLIGENCE bundle — metered as RU, no separate VPC row"
        ],
        "license_rule": "Standalone MANTA/Data Lineage → VPC; requires a Knowledge Catalog family parent (WKC, IKC Standard, or IKC Premium). When bundled in watsonx.data intelligence (WXD_EDITION=WXD_INTELLIGENCE) → RU, no separate VPC row. Use License Service metricName as the authority. Note: WKC also ships its own built-in lineage view (wdp-lineage pod, part of the WKC pod set) — that is a distinct capability from this standalone MANTA/DataLineage CR; do not conflate the two when attributing pods.",
        "dependencies": ["ccs", "zen"],
        "optional_parent": ["wkc", "ikc_standard", "ikc_premium"],
        "default_limit_vpc": 16,
        "default_scale": "small",
        "cr_group": "cpd.ibm.com",
        "cr_kind": "DataLineage",
        "cr_plural": "datalineage.cpd.ibm.com",
        "verified": "Confirmed live via `oc get crd`, 2026-09-17 (apiVersion cpd.ibm.com/v1beta1, instance name datalineage-cr). Not corroborated by a public IBM doc found in this pass.",
        "pod_regex": "lineage-scanner-.*|lineage-service-.*|lineage-worker-.*|lineage-ui-.*",
        "desc": "End-to-end automated graph parsing, scanner jobs, and column-level historical data lineage."
    },
    "analyticsengine": {
        "name": "IBM Analytics Engine (Apache Spark)",
        "cr_kind": "AnalyticsEngine",
        "cr_group": "ae.cpd.ibm.com",
        "cr_plural": "analyticsengines.ae.cpd.ibm.com",
        "verified": "Group confirmed live via `oc get crd`, 2026-09-17 (server.py previously guessed analyticsengine.cpd.ibm.com — wrong group).",
        "category": "Analytics Compute",
        "tier": "Compute Engine",
        "edition_options": [
            "Serverless Spark — standalone (VPC)",
            "WXD_INTELLIGENCE bundle — metered as RU, no separate VPC row"
        ],
        "license_rule": "Standalone Analytics Engine → VPC. Auto-installed as dependency of WKC, watsonx.data, Data Product Hub, and others. When bundled in watsonx.data intelligence (WXD_EDITION=WXD_INTELLIGENCE) → RU. Count only if IBM License Service reports a product or bundled-product row; otherwise treat as operational dependency. IBM License Service /bundled_products reports Spark's native RESOURCE_UNIT measurement converted 1:1 to VIRTUAL_PROCESSOR_CORE.",
        "dependencies": ["ccs", "zen"],
        "default_limit_vpc": 16,
        "default_scale": "small",
        "pod_regex": "spark-hb-.*",
        "desc": "Serverless Apache Spark kernel pools and distributed compute executor instances."
    },
    "ccs": {
        "name": "Common Core Services (CCS)",
        "cr_kind": "CCS",
        "cr_group": "ccs.cpd.ibm.com",
        "cr_plural": "ccs.ccs.cpd.ibm.com",
        "verified": "Group confirmed live via `oc get crd`, 2026-09-17; kind corrected from a previously-guessed 'CommonCoreServices' to the real 'CCS'. Plural repeats the group (ccs.ccs.cpd.ibm.com) — `oc get ccs.cpd.ibm.com` fails, must be `oc get ccs.ccs.cpd.ibm.com`.",
        "category": "Platform Foundation",
        "tier": "Foundational Dependency",
        "edition_options": ["Included with Platform"],
        "license_rule": "Foundational dependency with component ID ccs. Do not mark CCS as Standard or Premium; attribute it to the parent product and count it only when IBM License Service reports a product/bundled-product metric row.",
        "dependencies": ["zen"],
        "default_limit_vpc": 8,
        "default_scale": "small",
        "pod_regex": "ccs-cams-postgres-.*|ccs-jobs-postgres-.*|ccs-post-install-job-.*",
        "desc": "Universal data connectivity, Arrow Flight transport, collaborative project workspaces, and catalog asset previews."
    },
    "opencontent_opensearch": {
        "name": "IBM OpenContent OpenSearch",
        "cr_kind": "Cluster",
        "cr_group": "opensearch.cloudpackopen.ibm.com",
        "cr_plural": "clusters.opensearch.cloudpackopen.ibm.com",
        "verified": "Group and kind corrected via live `oc get crd`, 2026-09-17 — previously guessed as 'OpenSearchCluster'/'opensearch.cpd.ibm.com', both wrong. Live instance observed: elasticsearch-master. A second, newer opensearch218-* pod set was also observed on this cluster but its owning CR was not identified in this pass (candidate: managed by the watsonx.data WxdAddon CR) — do not attribute it to this service until confirmed via ownerReferences.",
        "category": "Search & Indexing",
        "tier": "Platform Foundation",
        "edition_options": ["Included with Platform"],
        "license_rule": "Platform internal support program. Usage is restricted to platform metadata search, indexing, and audit logging.",
        "dependencies": [],
        "default_limit_vpc": 8,
        "default_scale": "small",
        "pod_regex": "elasticsearch-master-esnodes-.*|elasticsearch-master-snapshot-.*",
        "desc": "Distributed search and analytics engine for platform metadata, asset indexing, and audit logging."
    },
    "zen": {
        "name": "IBM Software Hub Control Plane (Zen)",
        "cr_kind": "ZenService",
        "cr_group": "zen.cpd.ibm.com",
        "cr_plural": "zenservices.zen.cpd.ibm.com",
        "verified": "Confirmed both live (`oc get crd`) and in IBM docs: https://www.ibm.com/support/pages/how-troubleshoot-when-zenservice-upgrade-stuck-71 shows apiVersion zen.cpd.ibm.com/v1, kind ZenService. The only KNOWN_SERVICES_CATALOG entry that was already fully correct.",
        "category": "Platform Foundation",
        "tier": "Platform Foundation",
        "edition_options": ["Software Hub Core"],
        "license_rule": "Unified platform control plane. Hosts the microservices gateway, IAM, access control, and user interface.",
        "dependencies": [],
        "default_limit_vpc": 6,
        "default_scale": "small",
        "pod_regex": "zen-core-.*|zen-audit-.*|zen-watchdog-.*|zen-watcher-.*|zen-minio-.*|zen-metastore-.*|ibm-nginx-.*|common-web-ui-.*|platform-auth-service-.*|platform-identity-.*|usermgmt-.*|cpd-mgmt-server-.*",
        "desc": "Core control plane, unified experience UI, security gateway, and user management."
    },
    "datastage_px": {
        "name": "IBM DataStage PX Runtime",
        "cr_kind": "PXRuntime",
        "cr_group": "ds.cpd.ibm.com",
        "cr_plural": "pxruntimes.ds.cpd.ibm.com",
        "verified": "Confirmed live via `oc get crd`, 2026-09-17 and in IBM docs (https://www.ibm.com/docs/en/software-hub/5.1.x?topic=troubleshooting-datastage). Not previously tracked at all in this catalog — a separate CRD from the DataStage CR itself, driving the compute-heavy parallel engine pods.",
        "category": "Data Integration",
        "tier": "Compute Engine",
        "edition_options": ["Bundled with DataStage Enterprise entitlement — not separately licensed"],
        "license_rule": "PXRuntime is the parallel-engine compute layer for DataStage; it is covered by the parent DataStage Enterprise entitlement (see datastage), not a separate License Service product row.",
        "dependencies": ["datastage"],
        "default_limit_vpc": 0,
        "default_scale": "small",
        "pod_regex": ".*-ibm-datastage-px-(compute|runtime)-.*",
        "desc": "High-performance parallel execution (PX) compute cluster underlying DataStage Enterprise pipelines."
    },
    "datarefinery": {
        "name": "IBM Data Refinery",
        "cr_kind": "DataRefinery",
        "cr_group": "datarefinery.cpd.ibm.com",
        "cr_plural": "datarefinery.datarefinery.cpd.ibm.com",
        "verified": "Confirmed live via `oc get crd`, 2026-09-17. Previously listed in SUPPORTED_SERVICES_CATALOG with no cr_kind/cr_group at all.",
        "category": "Data Preparation",
        "tier": "Dependency",
        "edition_options": ["Auto-installed dependency of Watson Studio and WKC"],
        "license_rule": "Do not usually specify directly; parent services (Watson Studio, WKC) install/upgrade it. Count only if IBM License Service reports a standalone product row.",
        "dependencies": ["ccs", "zen"],
        "default_limit_vpc": 8,
        "default_scale": "small",
        "pod_regex": "datarefinery-.*",
        "desc": "Interactive, visual data preparation and shaping used by Watson Studio and WKC."
    }
}


class ClusterTelemetryCollector:
    """Discovers installed CRDs/CRs and collects live telemetry from Thanos and License Service."""

    def __init__(self):
        self.namespace_license = os.environ.get("PROJECT_LICENSE_SERVICE", "ibm-licensing")
        self.namespace_cpd = os.environ.get("PROJECT_CPD_INST_OPERANDS", "cpd-instance")
        self.custom_services: Dict[str, Any] = {}
        # See get_oc_token: without this cache, a single page render (which calls
        # get_oc_token once per per-service Prometheus query — up to ~40 times for 10
        # services) reran `oc login` on every single call. Measured live: that made
        # concurrent polls pile up dozens of `oc login` subprocesses and turned a single
        # /api/telemetry request into a 60+ second hang.
        self._oc_logged_in = False
        self._oc_token_cache: Optional[str] = None
        self._oc_token_cache_time: float = 0.0
        self._license_token_cache: Optional[str] = None
        self._license_token_cache_time: float = 0.0
        self._license_host_cache: Optional[str] = None
        self._license_host_cache_time: float = 0.0

    def run_cmd(self, cmd_args: List[str]) -> Optional[str]:
        try:
            res = subprocess.run(cmd_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=8)
            if res.returncode == 0:
                return res.stdout.strip()
            return None
        except Exception:
            return None

    def get_oc_token(self) -> Optional[str]:
        # Priority 1: explicit bearer token
        token = os.environ.get("OCP_TOKEN")
        if token:
            return token
        # Short-lived cache: one real page render calls this many times in quick succession
        # (once per service per Prometheus metric) — there is no need to re-login or even
        # re-run `oc whoami -t` that often.
        now = time.time()
        if self._oc_token_cache and (now - self._oc_token_cache_time) < 30:
            return self._oc_token_cache
        # Priority 2: username + password — perform 'oc login' automatically, but only once
        # per process; the kubeconfig session it creates persists for later `oc whoami -t`
        # calls, so repeating the login on every call was pure waste (and, under concurrent
        # requests, a pile-up of redundant login subprocesses).
        user = os.environ.get("OCP_USER")
        password = os.environ.get("OCP_PASSWORD")
        ocp_url = os.environ.get("OCP_URL")
        if user and password and ocp_url and not self._oc_logged_in:
            self.run_cmd([
                "oc", "login", ocp_url,
                "-u", user, "-p", password,
                "--insecure-skip-tls-verify=true"
            ])
            self._oc_logged_in = True
        # Priority 3: active oc session
        token = self.run_cmd(["oc", "whoami", "-t"])
        if token:
            self._oc_token_cache = token
            self._oc_token_cache_time = now
        return token

    # IBM License Service reports metricName as the full ILMT metric code name (confirmed
    # live: "VIRTUAL_PROCESSOR_CORE", "RESOURCE_UNIT" — matching ILMT metric IDs 5844/10251,
    # https://www.ibm.com/docs/en/license-metric-tool/9.2.0?topic=v2-metric-ids-code-names),
    # not the short "VPC"/"RU" labels this dashboard displays. Normalize for aggregation while
    # keeping the original metricName on each row for display.
    LICENSE_METRIC_ALIASES = {
        "VIRTUAL_PROCESSOR_CORE": "VPC",
        "VPC": "VPC",
        "RESOURCE_UNIT": "RU",
        "RU": "RU",
    }

    @staticmethod
    def _as_rows(data: Any, *wrapper_keys: str) -> List[Dict[str, Any]]:
        """IBM License Service returns /products, /bundled_products, /services as raw JSON
        arrays (confirmed live) — not wrapped in {"products": [...]}. Accept both shapes so a
        future API version that does wrap the array still works."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in wrapper_keys:
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []

    def normalize_metric_name(self, raw_name: str) -> str:
        return self.LICENSE_METRIC_ALIASES.get(str(raw_name or "").upper(), str(raw_name or "UNKNOWN").upper())

    def aggregate_license_metrics(self, products: List[Dict[str, Any]]) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for product in products:
            metric = self.normalize_metric_name(product.get("metricName") or product.get("metric"))
            try:
                quantity = float(product.get("metricQuantity", product.get("quantity", 0)) or 0)
            except (TypeError, ValueError):
                quantity = 0.0
            totals[metric] = round(totals.get(metric, 0.0) + quantity, 2)
        return totals

    def call_license_api(self, host: str, token: str, endpoint: str) -> Dict[str, Any]:
        # Live-tested 2026-09-17 against a real 4.2.20 instance: the `?token=` URL-parameter
        # method (IBM's own docs mention as one of two supported methods, disable-able by
        # policy) returned 401 for a ServiceAccount reader token; `Authorization: Bearer`
        # worked immediately. Send both so this also works against a deployment configured for
        # the URL-param method with a different kind of token.
        url = f"https://{host}/{endpoint}?token={urllib.parse.quote(token)}"
        started = time.time()
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=8) as resp:
                body = resp.read()
                content_type = resp.headers.get("Content-Type", "")
                parsed: Any
                if "json" in content_type or body[:1] in (b"{", b"["):
                    parsed = json.loads(body.decode("utf-8"))
                else:
                    parsed = {"bytes": len(body), "contentType": content_type}
                return {
                    "endpoint": f"/{endpoint}",
                    "method": "GET",
                    "path": f"/{endpoint}",
                    "status": "ok",
                    "httpStatus": resp.status,
                    "latencyMs": int((time.time() - started) * 1000),
                    "data": parsed,
                }
        except Exception as exc:
            return {
                "endpoint": f"/{endpoint}",
                "method": "GET",
                "path": f"/{endpoint}",
                "status": "error",
                "latencyMs": int((time.time() - started) * 1000),
                "error": str(exc),
            }

    def discover_installed_crs(self) -> Dict[str, Any]:
        """Discovers Custom Resources, their sizing (scaleConfig), and CRD status from OpenShift."""
        discovered = {}
        for svc_id, defn in KNOWN_SERVICES_CATALOG.items():
            kind = defn["cr_kind"]
            # Prefer the fully-qualified <plural>.<group> resource name (verified against a
            # live cluster, see KNOWN_SERVICES_CATALOG["verified"]) so the lookup is unambiguous
            # even when another CRD elsewhere in the cluster happens to share a bare kind name.
            # ibmlicensing is cluster-scoped (no namespace), everything else here is namespaced.
            resource_ref = defn.get("cr_plural") or kind.lower()
            cr_json_str = self.run_cmd([
                "oc", "get", resource_ref, "-n", self.namespace_cpd, "-o", "json"
            ])
            if cr_json_str:
                try:
                    cr_data = json.loads(cr_json_str)
                    items = cr_data.get("items", []) if "items" in cr_data else [cr_data] if cr_data else []
                    if items:
                        first_cr = items[0]
                        metadata = first_cr.get("metadata", {})
                        spec = first_cr.get("spec", {})
                        status = first_cr.get("status", {})
                        scale = (
                            spec.get("scaleConfig") or
                            spec.get("scale_config") or
                            spec.get("scale") or
                            spec.get("parameters", {}).get("scaleConfig") or
                            spec.get("parameters", {}).get("scale_config") or
                            defn["default_scale"]
                        )
                        cr_status = (
                            status.get(f"{kind.lower()}Status") or
                            status.get("conditions", [{}])[-1].get("type") or
                            status.get("phase") or
                            "Completed"
                        )
                        discovered[svc_id] = {
                            **defn,
                            "cr_name": metadata.get("name", kind.lower()),
                            "crd_sizing": str(scale),
                            "cr_status": str(cr_status),
                            "is_live_cr": True
                        }
                except Exception:
                    pass

        if not discovered:
            for svc_id, defn in KNOWN_SERVICES_CATALOG.items():
                discovered[svc_id] = {
                    **defn,
                    "cr_name": f"cr-{svc_id}",
                    "crd_sizing": defn["default_scale"],
                    "cr_status": "Completed",
                    "is_live_cr": False
                }

        for custom_id, custom_defn in self.custom_services.items():
            discovered[custom_id] = custom_defn

        return discovered

    def get_license_service_token(self) -> Optional[str]:
        """Live-cluster testing (2026-09-17) found the plain OAuth token from `oc whoami -t`
        gets 401 on every License Service endpoint except /version, and a secret literally
        named `ibm-licensing-token` also 401s. The token that actually authorizes read access
        is the Kubernetes ServiceAccount token in secret `ibm-licensing-default-reader-token`.
        IBM's docs confirm the auth *mechanism* (a service-account-token via the Authorization
        header) but not this exact secret name, so try it first, keep the older name as a
        fallback for clusters/versions that differ, and allow an explicit override for anyone
        who has already decoded a token by hand."""
        override = os.environ.get("LICENSE_SERVICE_TOKEN")
        if override:
            return override
        now = time.time()
        if self._license_token_cache and (now - self._license_token_cache_time) < 30:
            return self._license_token_cache
        for secret_name in ("ibm-licensing-default-reader-token", "ibm-licensing-token"):
            token_b64 = self.run_cmd([
                "oc", "get", "secret", secret_name,
                "-n", self.namespace_license, "-o", "jsonpath={.data.token}"
            ])
            if token_b64:
                try:
                    token = base64.b64decode(token_b64).decode("utf-8")
                    self._license_token_cache = token
                    self._license_token_cache_time = now
                    return token
                except Exception:
                    continue
        return None

    def get_license_service_host(self) -> Optional[str]:
        """Prefer an explicit override for environments where the cluster's `apps.*` wildcard
        route domain isn't resolvable from wherever this dashboard runs (a real, confirmed
        network-scoping issue distinct from the API server's `api.*` hostname) — e.g. point
        LICENSE_SERVICE_HOST at `localhost:<port>` behind `oc port-forward -n <ns>
        svc/ibm-licensing-service-instance <port>:8080`."""
        override = os.environ.get("LICENSE_SERVICE_HOST")
        if override:
            return override
        now = time.time()
        if self._license_host_cache and (now - self._license_host_cache_time) < 300:
            return self._license_host_cache
        host = self.run_cmd([
            "oc", "get", "route", "ibm-licensing-service-instance",
            "-n", self.namespace_license, "-o", "jsonpath={.spec.host}"
        ])
        if host:
            self._license_host_cache = host
            self._license_host_cache_time = now
        return host

    def get_license_service_data(self) -> Dict[str, Any]:
        host = self.get_license_service_host()
        token = self.get_license_service_token()

        if not host or not token:
            wxd_edition = os.environ.get("WXD_EDITION", "").upper()
            # Base row — always present for any watsonx.data deployment
            products = [
                {"name": "IBM watsonx.data", "id": "ibm-watsonx-data", "metricName": "RU", "metricQuantity": 420.0, "status": "Sample", "edition": wxd_edition or "Standard / non-premium", "release": "2.3.x / 2.4.x"},
            ]
            if wxd_edition == "WXD_LAKEHOUSE_PREMIUM":
                # Premium adds Milvus — still one RU row, higher quantity
                products[0]["edition"] = "Lakehouse Premium (Milvus + advanced governance)"
                products[0]["metricQuantity"] = 520.0
            elif wxd_edition == "WXD_INTEGRATION":
                # DataStage, Data Replication, DataBand, StreamSets all bundled as RU
                products[0]["edition"] = "watsonx.data Integration (DataStage + Replication bundled)"
                products[0]["metricQuantity"] = 620.0
                products.append({"name": "IBM DataStage Enterprise (bundled)", "id": "ibm-datastage-ent", "metricName": "RU", "metricQuantity": 0.0, "status": "Sample", "edition": "Bundled in WXD_INTEGRATION — no separate VPC row", "release": "15.3 / 15.4"})
            elif wxd_edition == "WXD_INTELLIGENCE":
                # Full Data Fabric stack — everything bundled as RU
                products[0]["edition"] = "watsonx.data Intelligence (full Data Fabric)"
                products[0]["metricQuantity"] = 820.0
                products.append({"name": "IBM DataStage Enterprise (bundled)", "id": "ibm-datastage-ent", "metricName": "RU", "metricQuantity": 0.0, "status": "Sample", "edition": "Bundled in WXD_INTELLIGENCE — no separate VPC row", "release": "15.3 / 15.4"})
                products.append({"name": "IBM Knowledge Catalog (bundled)", "id": "ibm-knowledge-catalog", "metricName": "RU", "metricQuantity": 0.0, "status": "Sample", "edition": "Bundled in WXD_INTELLIGENCE — no separate VPC row", "release": "5.3 / 5.4"})
                products.append({"name": "IBM MANTA Data Lineage (bundled)", "id": "ibm-manta-lineage", "metricName": "RU", "metricQuantity": 0.0, "status": "Sample", "edition": "Bundled in WXD_INTELLIGENCE — no separate VPC row", "release": "5.3 / 5.4"})
            else:
                # WXD_LAKEHOUSE (default) or unset — standalone CPD cartridges billed as VPC
                products += [
                    {"name": "IBM Knowledge Catalog", "id": "ibm-knowledge-catalog", "metricName": "VPC", "metricQuantity": 24.5, "status": "Sample", "edition": "Knowledge Catalog standalone", "release": "5.3 / 5.4"},
                    {"name": "IBM DataStage Enterprise Cartridge", "id": "ibm-datastage-ent", "metricName": "VPC", "metricQuantity": 18.0, "status": "Sample", "edition": "Standalone — separate VPC entitlement required", "release": "15.3 / 15.4"},
                    {"name": "IBM Manta Data Lineage Cartridge", "id": "ibm-manta-lineage", "metricName": "VPC", "metricQuantity": 8.0, "status": "Sample", "edition": "Lineage cartridge standalone", "release": "5.3 / 5.4"},
                    {"name": "IBM Analytics Engine", "id": "ibm-analytics-engine", "metricName": "VPC", "metricQuantity": 12.0, "status": "Sample", "edition": "Spark service standalone", "release": "5.3 / 5.4"},
                ]
            metric_totals = self.aggregate_license_metrics(products)
            return {
                "status": "simulated",
                "mode": "Sample data - connect IBM License Service for authoritative audit values",
                "products": products,
                "bundledProducts": [],
                "serviceContributions": [],
                "metricTotals": metric_totals,
                "totalVpc": metric_totals.get("VPC", 0.0),
                "totalRu": metric_totals.get("RU", 0.0),
                "clusterPeakVpc": float(os.environ.get("IBM_VPC_ENTITLEMENT", "128")),
                "ruEntitlement": float(os.environ.get("IBM_RU_ENTITLEMENT", "1000")),
                "latestPeakDate": None,
                "reportingWindow": "calendar quarter",
                "auditRetentionYears": 2,
                "auditReady": False,
                "apiCoverage": [
                    {
                        "endpoint": "/products",
                        "method": "GET",
                        "status": "sample",
                        "meaning": "Authoritative product metric rows when connected",
                        "raw": {"request": {"method": "GET", "path": "/products"}, "sample_response": {"products": products[:2]}},
                    },
                    {
                        "endpoint": "/bundled_products",
                        "method": "GET",
                        "status": "sample",
                        "meaning": "Cloud Pak bundle rows; do not double count with /products",
                        "raw": {"request": {"method": "GET", "path": "/bundled_products"}, "sample_response": {"products": []}},
                    },
                    {
                        "endpoint": "/services",
                        "method": "GET",
                        "status": "sample",
                        "meaning": "Service contribution drill-down",
                        "raw": {"request": {"method": "GET", "path": "/services"}, "sample_response": {"services": []}},
                    },
                    {
                        "endpoint": "/snapshot",
                        "method": "GET",
                        "status": "sample",
                        "meaning": "Audit package for a reporting period",
                        "raw": {"request": {"method": "GET", "path": "/snapshot", "query": {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}}, "sample_response": {"audit_package": "binary/json evidence depending on License Service Reporter configuration"}},
                    },
                    {
                        "endpoint": "/health",
                        "method": "GET",
                        "status": "sample",
                        "meaning": "License Service health",
                        "raw": {"request": {"method": "GET", "path": "/health"}, "sample_response": {"status": "healthy"}},
                    },
                    {
                        "endpoint": "/status",
                        "method": "GET",
                        "status": "sample",
                        "meaning": "License Service readiness and status page",
                        "raw": {"request": {"method": "GET", "path": "/status"}, "sample_response": {"status": "ready"}},
                    },
                ]
            }

        try:
            calls = {
                endpoint: self.call_license_api(host, token, endpoint)
                for endpoint in ["products", "bundled_products", "services", "health", "status"]
            }
            products_data = calls["products"].get("data") if calls["products"]["status"] == "ok" else []
            bundles_data = calls["bundled_products"].get("data") if calls["bundled_products"]["status"] == "ok" else []
            services_data = calls["services"].get("data") if calls["services"]["status"] == "ok" else []

            # IBM License Service returns these three as raw JSON arrays, not
            # {"products": [...]}-wrapped objects (confirmed against a live 4.2.20 instance) —
            # see _as_rows for why this must accept both shapes.
            products = self._as_rows(products_data, "products")
            bundled_products = self._as_rows(bundles_data, "products", "bundledProducts")
            service_contributions = self._as_rows(services_data, "services")
            for product in products:
                product["metricNameNormalized"] = self.normalize_metric_name(product.get("metricName"))
            metric_totals = self.aggregate_license_metrics(products)
            peak_dates = [p.get("metricPeakDate") for p in products if p.get("metricPeakDate")]
            latest_peak_date = max(peak_dates) if peak_dates else None
            api_coverage = [
                {k: v for k, v in call.items() if k != "data"} | {
                    "meaning": meaning,
                    "method": call.get("method", "GET"),
                    "raw": {
                        "request": {
                            "method": call.get("method", "GET"),
                            "path": call.get("path", call.get("endpoint")),
                            "token": "redacted",
                        },
                        "response": call.get("data", {"error": call.get("error")}),
                    },
                }
                for call, meaning in [
                    (calls["products"], "Product license usage and metricName/metricQuantity. Primary metering table."),
                    (calls["bundled_products"], "Bundled product usage for Cloud Pak style entitlements. Keep separate from products."),
                    (calls["services"], "Service contribution and attribution detail."),
                    ({
                        "endpoint": "/snapshot",
                        "method": "GET",
                        "path": "/snapshot",
                        "status": "available",
                        "meaning": "Returns a signed ZIP (not JSON) containing products/bundled_products CSVs, signature.rsa, pub_key.pem, and checksum.txt — the same audit package IBM License Service uploads for compliance reporting. Fetched on demand by the Export Audit Package action, not on every poll.",
                        "data": {"request": {"method": "GET", "path": "/snapshot", "query": {"startDate": "YYYY-MM-DD", "endDate": "YYYY-MM-DD"}}},
                    }, "Signed audit evidence ZIP package."),
                    (calls["health"], "License Service health."),
                    (calls["status"], "License Service status page/API readiness."),
                ]
            ]
            # Resolving host+token does not mean the API is actually reachable — e.g. this
            # cluster's apps.* wildcard route domain was unresolvable from outside the cluster
            # network in testing even though the api.* apiserver hostname worked fine. Report
            # that honestly instead of claiming "connected" when every call errored.
            any_call_ok = any(calls[e]["status"] == "ok" for e in ("products", "bundled_products", "services", "health"))
            if any_call_ok:
                status, mode = "connected", "Live IBM License Service API"
            else:
                status, mode = "unreachable", (
                    "Host and token resolved but every API call failed — likely a network-reachability "
                    "issue (e.g. the apps.* route domain isn't resolvable from here). Try setting "
                    "LICENSE_SERVICE_HOST to a `oc port-forward`-ed localhost:<port> instead."
                )
            return {
                "status": status,
                "mode": mode,
                "host": host,
                "products": products,
                "bundledProducts": bundled_products,
                "serviceContributions": service_contributions,
                "metricTotals": metric_totals,
                "totalVpc": metric_totals.get("VPC", 0.0),
                "totalRu": metric_totals.get("RU", 0.0),
                "clusterPeakVpc": float(os.environ.get("IBM_VPC_ENTITLEMENT", "128")),
                "ruEntitlement": float(os.environ.get("IBM_RU_ENTITLEMENT", "1000")),
                "latestPeakDate": latest_peak_date,
                "reportingWindow": "calendar quarter",
                "auditRetentionYears": 2,
                "auditReady": calls["products"]["status"] == "ok",
                "apiCoverage": api_coverage
            }
        except Exception as e:
            return {
                "status": "error",
                "mode": "API Error",
                "message": f"Error querying License Service: {str(e)}",
                "products": [],
                "bundledProducts": [],
                "serviceContributions": [],
                "metricTotals": {},
                "totalVpc": 0.0,
                "totalRu": 0.0,
                "clusterPeakVpc": float(os.environ.get("IBM_VPC_ENTITLEMENT", "128")),
                "ruEntitlement": float(os.environ.get("IBM_RU_ENTITLEMENT", "1000")),
                "auditReady": False,
                "apiCoverage": [
                    {"endpoint": "/products", "status": "error", "meaning": f"License Service query failed: {str(e)}"}
                ]
            }

    def fetch_license_snapshot(self) -> Dict[str, Any]:
        """Fetches the real IBM License Service /snapshot audit package (a signed ZIP, not
        JSON — see get_license_service_data) on demand. Not called on every telemetry poll:
        only invoked by the Export Audit Package action, since it's a heavier binary payload."""
        host = self.get_license_service_host()
        token = self.get_license_service_token()
        if not host or not token:
            return {"status": "unavailable", "reason": "License Service host/token not resolved — connect to a live cluster to export the IBM-signed package."}
        url = f"https://{host}/snapshot?token={urllib.parse.quote(token)}"
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=20) as resp:
                body = resp.read()
                return {"status": "ok", "bytes": body, "contentType": resp.headers.get("Content-Type", "application/zip")}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    def query_prometheus(self, query: str) -> Optional[Any]:
        # Same apps.* wildcard DNS reachability issue as the License Service route can apply
        # here — allow the same override pattern (e.g. `oc port-forward -n openshift-monitoring
        # svc/thanos-querier <port>:9091` and THANOS_HOST=localhost:<port>).
        thanos_host = os.environ.get("THANOS_HOST") or self.run_cmd([
            "oc", "get", "route", "thanos-querier",
            "-n", "openshift-monitoring", "-o", "jsonpath={.spec.host}"
        ])
        token = self.get_oc_token()
        if not thanos_host or not token:
            return None

        try:
            url = f"https://{thanos_host}/api/v1/query?query={urllib.parse.quote(query)}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=8) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                return res_json.get("data", {}).get("result", [])
        except Exception:
            return None

    def read_local_install_options(self) -> Dict[str, Any]:
        path = os.environ.get(
            "LOCAL_INSTALL_OPTIONS_FILE",
            os.path.join(ROOT_DIR, "config", "install-options.watsonx-data-no-gpu.yml")
        )
        try:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            return {"path": path, "present": False, "text": ""}
        return {"path": path, "present": True, "text": text}

    def infer_option_status(self, option: Dict[str, Any], text: str, components: str) -> Dict[str, str]:
        text_lower = text.lower()
        components_set = {c.strip() for c in components.split(",") if c.strip()}
        status = option.get("default_status", "TBD")
        evidence = "Not detected in local install-options or COMPONENTS."

        for marker in option.get("component_markers", []):
            if marker in components_set:
                return {"status": "configured", "evidence": f"COMPONENTS includes {marker}."}

        disabled = option.get("disabled_when", "").lower()
        configured = option.get("configured_when", "").lower()
        if disabled and disabled in text_lower:
            status = "not enabled"
            evidence = f"Local install-options contains {option.get('disabled_when')}."
        elif configured and configured in text_lower:
            status = "configured"
            evidence = f"Local install-options contains {option.get('configured_when')}."
        elif option.get("configured_value_key") and option["configured_value_key"].lower() in text_lower:
            status = "configured"
            evidence = f"Local install-options contains {option['configured_value_key']}."
        elif option.get("default_status") == "dependency":
            evidence = "Dependency/component is normally resolved by parent services; confirm with live CRs and License Service."

        return {"status": status, "evidence": evidence}

    def get_install_options_overview(self, wkc_features: Optional[Dict[str, Any]] = None,
                                      milvus_status: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        local = self.read_local_install_options()
        text = local.get("text", "")
        components = os.environ.get("COMPONENTS", "")
        ikc_type = os.environ.get("IKC_TYPE", "")
        entries = []

        # Several of these rows started life as static "TBD"/YAML-text guesses — the static
        # install-options file can say enable_lite_milvus: false while a real Milvus WxdEngine
        # runs on the cluster (provisioned another way), or a WKC sub-CRD can exist and report
        # Completed while functionally disabled (see get_wkc_feature_status's docstring). Once
        # a live cluster check has run, its verdict overrides the static guess here.
        wkc_feature_by_id = {f["id"]: f for f in (wkc_features or {}).get("features", [])}
        reference_to_live_id = {
            "data_quality": "data_quality",
            "knowledge_graph": "knowledge_graph",
            "lineage": "data_lineage",
            "neo4j": "neo4j",
        }

        for option in INSTALL_OPTIONS_CATALOG:
            inferred = self.infer_option_status(option, text, components)
            entry = {
                **option,
                "status": inferred["status"],
                "evidence": inferred["evidence"],
            }
            live_id = reference_to_live_id.get(option["id"])
            live_feature = wkc_feature_by_id.get(live_id) if live_id else None
            if live_feature is not None:
                entry["status"] = "configured" if live_feature["enabled"] else "not enabled"
                entry["evidence"] = f"Live-verified against {live_feature['cr']}: {live_feature['evidence']}"
            elif option["id"] == "lite_milvus" and milvus_status is not None and milvus_status.get("reachable"):
                entry["status"] = "configured" if milvus_status["enabled"] else "not enabled"
                entry["evidence"] = f"Live-verified against wxdengines.watsonxdata.ibm.com: {milvus_status['evidence']}"
            entries.append(entry)

        return {
            "sourceFile": local.get("path"),
            "sourceFilePresent": local.get("present", False),
            "components": components or "not set in this process",
            "ikcType": ikc_type or "not set in this process",
            "entries": entries,
            "sources": INSTALL_OPTIONS_REFERENCE["install_options_policy"]["sources"],
        }

    def get_supported_services(self, installed_services: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        installed_services = installed_services or {}
        installed_ids = set(installed_services.keys())
        installed_components = set()
        for svc in installed_services.values():
            installed_components.add(str(svc.get("id", "")))
            for opt in svc.get("edition_options", []):
                installed_components.add(str(opt))

        known_component_map = {
            "watsonx_data": "watsonx_data",
            "wkc": "wkc",
            "datastage": "datastage_ent",
            "lineage": "datalineage",
            "analyticsengine": "analyticsengine",
            "ccs": "ccs",
            "opencontent_opensearch": "opencontent_opensearch",
            "zen": "zen",
        }

        supported = []
        for item in SUPPORTED_SERVICES_CATALOG:
            component_id = item["id"]
            local_id = next((k for k, v in known_component_map.items() if v == component_id), component_id)
            installed = local_id in installed_ids or component_id in installed_components
            supported.append({
                **item,
                "local_id": local_id,
                "installed": installed,
                # Kubernetes pod names are RFC-1123 labels and can never contain an underscore,
                # so a fallback regex must use hyphens, not underscores, or it can never match
                # a real pod (a bug found by live-cluster audit, e.g. for "ibm-streamsets-sdi").
                "pod_regex": item.get("pod_regex") or component_id.replace("_", "-") + ".*",
                "license_rule": (
                    "Supported Software Hub service. Add for monitoring only after entitlement and "
                    "License Service product/bundled-product rows are verified."
                )
            })
        return supported

    def get_dependency_explorer(self, installed_services: Dict[str, Any], install_options: Dict[str, Any]) -> Dict[str, Any]:
        installed_ids = set(installed_services.keys())
        option_status = {entry.get("id"): entry for entry in install_options.get("entries", [])}
        wxd_edition = os.environ.get("WXD_EDITION", "").upper()

        # Determine which catalog node IDs are "active" based on installed CRs + WXD_EDITION
        # Always include platform backbone
        active_ids: set = {"software_hub", "license_service", "ccs", "zen", "opensearch"}

        # Add installed CRs and map KNOWN_SERVICES_CATALOG ids to dep-explorer ids
        cr_to_dep = {
            "watsonx_data": "watsonx_data",
            "wkc": "wkc",
            "datastage": "datastage_ent",
            "lineage": "datalineage",
            "analyticsengine": "analyticsengine",
            "opencontent_opensearch": "opensearch",
        }
        for cr_id in installed_ids:
            dep_id = cr_to_dep.get(cr_id, cr_id)
            active_ids.add(dep_id)

        # Add the watsonx.data edition node and its bundled components
        if wxd_edition == "WXD_LAKEHOUSE_PREMIUM":
            active_ids.update(["watsonx_data", "watsonx_data_premium", "lite_milvus"])
        elif wxd_edition == "WXD_INTEGRATION":
            active_ids.update(["watsonx_data", "wxd_integration", "datastage_ent", "replication", "databand", "streamsets"])
        elif wxd_edition == "WXD_INTELLIGENCE":
            active_ids.update(["watsonx_data", "wxd_integration", "watsonx_dataintelligence",
                                "datastage_ent", "replication", "databand", "streamsets",
                                "wkc", "ikc_premium", "datalineage", "analyticsengine", "dataproduct"])
        elif "watsonx_data" in installed_ids:
            active_ids.update(["watsonx_data", "analyticsengine", "opensearch", "lite_milvus"])

        # Always include watsonx_data if any lakehouse is active
        if any(i in active_ids for i in ["wxd_integration", "watsonx_dataintelligence", "watsonx_data_premium"]):
            active_ids.add("watsonx_data")

        # Pull in install-option nodes that are confirmed configured
        for entry in install_options.get("entries", []):
            if entry.get("status") == "configured":
                active_ids.add(entry["id"])
                if entry["id"] == "data_quality":
                    active_ids.add("datastage_ent")
                    active_ids.add("data_quality")

        # ── Graph simplification ─────────────────────────────────────────────
        # zen (Software Hub Control Plane) and ccs (Common Core Services) are
        # omitted as separate nodes. Instead, software_hub acts as the single
        # platform root. Any edge that previously pointed to/from zen or ccs
        # is redirected to software_hub so products still show their platform
        # dependency without creating a noisy fan-out cluster.
        COLLAPSED_INTO_HUB = {"zen", "ccs"}

        # Build annotated node list — only active nodes, zen/ccs suppressed
        nodes = []
        for node in DEPENDENCY_EXPLORER_CATALOG["nodes"]:
            if node["id"] not in active_ids:
                continue
            if node["id"] in COLLAPSED_INTO_HUB:
                continue  # folded into software_hub
            option = option_status.get(node["id"])
            installed = node["id"] in installed_ids or node["id"] in cr_to_dep.values()
            nodes.append({
                **node,
                "installed": installed,
                "option_status": option.get("status") if option else None,
                "evidence": option.get("evidence") if option else "Relationship catalog entry; verify with live CRs, install-options, entitlement, and License Service rows.",
            })

        # Build edges — reroute zen/ccs refs → software_hub, deduplicate, skip self-loops
        seen_edges: set = set()
        edges = []
        for edge in DEPENDENCY_EXPLORER_CATALOG["edges"]:
            src = edge["from"]
            tgt = edge["to"]
            # Reroute collapsed nodes
            if src in COLLAPSED_INTO_HUB:
                src = "software_hub"
            if tgt in COLLAPSED_INTO_HUB:
                tgt = "software_hub"
            # Skip if either endpoint not active (after rerouting)
            active_node_ids = {n["id"] for n in nodes} | {"software_hub"}
            if src not in active_node_ids or tgt not in active_node_ids:
                continue
            # Skip self-loops (e.g. software_hub → software_hub after collapse)
            if src == tgt:
                continue
            # Deduplicate — same src/tgt/relationship only once
            dedup_key = (src, tgt, edge["relationship"])
            if dedup_key in seen_edges:
                continue
            seen_edges.add(dedup_key)
            option = option_status.get(tgt) or option_status.get(src)
            edges.append({
                **edge,
                "from": src,
                "to": tgt,
                "status": option.get("status") if option else "relationship",
                "evidence": option.get("evidence") if option else edge.get("license_boundary", "Verify with IBM docs and License Service."),
            })

        edition_label = {
            "WXD_LAKEHOUSE_PREMIUM": "watsonx.data Premium",
            "WXD_INTEGRATION": "watsonx.data Integration",
            "WXD_INTELLIGENCE": "watsonx.data Intelligence",
        }.get(wxd_edition, "watsonx.data Lakehouse (Standard)" if "watsonx_data" in installed_ids else "CPD standalone")

        return {
            "nodes": nodes,
            "edges": edges,
            "sources": DEPENDENCY_EXPLORER_CATALOG["sources"],
            "wxd_edition": wxd_edition,
            "edition_label": edition_label,
            "positioning": (
                f"Showing active components only (WXD_EDITION={wxd_edition or 'not set'}, "
                f"{len(installed_ids)} live CRs detected). "
                "License Service product/bundled-product rows and entitlement terms decide metering."
            )
        }

    def get_service_telemetry(self) -> Dict[str, Any]:
        services = self.discover_installed_crs()
        results = {}

        for svc_id, s in services.items():
            regex = s.get("pod_regex", f"{svc_id}.*")
            cpu_usage_q = f'sum(node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate{{namespace="{self.namespace_cpd}", pod=~"{regex}"}})'
            cpu_limit_q = f'sum(kube_pod_container_resource_limits{{namespace="{self.namespace_cpd}", pod=~"{regex}", resource="cpu"}})'
            mem_usage_q = f'sum(container_memory_working_set_bytes{{namespace="{self.namespace_cpd}", pod=~"{regex}"}}) / 1073741824'
            mem_limit_q = f'sum(kube_pod_container_resource_limits{{namespace="{self.namespace_cpd}", pod=~"{regex}", resource="memory"}}) / 1073741824'

            cpu_usage_res = self.query_prometheus(cpu_usage_q)
            cpu_limit_res = self.query_prometheus(cpu_limit_q)
            mem_usage_res = self.query_prometheus(mem_usage_q)
            mem_limit_res = self.query_prometheus(mem_limit_q)

            crd_sizing = s.get("crd_sizing", "small")
            multiplier = 0.6 if "mincpu" in crd_sizing else 1.0 if crd_sizing == "small" else 2.0 if crd_sizing == "medium" else 3.5

            if cpu_usage_res and len(cpu_usage_res) > 0:
                cpu_used = float(cpu_usage_res[0].get("value", [0, 0])[1])
            else:
                base_map = {"datastage": 14.8, "watsonx_data": 33.2, "wkc": 19.5, "lineage": 7.4, "analyticsengine": 11.2, "ccs": 4.2, "opencontent_opensearch": 4.5, "zen": 3.8}
                cpu_used = round(base_map.get(svc_id, 8.0) * (0.8 if "mincpu" in crd_sizing else 1.0), 2)

            if cpu_limit_res and len(cpu_limit_res) > 0:
                cpu_limit = float(cpu_limit_res[0].get("value", [0, 0])[1])
            else:
                cpu_limit = round(s.get("default_limit_vpc", 16) * multiplier, 2)

            if mem_usage_res and len(mem_usage_res) > 0:
                mem_used_gb = float(mem_usage_res[0].get("value", [0, 0])[1])
            else:
                base_mem = {"datastage": 48.0, "watsonx_data": 96.0, "wkc": 52.0, "lineage": 22.0, "analyticsengine": 34.0, "ccs": 16.0, "opencontent_opensearch": 16.0, "zen": 12.0}
                mem_used_gb = round(base_mem.get(svc_id, 16.0) * (0.8 if "mincpu" in crd_sizing else 1.0), 2)

            if mem_limit_res and len(mem_limit_res) > 0:
                mem_limit_gb = float(mem_limit_res[0].get("value", [0, 0])[1])
            else:
                mem_limit_gb = round(mem_used_gb * 1.4, 2)

            cpu_pct = round((cpu_used / max(cpu_limit, 0.1)) * 100, 1)
            mem_pct = round((mem_used_gb / max(mem_limit_gb, 0.1)) * 100, 1)

            status = "HEALTHY"
            if cpu_pct > 85 or mem_pct > 90:
                status = "CRITICAL"
            elif cpu_pct > 70 or mem_pct > 75:
                status = "WARNING"

            results[svc_id] = {
                "id": svc_id,
                "name": s["name"],
                "category": s.get("category", "Workload"),
                "tier": s.get("tier", "Service"),
                "description": s.get("desc", ""),
                "cr_kind": s.get("cr_kind", "CustomResource"),
                "cr_name": s.get("cr_name", f"cr-{svc_id}"),
                "crd_sizing": crd_sizing,
                "cr_status": s.get("cr_status", "Completed"),
                "is_live_cr": s.get("is_live_cr", False),
                "edition_options": s.get("edition_options", ["Standard"]),
                "license_rule": s.get("license_rule", "Licensed under standard platform terms."),
                "dependencies": s.get("dependencies", []),
                "pod_regex": regex,
                "cpu_used_cores": round(cpu_used, 2),
                "cpu_limit_cores": round(cpu_limit, 2),
                "cpu_utilization_pct": cpu_pct,
                "mem_used_gb": round(mem_used_gb, 2),
                "mem_limit_gb": round(mem_limit_gb, 2),
                "mem_utilization_pct": mem_pct,
                "status": status,
            }

        return results

    @staticmethod
    def _parse_k8s_cpu(value: str) -> float:
        value = str(value or "0")
        if value.endswith("m"):
            try:
                return float(value[:-1]) / 1000.0
            except ValueError:
                return 0.0
        try:
            return float(value)
        except ValueError:
            return 0.0

    @staticmethod
    def _parse_k8s_memory_gib(value: str) -> float:
        value = str(value or "0")
        units = {"Ki": 1 / (1024 * 1024), "Mi": 1 / 1024, "Gi": 1, "Ti": 1024,
                 "K": 1e3 / (1024 ** 3), "M": 1e6 / (1024 ** 3), "G": 1e9 / (1024 ** 3), "T": 1e12 / (1024 ** 3)}
        for suffix, factor in units.items():
            if value.endswith(suffix):
                try:
                    return float(value[: -len(suffix)]) * factor
                except ValueError:
                    return 0.0
        try:
            return float(value) / (1024 ** 3)
        except ValueError:
            return 0.0

    def get_cluster_compliance_controls(self) -> Dict[str, Any]:
        """Real, checkable compliance-readiness signals that go beyond License Service's own
        metering: node-pinning configuration and namespace resource quota presence. Node
        pinning is IBM's documented mechanism (isc-entitlement node label; see
        entitlement_application_and_node_pinning in IBM_LICENSE_TERMS_INFO) for keeping
        multi-solution VPC/RU capacity attribution unambiguous — it is optional for a
        single-solution instance but worth surfacing either way, from a live check rather than
        an assumption."""
        nodes_json = self.run_cmd(["oc", "get", "nodes", "-o", "json"])
        nodes: List[Dict[str, Any]] = []
        total_cpu = 0.0
        total_mem_gib = 0.0
        pinned_count = 0
        gpu_capable_nodes = 0
        if nodes_json:
            try:
                data = json.loads(nodes_json)
                for item in data.get("items", []):
                    labels = item.get("metadata", {}).get("labels", {}) or {}
                    capacity = item.get("status", {}).get("capacity", {}) or {}
                    cpu = self._parse_k8s_cpu(capacity.get("cpu"))
                    mem_gib = self._parse_k8s_memory_gib(capacity.get("memory"))
                    is_worker = "node-role.kubernetes.io/worker" in labels and "node-role.kubernetes.io/master" not in labels and "node-role.kubernetes.io/control-plane" not in labels
                    isc_label = labels.get("isc-entitlement")
                    has_gpu = any(k.startswith("nvidia.com/gpu") for k in capacity.keys())
                    if has_gpu:
                        gpu_capable_nodes += 1
                    if isc_label:
                        pinned_count += 1
                    if is_worker:
                        total_cpu += cpu
                        total_mem_gib += mem_gib
                    nodes.append({
                        "name": item.get("metadata", {}).get("name"),
                        "isWorker": is_worker,
                        "cpuCores": cpu,
                        "memoryGiB": round(mem_gib, 1),
                        "iscEntitlementLabel": isc_label,
                        "gpuCapable": has_gpu,
                    })
            except Exception:
                pass

        quota_json = self.run_cmd(["oc", "get", "resourcequota,limitrange", "-n", self.namespace_cpd, "-o", "json"])
        has_quota = False
        if quota_json:
            try:
                has_quota = len(json.loads(quota_json).get("items", [])) > 0
            except Exception:
                pass

        return {
            "nodes": nodes,
            "workerNodeCount": sum(1 for n in nodes if n["isWorker"]),
            "totalWorkerCpuCores": round(total_cpu, 1),
            "totalWorkerMemoryGiB": round(total_mem_gib, 1),
            "gpuCapableNodeCount": gpu_capable_nodes,
            "nodePinningConfigured": pinned_count > 0,
            "pinnedNodeCount": pinned_count,
            "namespaceQuotaConfigured": has_quota,
            "namespaceQuotaNote": (
                f"No ResourceQuota/LimitRange found in namespace '{self.namespace_cpd}' — "
                "compare service sizing against raw worker node capacity below, not a namespace ceiling."
                if not has_quota else
                f"A ResourceQuota or LimitRange is configured in namespace '{self.namespace_cpd}'."
            ),
            "sources": ["https://www.ibm.com/docs/en/software-hub/5.4.x?topic=entitlements-applying-your-without-node-pinning",
                        "https://www.ibm.com/docs/en/SSNFH6_5.1.x/hub/plan/node-planning.html"],
        }

    def get_wkc_feature_status(self) -> Dict[str, Any]:
        """Live-verifies specific WKC/IKC sub-features (Data Quality, Knowledge Graph,
        standalone Data Lineage, Neo4j, Semantic Search) the way a live-cluster audit found
        actually works: a sub-CRD instance existing is NOT sufficient evidence by itself — on
        a real cluster a DataQuality CR existed and reported "Completed" while the owning
        WKC CR's own enableDataQuality field was false and zero matching pods existed
        anywhere, i.e. a reconciled-but-inactive shell. Cross-check the wkc-cr toggle field,
        the sub-CRD's own instance count, AND real deployment/statefulset presence before
        calling a feature enabled."""
        wkc_json = self.run_cmd(["oc", "get", "wkc.wkc.cpd.ibm.com", "-n", self.namespace_cpd, "-o", "json"])
        wkc_spec: Dict[str, Any] = {}
        wkc_reachable = False
        if wkc_json:
            try:
                items = json.loads(wkc_json).get("items", [])
                if items:
                    wkc_spec = items[0].get("spec", {})
                    wkc_reachable = True
            except Exception:
                pass

        def instance_count(resource: str) -> int:
            out = self.run_cmd(["oc", "get", resource, "-n", self.namespace_cpd, "-o", "json"])
            if not out:
                return 0
            try:
                return len(json.loads(out).get("items", []))
            except Exception:
                return 0

        workload_names = self.run_cmd(["oc", "get", "deploy,statefulset", "-n", self.namespace_cpd, "-o", "name"]) or ""
        workload_names = workload_names.lower()

        def has_workload(*substrings: str) -> bool:
            return any(s.lower() in workload_names for s in substrings)

        dq_instances = instance_count("dataquality.wkc.cpd.ibm.com")
        kg_instances = instance_count("knowledgegraph.wkc.cpd.ibm.com")
        lineage_instances = instance_count("datalineage.cpd.ibm.com")
        neo4j_instances = instance_count("neo4jclusters.neo4j.cpd.ibm.com")
        semsearch_instances = instance_count("semanticsearch.wkc.cpd.ibm.com")

        enable_dq = wkc_spec.get("enableDataQuality")
        enable_kg = wkc_spec.get("enableKnowledgeGraph")
        dq_workload = has_workload("data-quality", "dataquality")
        kg_workload = has_workload("wdp-kg-ingestion-service", "knowledge-accelerators")
        lineage_workload = has_workload("lineage-scanner", "wdp-lineage", "wkc-data-lineage-service")

        features = [
            {
                "id": "data_quality",
                "name": "IBM Knowledge Catalog Data Quality",
                "cr": "dataquality.wkc.cpd.ibm.com",
                "instances": dq_instances,
                "toggle_field": "wkc-cr spec.enableDataQuality",
                "toggle_value": enable_dq,
                "workload_found": dq_workload,
                "enabled": bool(enable_dq) and dq_instances > 0 and dq_workload,
                "evidence": (
                    f"{dq_instances} CR instance(s), wkc-cr enableDataQuality={enable_dq}, dedicated workload found={dq_workload}. "
                    + ("A CR can exist and report Completed while the feature is functionally off — verified on a live cluster where exactly this happened."
                       if dq_instances > 0 and not (bool(enable_dq) and dq_workload) else "")
                ),
            },
            {
                "id": "knowledge_graph",
                "name": "Knowledge Graph",
                "cr": "knowledgegraph.wkc.cpd.ibm.com",
                "instances": kg_instances,
                "toggle_field": "wkc-cr spec.enableKnowledgeGraph",
                "toggle_value": enable_kg,
                "workload_found": kg_workload,
                "enabled": bool(enable_kg) and kg_instances > 0 and kg_workload,
                "evidence": f"{kg_instances} CR instance(s), wkc-cr enableKnowledgeGraph={enable_kg}, dedicated workload found={kg_workload} (e.g. wdp-kg-ingestion-service).",
            },
            {
                "id": "data_lineage",
                "name": "Data Lineage (standalone MANTA/DataLineage)",
                "cr": "datalineage.cpd.ibm.com",
                "instances": lineage_instances,
                "toggle_field": None,
                "toggle_value": None,
                "workload_found": lineage_workload,
                "enabled": lineage_instances > 0 and lineage_workload,
                "evidence": f"{lineage_instances} CR instance(s), dedicated workload found={lineage_workload} (e.g. lineage-scanner-*, wdp-lineage). Not a wkc-cr toggle — this is a separate top-level add-on CR.",
            },
            {
                "id": "neo4j",
                "name": "Neo4j graph backend",
                "cr": "neo4jclusters.neo4j.cpd.ibm.com",
                "instances": neo4j_instances,
                "toggle_field": None,
                "toggle_value": None,
                "workload_found": None,
                "enabled": neo4j_instances > 0,
                "evidence": f"{neo4j_instances} Neo4jCluster instance(s) (checked cluster-wide). The operator can be installed and idle with zero instances requested.",
            },
            {
                "id": "semantic_search",
                "name": "Semantic Search",
                "cr": "semanticsearch.wkc.cpd.ibm.com",
                "instances": semsearch_instances,
                "toggle_field": None,
                "toggle_value": None,
                "workload_found": None,
                "enabled": semsearch_instances > 0,
                "evidence": f"{semsearch_instances} SemanticSearch CR instance(s). Do not confuse with the wkc-search deployment, which is Common Core Services' base catalog search (addOnId=ccs), not this feature.",
            },
        ]
        return {"wkc_reachable": wkc_reachable, "features": features}

    def get_live_milvus_status(self) -> Dict[str, Any]:
        """watsonx.data's Milvus vector engine is a named WxdEngine instance, not its own
        top-level CRD — so it can't be caught by the generic per-service CR discovery, and the
        static install-options YAML (enable_lite_milvus) can just as easily be stale or never
        set for a cluster where someone provisioned Milvus by other means. Read the actual
        WxdEngine instance names on a live cluster where a real Milvus engine was found running
        while install-options claimed it was disabled — this checks the ground truth directly."""
        engines_json = self.run_cmd(["oc", "get", "wxdengines.watsonxdata.ibm.com", "-n", self.namespace_cpd, "-o", "json"])
        milvus_names: List[str] = []
        reachable = False
        if engines_json:
            try:
                items = json.loads(engines_json).get("items", [])
                reachable = True
                milvus_names = [item["metadata"]["name"] for item in items if "milvus" in item.get("metadata", {}).get("name", "").lower()]
            except Exception:
                pass
        workload_names = self.run_cmd(["oc", "get", "statefulset,deploy", "-n", self.namespace_cpd, "-o", "name"]) or ""
        milvus_workload = any("milvus" in name.lower() for name in workload_names.splitlines())
        enabled = bool(milvus_names) and milvus_workload
        return {
            "reachable": reachable,
            "engine_names": milvus_names,
            "workload_found": milvus_workload,
            "enabled": enabled,
            "evidence": (
                f"{len(milvus_names)} WxdEngine instance(s) with 'milvus' in the name ({', '.join(milvus_names) or 'none'}), "
                f"dedicated workload found={milvus_workload}."
            ),
        }

    def get_cr_yaml(self, service_id: str, cr_name: Optional[str] = None) -> Dict[str, Any]:
        """Fetches the real, live YAML for a service's Custom Resource on demand — not baked
        into the telemetry poll payload (CRs can be large; most cards are never clicked)."""
        defn = KNOWN_SERVICES_CATALOG.get(service_id) or self.custom_services.get(service_id)
        if not defn:
            return {"status": "error", "message": f"Unknown service id: {service_id}"}
        resource = defn.get("cr_plural") or str(defn.get("cr_kind", "")).lower()
        if not resource:
            return {"status": "error", "message": f"No known CRD reference for '{service_id}'."}
        args = ["oc", "get", resource]
        if cr_name:
            args.append(cr_name)
        args += ["-n", self.namespace_cpd, "-o", "yaml"]
        yaml_text = self.run_cmd(args)
        if yaml_text is None:
            return {
                "status": "error",
                "resource": resource,
                "message": f"`oc get {resource}{' ' + cr_name if cr_name else ''} -n {self.namespace_cpd}` failed — check cluster connectivity, RBAC, and that an instance exists.",
            }
        return {"status": "ok", "resource": resource, "cr_name": cr_name, "namespace": self.namespace_cpd, "yaml": yaml_text}

    def get_cluster_overview(self) -> Dict[str, Any]:
        lic = self.get_license_service_data()
        services = self.get_service_telemetry()
        supported_services = self.get_supported_services(services)
        wkc_features = self.get_wkc_feature_status()
        milvus_status = self.get_live_milvus_status()
        install_options = self.get_install_options_overview(wkc_features, milvus_status)
        dependency_explorer = self.get_dependency_explorer(services, install_options)
        compliance_controls = self.get_cluster_compliance_controls()

        total_cpu_used = sum(s["cpu_used_cores"] for s in services.values())
        total_cpu_limit = sum(s["cpu_limit_cores"] for s in services.values())
        total_mem_used = sum(s["mem_used_gb"] for s in services.values())
        total_mem_limit = sum(s["mem_limit_gb"] for s in services.values())

        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "cluster_info": {
                "ocp_url": os.environ.get("OCP_URL", "https://api.watson.ibmas-zocp-techcluster.org:6443"),
                "license_namespace": self.namespace_license,
                "workload_namespace": self.namespace_cpd,
                "version": os.environ.get("IBM_SOFTWARE_HUB_VERSION", "IBM Software Hub / CPD 5.3.x-5.4.x, watsonx 2.3.x-2.4.x"),
            },
            "terms_info": {**IBM_LICENSE_TERMS_INFO, **INSTALL_OPTIONS_REFERENCE},
            "licensing": lic,
            "install_options": install_options,
            "dependency_explorer": dependency_explorer,
            "compliance_controls": compliance_controls,
            "wkc_features": wkc_features,
            "supported_services": supported_services,
            "services": services,
            "totals": {
                "discovered_service_count": len(services),
                "total_cpu_used_cores": round(total_cpu_used, 2),
                "total_cpu_limit_cores": round(total_cpu_limit, 2),
                "total_mem_used_gb": round(total_mem_used, 2),
                "total_mem_limit_gb": round(total_mem_limit, 2),
                "overall_cpu_pct": round((total_cpu_used / max(total_cpu_limit, 0.1)) * 100, 1),
                "overall_mem_pct": round((total_mem_used / max(total_mem_limit, 0.1)) * 100, 1),
            }
        }


# High-Fidelity Carbon Design System (carbondesignsystem.com) Spec & Plex Typography
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" data-theme="carbon" data-mode="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IBM Software Hub | VPC/RU License Metering</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,400;0,500;0,600;0,700;1,400&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&family=IBM+Plex+Sans+Condensed:wght@500;600;700&display=swap" rel="stylesheet">
<style>
  /* -------------------------------------------------------------------------
     OFFICIAL IBM CARBON DESIGN SYSTEM (carbondesignsystem.com) SPEC
     + a signature "metering" accent (see --cds-metering-*) for the one thing this
     product actually does that no other Carbon app does: visualize licensed
     capacity flowing from a cluster into IBM License Service.
     ------------------------------------------------------------------------- */
  :root {
    --cds-font-sans: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    --cds-font-mono: "IBM Plex Mono", "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    --cds-font-condensed: "IBM Plex Sans Condensed", var(--cds-font-sans);
    --cds-spacing-01: 2px;
    --cds-spacing-02: 4px;
    --cds-spacing-03: 8px;
    --cds-spacing-04: 12px;
    --cds-spacing-05: 16px;
    --cds-spacing-06: 24px;
    --cds-spacing-07: 32px;
    --cds-spacing-08: 40px;
  }

  /* Carbon g100 Theme (Dark) */
  html[data-mode="dark"] {
    --cds-background: #161616;
    --cds-layer-01: #262626;
    --cds-layer-02: #393939;
    --cds-layer-03: #525252;
    --cds-border-subtle-01: #393939;
    --cds-border-strong-01: #6f6f6f;
    --cds-text-primary: #f4f4f4;
    --cds-text-secondary: #c6c6c6;
    --cds-text-helper: #8d8d8d;
    --cds-interactive-01: #0f62fe;
    --cds-interactive-01-hover: #0353e9;
    --cds-interactive-accent: #8a3ffc;
    --cds-support-success: #24a148;
    --cds-support-warning: #f1c21b;
    --cds-support-danger: #da1e28;
    --cds-support-info: #4589ff;
    --cds-tag-bg-blue: rgba(15, 98, 254, 0.2);
    --cds-tag-color-blue: #78a9ff;
    --cds-tag-bg-purple: rgba(138, 63, 252, 0.2);
    --cds-tag-color-purple: #be95ff;
    --cds-tag-bg-green: rgba(36, 161, 72, 0.2);
    --cds-tag-color-green: #42be65;
    --cds-tag-bg-gray: rgba(141, 141, 141, 0.2);
    --cds-tag-color-gray: #c6c6c6;
    --cds-tag-bg-red: rgba(218, 30, 40, 0.2);
    --cds-tag-color-red: #ff8389;
    --cds-terms-bg: #1e1e1e;
    /* Signature "metering flow" accent — used only for the license-metering
       visualization (graph flow edges, live pulse), never as a semantic status
       color, so it never competes with success/warning/danger. */
    --cds-metering-accent: #00e5c7;
    --cds-metering-accent-dim: rgba(0, 229, 199, 0.22);
    --cds-canvas-grid: rgba(255, 255, 255, 0.05);
  }

  /* Carbon Gray 10 Theme (Light) */
  html[data-mode="light"] {
    --cds-background: #f4f4f4;
    --cds-layer-01: #ffffff;
    --cds-layer-02: #e0e0e0;
    --cds-layer-03: #c6c6c6;
    --cds-border-subtle-01: #e0e0e0;
    --cds-border-strong-01: #8d8d8d;
    --cds-text-primary: #161616;
    --cds-text-secondary: #525252;
    --cds-text-helper: #6f6f6f;
    --cds-interactive-01: #0f62fe;
    --cds-interactive-01-hover: #0353e9;
    --cds-interactive-accent: #6929c4;
    --cds-support-success: #198038;
    --cds-support-warning: #b28600;
    --cds-support-danger: #da1e28;
    --cds-support-info: #0043ce;
    --cds-tag-bg-blue: #edf5ff;
    --cds-tag-color-blue: #0043ce;
    --cds-tag-bg-purple: #f6f2ff;
    --cds-tag-color-purple: #6929c4;
    --cds-tag-bg-green: #defbe6;
    --cds-tag-color-green: #0e6027;
    --cds-tag-bg-gray: #e0e0e0;
    --cds-tag-color-gray: #393939;
    --cds-tag-bg-red: #ffebe9;
    --cds-tag-color-red: #a2191f;
    --cds-terms-bg: #f9f9fb;
    --cds-metering-accent: #007567;
    --cds-metering-accent-dim: rgba(0, 117, 103, 0.14);
    --cds-canvas-grid: rgba(0, 0, 0, 0.05);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background-color: var(--cds-background);
    color: var(--cds-text-primary);
    font-family: var(--cds-font-sans);
    line-height: 1.5;
    transition: background-color 0.15s ease, color 0.15s ease;
    -webkit-font-smoothing: antialiased;
  }

  /* Carbon UI Shell Header */
  .cds--header {
    background-color: var(--cds-background);
    border-bottom: 1px solid var(--cds-border-subtle-01);
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 var(--cds-spacing-06);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .cds--header__name {
    display: flex;
    align-items: center;
    gap: var(--cds-spacing-03);
    font-size: 14px;
    color: var(--cds-text-primary);
    text-decoration: none;
    letter-spacing: 0.1px;
  }
  .cds--header__name strong { font-weight: 600; }
  .cds--header__global {
    display: flex;
    align-items: center;
    gap: var(--cds-spacing-03);
  }

  /* ── App shell: persistent left nav + status rail, like a real ops console ── */
  .cds--app-body {
    display: flex;
    align-items: stretch;
    min-height: calc(100vh - 48px);
  }
  .cds--sidenav {
    width: 232px;
    flex: 0 0 232px;
    background: var(--cds-layer-01);
    border-right: 1px solid var(--cds-border-subtle-01);
    position: sticky;
    top: 48px;
    height: calc(100vh - 48px);
    display: flex;
    flex-direction: column;
    z-index: 90;
  }
  .cds--sidenav-nav {
    padding: var(--cds-spacing-05) 0;
    flex: 1;
    overflow-y: auto;
  }
  .cds--sidenav-section-label {
    font-family: var(--cds-font-condensed);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--cds-text-helper);
    padding: 0 var(--cds-spacing-05);
    margin: var(--cds-spacing-04) 0 var(--cds-spacing-02);
  }
  .cds--sidenav-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px var(--cds-spacing-05);
    background: none;
    border: none;
    border-left: 3px solid transparent;
    color: var(--cds-text-secondary);
    font-family: var(--cds-font-sans);
    font-size: 13px;
    text-align: left;
    cursor: pointer;
    transition: background-color 0.15s, color 0.15s, border-color 0.15s;
  }
  .cds--sidenav-item:hover {
    background: var(--cds-layer-02);
    color: var(--cds-text-primary);
  }
  .cds--sidenav-item.is-active {
    background: var(--cds-layer-02);
    color: var(--cds-text-primary);
    border-left-color: var(--cds-metering-accent);
    font-weight: 600;
  }
  .cds--sidenav-item .cds--nav-icon {
    width: 16px; height: 16px; flex: none;
    display: inline-flex; align-items: center; justify-content: center;
  }
  .cds--sidenav-item .cds--nav-badge {
    margin-left: auto;
    font-family: var(--cds-font-mono);
    font-size: 10px;
    color: var(--cds-text-helper);
  }
  .cds--sidenav-status {
    border-top: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-04) var(--cds-spacing-05);
    font-size: 11px;
    color: var(--cds-text-secondary);
    display: flex;
    flex-direction: column;
    gap: 7px;
  }
  .cds--status-row {
    display: flex;
    align-items: center;
    gap: 7px;
    justify-content: space-between;
  }
  .cds--status-row .cds--status-label {
    color: var(--cds-text-helper);
    text-transform: uppercase;
    font-size: 9px;
    letter-spacing: 0.6px;
  }
  .cds--status-row .cds--status-value {
    font-family: var(--cds-font-mono);
    font-size: 10px;
    color: var(--cds-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 148px;
    text-align: right;
  }
  .cds--status-dot {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    flex: none;
  }
  .cds--status-dot.is-live { background: var(--cds-support-success); box-shadow: 0 0 6px var(--cds-support-success); }
  .cds--status-dot.is-simulated { background: var(--cds-support-warning); }
  .cds--status-dot.is-down { background: var(--cds-support-danger); }
  .cds--status-dot.is-loading { background: var(--cds-metering-accent); animation: cds-pulse-dot 1s ease-in-out infinite; }
  @keyframes cds-pulse-dot { 0%, 100% { opacity: 1; } 50% { opacity: 0.35; } }

  .cds--shell-main {
    flex: 1;
    min-width: 0;
  }
  .cds--view-panel[hidden] { display: none !important; }

  /* Slim top-of-page fetch indicator — a real "it's working", not a frozen screen */
  .cds--fetch-bar {
    position: sticky;
    top: 48px;
    left: 0;
    height: 2px;
    width: 100%;
    background: transparent;
    z-index: 95;
    overflow: hidden;
  }
  .cds--fetch-bar::before {
    content: "";
    display: block;
    height: 100%;
    width: 30%;
    background: var(--cds-metering-accent);
    transform: translateX(-100%);
    opacity: 0;
  }
  .cds--fetch-bar.is-active::before {
    opacity: 1;
    animation: cds-fetch-sweep 1.1s ease-in-out infinite;
  }
  @keyframes cds-fetch-sweep {
    0% { transform: translateX(-100%); }
    100% { transform: translateX(400%); }
  }

  /* Loading spinner used for any tile/card still waiting on its first real value */
  .cds--spinner {
    display: inline-block;
    width: 14px; height: 14px;
    border: 2px solid var(--cds-border-subtle-01);
    border-top-color: var(--cds-metering-accent);
    border-radius: 50%;
    animation: cds-spin 0.7s linear infinite;
    vertical-align: middle;
  }
  @keyframes cds-spin { to { transform: rotate(360deg); } }
  .cds--tile__value.is-loading { display: flex; align-items: center; gap: 8px; font-size: 16px; color: var(--cds-text-helper); }

  @media (max-width: 900px) {
    .cds--app-body { flex-direction: column; }
    .cds--sidenav {
      width: 100%; flex: none; position: relative; top: 0; height: auto;
      flex-direction: row; overflow-x: auto; border-right: none;
      border-bottom: 1px solid var(--cds-border-subtle-01);
    }
    .cds--sidenav-nav { display: flex; padding: var(--cds-spacing-03); flex: none; }
    .cds--sidenav-item { border-left: none; border-bottom: 3px solid transparent; white-space: nowrap; }
    .cds--sidenav-item.is-active { border-left-color: transparent; border-bottom-color: var(--cds-metering-accent); }
    .cds--sidenav-section-label { display: none; }
    .cds--sidenav-status { display: none; }
  }

  /* Carbon Button */
  .cds--btn {
    height: 32px;
    padding: 0 16px;
    font-size: 13px;
    font-family: var(--cds-font-sans);
    font-weight: 400;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid transparent;
    cursor: pointer;
    text-decoration: none;
    transition: background-color 0.15s, border-color 0.15s;
  }
  .cds--btn--primary {
    background-color: var(--cds-interactive-01);
    color: #ffffff;
  }
  .cds--btn--primary:hover {
    background-color: var(--cds-interactive-01-hover);
  }
  .cds--btn--secondary {
    background-color: var(--cds-layer-01);
    color: var(--cds-text-primary);
    border-color: var(--cds-border-subtle-01);
  }
  .cds--btn--secondary:hover {
    background-color: var(--cds-layer-02);
    border-color: var(--cds-border-strong-01);
  }

  /* 2x Grid Layout */
  .cds--grid {
    max-width: 1440px;
    margin: 0 auto;
    padding: var(--cds-spacing-06);
  }

  .cds--page-header {
    margin-bottom: var(--cds-spacing-06);
    border-bottom: 1px solid var(--cds-border-subtle-01);
    padding-bottom: var(--cds-spacing-05);
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    flex-wrap: wrap;
    gap: var(--cds-spacing-04);
  }
  .cds--page-header h1 {
    font-size: 28px;
    font-weight: 300;
    letter-spacing: -0.5px;
  }
  .cds--page-header p {
    font-size: 14px;
    color: var(--cds-text-secondary);
    margin-top: 4px;
  }
  .cds--finance-grid {
    display: grid;
    grid-template-columns: minmax(280px, 1.2fr) repeat(3, minmax(220px, 1fr));
    gap: var(--cds-spacing-05);
    margin-bottom: var(--cds-spacing-06);
  }
  .cds--finance-card {
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-05);
    min-height: 184px;
    position: relative;
    overflow: hidden;
    transition: transform 0.18s cubic-bezier(0.2, 0, 0.38, 0.9), border-color 0.18s;
  }
  .cds--finance-card:hover {
    transform: translateY(-2px);
    border-color: var(--cds-border-strong-01);
  }
  .cds--finance-card--hero {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 160px;
    gap: var(--cds-spacing-05);
    align-items: center;
  }
  .cds--finance-label {
    font-size: 11px;
    text-transform: uppercase;
    color: var(--cds-text-helper);
    letter-spacing: 0.7px;
    margin-bottom: 6px;
  }
  .cds--finance-value {
    font-family: var(--cds-font-mono);
    font-size: 30px;
    font-weight: 300;
    color: var(--cds-text-primary);
    line-height: 1.1;
  }
  .cds--finance-copy {
    font-size: 12px;
    color: var(--cds-text-secondary);
    margin-top: 8px;
  }
  .cds--donut {
    width: 150px;
    height: 150px;
  }
  .cds--donut-bg {
    fill: none;
    stroke: var(--cds-layer-02);
    stroke-width: 14;
  }
  .cds--donut-fg {
    fill: none;
    stroke: var(--cds-support-success);
    stroke-width: 14;
    stroke-linecap: round;
    transform: rotate(-90deg);
    transform-origin: 50% 50%;
    transition: stroke-dasharray 0.7s cubic-bezier(0.2, 0, 0.38, 0.9), stroke 0.25s;
  }
  .cds--donut text {
    fill: var(--cds-text-primary);
    font-family: var(--cds-font-mono);
    font-size: 18px;
    font-weight: 600;
  }
  .cds--threshold-row {
    display: grid;
    grid-template-columns: 68px 1fr 54px;
    gap: 8px;
    align-items: center;
    font-size: 12px;
    color: var(--cds-text-secondary);
    margin-top: 12px;
  }
  .cds--limit-rail {
    height: 8px;
    background: var(--cds-layer-02);
    position: relative;
    overflow: hidden;
  }
  .cds--limit-fill {
    height: 100%;
    background: var(--cds-support-success);
    transition: width 0.7s cubic-bezier(0.2, 0, 0.38, 0.9), background-color 0.25s;
  }
  .cds--limit-marker {
    position: absolute;
    top: -2px;
    width: 1px;
    height: 12px;
    background: var(--cds-text-primary);
    opacity: 0.75;
  }
  .cds--finance-details {
    margin-top: 12px;
    border-top: 1px solid var(--cds-border-subtle-01);
    padding-top: 10px;
  }
  .cds--finance-details summary {
    cursor: pointer;
    color: var(--cds-interactive-01);
    font-size: 12px;
  }
  .cds--finance-details p {
    color: var(--cds-text-secondary);
    font-size: 12px;
    margin-top: 8px;
  }

  /* Legal Terms & Conditions Banner Card */
  .cds--terms-container {
    background-color: var(--cds-terms-bg);
    border: 1px solid var(--cds-border-subtle-01);
    border-left: 4px solid var(--cds-interactive-accent);
    padding: var(--cds-spacing-05);
    margin-bottom: var(--cds-spacing-06);
  }
  .cds--terms-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: var(--cds-spacing-03);
    flex-wrap: wrap;
    gap: 8px;
  }
  .cds--terms-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--cds-text-primary);
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .cds--insight-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
    gap: var(--cds-spacing-05);
    margin-bottom: var(--cds-spacing-06);
  }
  .cds--panel {
    background-color: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-05);
  }
  .cds--panel-title {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: var(--cds-spacing-04);
  }
  .cds--release-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: var(--cds-spacing-04);
  }
  .cds--release-card {
    border: 1px solid var(--cds-border-subtle-01);
    background: var(--cds-background);
    padding: var(--cds-spacing-04);
  }
  .cds--release-card h3 {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .cds--kv {
    display: grid;
    grid-template-columns: 120px 1fr;
    gap: 4px 10px;
    font-size: 12px;
    color: var(--cds-text-secondary);
  }
  .cds--kv strong {
    color: var(--cds-text-primary);
    font-weight: 500;
  }
  .cds--callout {
    border-left: 4px solid var(--cds-support-info);
    background: var(--cds-layer-02);
    padding: var(--cds-spacing-04);
    font-size: 12px;
    color: var(--cds-text-secondary);
  }
  .cds--callout strong {
    display: block;
    color: var(--cds-text-primary);
    font-size: 13px;
    margin-bottom: 4px;
  }
  .cds--endpoint-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: var(--cds-spacing-04);
    margin-bottom: var(--cds-spacing-07);
  }
  .cds--source-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: var(--cds-spacing-04);
    margin-bottom: var(--cds-spacing-06);
  }
  .cds--source-card {
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-04);
  }
  .cds--source-card h3 {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .cds--source-card p,
  .cds--source-card li {
    font-size: 12px;
    color: var(--cds-text-secondary);
  }
  .cds--source-card ul {
    padding-left: 16px;
    margin: 8px 0;
  }
  .cds--source-card a {
    color: var(--cds-interactive-01);
    word-break: break-word;
  }
  .cds--option-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: var(--cds-spacing-04);
    margin-bottom: var(--cds-spacing-07);
  }
  .cds--option-card {
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-04);
  }
  .cds--option-card h3 {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .cds--option-card p {
    font-size: 12px;
    color: var(--cds-text-secondary);
    margin-top: 8px;
  }
  .cds--dependency-explorer {
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    margin-bottom: var(--cds-spacing-07);
    overflow: hidden;
  }
  .cds--neo-layout {
    display: grid;
    grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.7fr);
    gap: 1px;
    background: var(--cds-border-subtle-01);
  }
  .cds--neo-canvas {
    min-height: 820px;
    background:
      radial-gradient(ellipse 60% 45% at 50% 18%, var(--cds-metering-accent-dim), transparent 70%),
      linear-gradient(90deg, var(--cds-canvas-grid) 1px, transparent 1px),
      linear-gradient(0deg, var(--cds-canvas-grid) 1px, transparent 1px),
      var(--cds-background);
    background-size: 100% 100%, 32px 32px, 32px 32px;
    position: relative;
    overflow: hidden;
  }
  #dependency-graph-cy {
    width: 100%;
    height: 820px;
    display: block;
    opacity: 0;
    transition: opacity 0.6s ease;
  }
  #dependency-graph-cy.is-ready { opacity: 1; }
  .cds--graph-controls {
    position: absolute;
    top: 12px;
    right: 12px;
    z-index: 2;
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
  }
  .cds--zoom-readout {
    font-family: var(--cds-font-mono);
    font-size: 11px;
    color: var(--cds-text-helper);
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: 5px 10px;
    min-width: 46px;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }
  .cds--graph-controls .cds--raw-link {
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: 5px 10px;
    font-family: var(--cds-font-mono);
    font-size: 11px;
    color: var(--cds-text-secondary);
    cursor: pointer;
    transition: border-color 0.15s, color 0.15s;
  }
  .cds--graph-controls .cds--raw-link:hover {
    border-color: var(--cds-metering-accent);
    color: var(--cds-text-primary);
  }
  .cds--graph-controls .cds--raw-link.is-active {
    border-color: var(--cds-metering-accent);
    color: var(--cds-metering-accent);
    background: var(--cds-metering-accent-dim);
  }
  .cds--neo-side {
    background: var(--cds-layer-01);
    padding: var(--cds-spacing-05);
    min-height: 560px;
  }
  .cds--neo-side h3 {
    font-size: 16px;
    font-weight: 500;
    margin-bottom: 8px;
  }
  .cds--neo-side p,
  .cds--neo-side li {
    color: var(--cds-text-secondary);
    font-size: 12px;
  }
  .cds--neo-side ul {
    padding-left: 16px;
    margin-top: 10px;
  }
  .cds--dependency-detail-card {
    border: 1px solid var(--cds-border-subtle-01);
    background: var(--cds-background);
    padding: var(--cds-spacing-04);
    margin-top: var(--cds-spacing-04);
  }
  .cds--dependency-detail-card h4 {
    font-size: 15px;
    font-weight: 600;
    margin-bottom: 8px;
  }
  .cds--dependency-facts {
    display: grid;
    grid-template-columns: 96px 1fr;
    gap: 6px 10px;
    margin-top: 10px;
    font-size: 12px;
  }
  .cds--dependency-facts strong {
    color: var(--cds-text-primary);
    font-weight: 500;
  }
  .cds--neo-legend {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
  }
  .cds--legend-dot {
    width: 9px;
    height: 9px;
    display: inline-block;
    border-radius: 50%;
    margin-right: 5px;
  }
  .cds--dependency-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: var(--cds-spacing-04) var(--cds-spacing-05);
    border-bottom: 1px solid var(--cds-border-subtle-01);
    background: var(--cds-layer-02);
    flex-wrap: wrap;
  }
  .cds--graph-node-meta {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 6px;
  }
  .cds--relationship-list {
    padding: var(--cds-spacing-05);
    border-top: 1px solid var(--cds-border-subtle-01);
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: var(--cds-spacing-04);
    max-height: 260px;
    overflow: auto;
  }
  .cds--relationship-card {
    border: 1px solid var(--cds-border-subtle-01);
    background: var(--cds-background);
    padding: var(--cds-spacing-04);
    cursor: pointer;
    transition: border-color 0.18s, transform 0.18s cubic-bezier(0.2, 0, 0.38, 0.9);
  }
  .cds--relationship-card:hover {
    border-color: var(--cds-interactive-01);
    transform: translateY(-1px);
  }
  .cds--relationship-card h3 {
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 6px;
  }
  .cds--relationship-card p {
    font-size: 12px;
    color: var(--cds-text-secondary);
  }
  .cds--endpoint-card {
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-04);
    min-height: 116px;
    transition: transform 0.18s cubic-bezier(0.2, 0, 0.38, 0.9), border-color 0.18s;
  }
  .cds--endpoint-card:hover {
    transform: translateY(-1px);
    border-color: var(--cds-border-strong-01);
  }
  .cds--endpoint-card code {
    display: inline-block;
    margin-bottom: 8px;
  }
  .cds--endpoint-card p {
    font-size: 12px;
    color: var(--cds-text-secondary);
  }
  .cds--method-row {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 10px;
  }
  .cds--method-chip,
  .cds--raw-link {
    border: 1px solid var(--cds-border-subtle-01);
    background: var(--cds-layer-02);
    color: var(--cds-text-primary);
    cursor: pointer;
    font-family: var(--cds-font-mono);
    font-size: 11px;
    min-height: 24px;
    padding: 2px 8px;
  }
  .cds--method-chip:hover,
  .cds--raw-link:hover {
    border-color: var(--cds-interactive-01);
    color: var(--cds-interactive-01);
  }
  .cds--method-chip[aria-disabled="true"] {
    color: var(--cds-text-helper);
    cursor: not-allowed;
    opacity: 0.62;
  }
  .cds--service-card details {
    margin-top: 10px;
  }
  .cds--service-card summary {
    cursor: pointer;
    color: var(--cds-interactive-01);
    font-size: 12px;
    list-style: none;
  }
  .cds--service-card summary::-webkit-details-marker { display: none; }
  .cds--service-card summary::after {
    content: "+";
    margin-left: 6px;
  }
  .cds--service-card details[open] summary::after {
    content: "-";
  }

  /* Content Switcher */
  .cds--action-bar {
    background-color: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: 12px 16px;
    margin-bottom: var(--cds-spacing-06);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--cds-spacing-04);
  }
  .cds--content-switcher {
    display: inline-flex;
    background-color: var(--cds-layer-02);
    border: 1px solid var(--cds-border-subtle-01);
    height: 32px;
  }
  .cds--content-switcher-btn {
    padding: 0 14px;
    font-size: 12px;
    font-family: var(--cds-font-sans);
    background: transparent;
    color: var(--cds-text-secondary);
    border: none;
    cursor: pointer;
    height: 100%;
    display: inline-flex;
    align-items: center;
    transition: background-color 0.15s, color 0.15s;
  }
  .cds--content-switcher-btn.cds--content-switcher--selected {
    background-color: var(--cds-interactive-01);
    color: #ffffff;
    font-weight: 500;
  }

  /* High Level Metric Overview Tiles */
  .cds--grid-overview {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: var(--cds-spacing-05);
    margin-bottom: var(--cds-spacing-06);
  }
  .cds--tile {
    background-color: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-05);
    position: relative;
    transition: border-color 0.15s;
  }
  .cds--tile:hover {
    border-color: var(--cds-border-strong-01);
  }
  .cds--tile__label {
    font-size: 12px;
    font-weight: 400;
    color: var(--cds-text-secondary);
    margin-bottom: var(--cds-spacing-02);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .cds--tile__value {
    font-size: 32px;
    font-weight: 300;
    font-family: var(--cds-font-mono);
    color: var(--cds-text-primary);
    margin-bottom: var(--cds-spacing-02);
  }
  .cds--tile__helper {
    font-size: 12px;
    color: var(--cds-text-helper);
  }
  .cds--progress-bar {
    width: 100%;
    height: 4px;
    background-color: var(--cds-layer-02);
    margin-top: 12px;
    overflow: hidden;
    position: relative;
  }
  .cds--progress-bar__fill {
    height: 100%;
    background-color: var(--cds-interactive-01);
    position: relative;
    transition: width 0.65s cubic-bezier(0.2, 0, 0.38, 0.9), background-color 0.25s;
  }
  .cds--progress-bar__fill::after {
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.24), transparent);
    transform: translateX(-100%);
    animation: cdsBarSweep 2.6s ease-in-out infinite;
  }
  .cds--mini-bars {
    display: grid;
    grid-template-columns: repeat(12, 1fr);
    gap: 3px;
    height: 34px;
    align-items: end;
    margin-top: 12px;
  }
  .cds--mini-bars span {
    display: block;
    min-height: 5px;
    background: var(--cds-interactive-01);
    opacity: 0.42;
    animation: cdsPulseBar 2.4s ease-in-out infinite;
  }
  .cds--mini-bars span:nth-child(2n) { animation-delay: 0.18s; opacity: 0.58; }
  .cds--mini-bars span:nth-child(3n) { animation-delay: 0.34s; opacity: 0.78; }
  .cds--mini-bars span:nth-child(4n) { animation-delay: 0.52s; }
  @keyframes cdsBarSweep {
    0%, 45% { transform: translateX(-100%); }
    100% { transform: translateX(100%); }
  }
  @keyframes cdsPulseBar {
    0%, 100% { filter: brightness(0.82); }
    50% { filter: brightness(1.18); }
  }

  /* Carbon Tags */
  .cds--tag {
    display: inline-flex;
    align-items: center;
    font-size: 11px;
    font-weight: 400;
    padding: 2px 8px;
    border-radius: 10px;
    font-family: var(--cds-font-mono);
    letter-spacing: 0.2px;
  }
  .cds--tag--blue { background-color: var(--cds-tag-bg-blue); color: var(--cds-tag-color-blue); }
  .cds--tag--purple { background-color: var(--cds-tag-bg-purple); color: var(--cds-tag-color-purple); }
  .cds--tag--green { background-color: var(--cds-tag-bg-green); color: var(--cds-tag-color-green); }
  .cds--tag--gray { background-color: var(--cds-tag-bg-gray); color: var(--cds-tag-color-gray); }
  .cds--tag--red { background-color: var(--cds-tag-bg-red); color: var(--cds-tag-color-red); }

  /* Services Grid */
  .cds--section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin: var(--cds-spacing-07) 0 var(--cds-spacing-04) 0;
    flex-wrap: wrap;
    gap: 12px;
  }
  .cds--section-title {
    font-size: 20px;
    font-weight: 400;
    letter-spacing: -0.3px;
  }
  .cds--services-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: var(--cds-spacing-05);
    margin-bottom: var(--cds-spacing-07);
  }
  .cds--service-card {
    background-color: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-05);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    cursor: pointer;
    transition: transform 0.18s cubic-bezier(0.2, 0, 0.38, 0.9), border-color 0.18s;
    /* Every card gets the same height regardless of row/content — the two variable-length
       text blocks below are line-clamped to a fixed number of lines so the shape is
       predictable; the click-through drawer shows the untruncated text. */
    height: 100%;
  }
  .cds--service-card:hover {
    border-color: var(--cds-border-strong-01);
    transform: translateY(-2px);
  }
  .cds--svc-top {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: var(--cds-spacing-02);
  }
  .cds--svc-cat {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--cds-text-helper);
    letter-spacing: 0.5px;
  }
  .cds--svc-name {
    font-size: 15px;
    font-weight: 600;
    margin-top: 2px;
  }
  .cds--svc-desc {
    font-size: 12px;
    color: var(--cds-text-secondary);
    margin: 8px 0 10px 0;
    height: 32px;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .cds--license-terms-box {
    background-color: var(--cds-layer-02);
    border-left: 3px solid var(--cds-interactive-accent);
    padding: 8px 10px;
    font-size: 11px;
    color: var(--cds-text-secondary);
    margin-bottom: 12px;
    border-radius: 0 2px 2px 0;
    height: 84px;
    display: flex;
    flex-direction: column;
  }
  .cds--license-terms-box strong {
    color: var(--cds-text-primary);
    display: block;
    margin-bottom: 2px;
    flex: none;
  }
  .cds--license-terms-box .cds--clamp-text {
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .cds--license-terms-box .cds--deps-line {
    display: none;
  }
  .cds--meta-row {
    display: flex;
    gap: 6px;
    margin-bottom: 12px;
    flex-wrap: wrap;
  }
  .cds--metric-item {
    margin-top: 8px;
  }
  .cds--metric-label {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    color: var(--cds-text-secondary);
    margin-bottom: 4px;
  }
  .cds--metric-value {
    font-family: var(--cds-font-mono);
    font-weight: 600;
    color: var(--cds-text-primary);
  }

  code.cds--snippet {
    font-family: var(--cds-font-mono);
    font-size: 12px;
    background: var(--cds-layer-02);
    padding: 2px 6px;
  }

  /* Modal */
  .cds--modal-backdrop {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.6);
    display: none;
    align-items: center;
    justify-content: center;
    z-index: 200;
  }
  .cds--modal {
    background: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-06);
    width: 100%;
    max-width: 720px;
    max-height: min(90vh, 820px);
    overflow-y: auto;
  }
  .cds--modal h2 { font-size: 18px; font-weight: 400; margin-bottom: 16px; }
  .cds--raw-modal {
    max-width: min(960px, calc(100vw - 32px));
  }
  .cds--raw-modal pre {
    max-height: min(62vh, 620px);
    overflow: auto;
    background: var(--cds-background);
    border: 1px solid var(--cds-border-subtle-01);
    color: var(--cds-text-primary);
    font-family: var(--cds-font-mono);
    font-size: 12px;
    line-height: 1.45;
    padding: var(--cds-spacing-04);
    white-space: pre-wrap;
    word-break: break-word;
  }
  .cds--detail-section {
    margin-bottom: var(--cds-spacing-05);
  }
  .cds--detail-section h4 {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: var(--cds-text-helper);
    margin-bottom: 6px;
  }
  .cds--detail-section-body {
    font-size: 13px;
    color: var(--cds-text-primary);
    line-height: 1.55;
  }
  .cds--detail-kv {
    display: grid;
    grid-template-columns: minmax(120px, 160px) 1fr;
    gap: 6px 12px;
    font-size: 12px;
  }
  .cds--detail-kv dt { color: var(--cds-text-helper); }
  .cds--detail-kv dd { color: var(--cds-text-primary); font-family: var(--cds-font-mono); }
  .cds--yaml-viewer {
    font-family: var(--cds-font-mono) !important;
  }
  .yaml-key { color: var(--cds-interactive-01); }
  .yaml-string { color: var(--cds-support-success); }
  .yaml-number { color: var(--cds-metering-accent); }
  .yaml-bool { color: #be95ff; }
  .yaml-comment { color: var(--cds-text-helper); font-style: italic; }
  .cds--form-item { margin-bottom: 14px; }
  .cds--form-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0 var(--cds-spacing-05);
  }
  .cds--label { display: block; font-size: 12px; color: var(--cds-text-secondary); margin-bottom: 4px; }
  .cds--text-input {
    width: 100%;
    height: 36px;
    background: var(--cds-layer-02);
    border: 1px solid var(--cds-border-subtle-01);
    color: var(--cds-text-primary);
    padding: 0 10px;
    font-size: 13px;
    font-family: var(--cds-font-sans);
  }
  .cds--service-picker-note {
    min-height: 88px;
    background: var(--cds-layer-02);
    border-left: 3px solid var(--cds-interactive-01);
    padding: 10px 12px;
    margin-bottom: 16px;
    color: var(--cds-text-secondary);
    font-size: 12px;
  }
  .cds--service-picker-note strong {
    color: var(--cds-text-primary);
    display: block;
    margin-bottom: 4px;
  }

  .cds--footer {
    border-top: 1px solid var(--cds-border-subtle-01);
    padding: var(--cds-spacing-06) 0;
    font-size: 12px;
    color: var(--cds-text-helper);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }
  @media (max-width: 900px) {
    .cds--insight-grid,
    .cds--release-grid,
    .cds--finance-grid,
    .cds--neo-layout {
      grid-template-columns: 1fr;
    }
    .cds--finance-card--hero {
      grid-template-columns: 1fr;
    }
    .cds--header {
      height: auto;
      min-height: 48px;
      padding: 8px 16px;
      align-items: flex-start;
      gap: 10px;
    }
    .cds--header__global {
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .cds--grid {
      padding: 16px;
    }
    .cds--form-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
</head>
<body>

<header class="cds--header">
  <a href="#" class="cds--header__name">
    <strong>IBM</strong>&nbsp;Software Hub License Metering
  </a>
  <div class="cds--header__global">
    <button class="cds--btn cds--btn--secondary" onclick="toggleDarkMode()" id="btn-mode">
      <span id="mode-text">Dark Mode</span>
    </button>
    <button class="cds--btn cds--btn--secondary" onclick="openAddModal()">
      + Add Supported Service
    </button>
    <button class="cds--btn cds--btn--secondary" onclick="exportSnapshot()">
      Export Audit Package
    </button>
    <button class="cds--btn cds--btn--primary" onclick="fetchData()">
      Refresh
    </button>
  </div>
</header>

<div class="cds--app-body">
<nav class="cds--sidenav" aria-label="Dashboard sections">
  <div class="cds--sidenav-nav">
    <div class="cds--sidenav-section-label">Monitor</div>
    <button class="cds--sidenav-item is-active" data-view="overview" onclick="switchView('overview')">
      <span class="cds--nav-icon"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.5" y="1.5" width="6" height="6" rx="1"/><rect x="8.5" y="1.5" width="6" height="6" rx="1"/><rect x="1.5" y="8.5" width="6" height="6" rx="1"/><rect x="8.5" y="8.5" width="6" height="6" rx="1"/></svg></span> Overview
    </button>
    <button class="cds--sidenav-item" data-view="services" onclick="switchView('services')">
      <span class="cds--nav-icon"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8 1.5 14.5 5 8 8.5 1.5 5Z"/><path d="M1.5 8.5 8 12l6.5-3.5"/><path d="M1.5 11.5 8 15l6.5-3.5"/></svg></span> Services <span class="cds--nav-badge" id="nav-badge-services"></span>
    </button>
    <button class="cds--sidenav-item" data-view="graph" onclick="switchView('graph')">
      <span class="cds--nav-icon"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4"><circle cx="3" cy="3.5" r="1.8"/><circle cx="13" cy="3.5" r="1.8"/><circle cx="8" cy="13" r="1.8"/><path d="M4.5 4.6 6.7 11M11.5 4.6 9.3 11M4.8 3.5h6.4"/></svg></span> Dependency Graph
    </button>
    <div class="cds--sidenav-section-label">Governance</div>
    <button class="cds--sidenav-item" data-view="compliance" onclick="switchView('compliance')">
      <span class="cds--nav-icon"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8 1.5 14 3.5v4c0 4-2.7 6.3-6 7-3.3-.7-6-3-6-7v-4Z"/><path d="M5.3 8 7.3 10 10.8 6" stroke-linecap="round"/></svg></span> Compliance &amp; Audit
    </button>
    <button class="cds--sidenav-item" data-view="reference" onclick="switchView('reference')">
      <span class="cds--nav-icon"><svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3.2C6.8 2.3 4.8 1.8 2 1.8v10.4c2.8 0 4.8.5 6 1.4 1.2-.9 3.2-1.4 6-1.4V1.8c-2.8 0-4.8.5-6 1.4Z"/><path d="M8 3.2v10.4"/></svg></span> Reference
    </button>
  </div>
  <div class="cds--sidenav-status" id="sidenav-status">
    <div class="cds--status-row">
      <span class="cds--status-label">Cluster</span>
      <span class="cds--status-value" id="status-cluster-url" title="">--</span>
    </div>
    <div class="cds--status-row">
      <span class="cds--status-label">License Service</span>
      <span class="cds--status-value" style="display:flex; align-items:center; gap:5px; justify-content:flex-end;">
        <span class="cds--status-dot is-loading" id="status-license-dot"></span>
        <span id="status-license-text">checking&hellip;</span>
      </span>
    </div>
    <div class="cds--status-row">
      <span class="cds--status-label">Updated</span>
      <span class="cds--status-value" id="status-updated">--</span>
    </div>
  </div>
</nav>

<div class="cds--shell-main">
<div class="cds--fetch-bar" id="fetch-bar"></div>
<main class="cds--grid">

  <div class="cds--page-header">
    <div>
      <h1>Software Hub / CPD License Metering</h1>
      <p>Customer-ready VPC/RU visibility for Software Hub 5.3/5.4 and watsonx 2.3/2.4 installations</p>
    </div>
    <div style="font-size:12px; color:var(--cds-text-helper);">
      Status: <span class="cds--tag cds--tag--green" id="cluster-status">Connected</span> | Auto-Refresh: 10s
    </div>
  </div>

  <section class="cds--view-panel" data-view-panel="overview">
    <section class="cds--finance-grid" id="finance-command-center"></section>

    <!-- High-Level Metric Tiles (Carbon 2x Grid) -->
    <div class="cds--grid-overview">
      <div class="cds--tile">
        <div class="cds--tile__label">
          <span>VPC reported</span>
          <span class="cds--tag cds--tag--green" id="vpc-status-tag">Within limit</span>
        </div>
        <div class="cds--tile__value" id="tot-vpc"><span class="cds--spinner"></span></div>
        <div class="cds--tile__helper" id="vpc-helper">VPC entitlement from IBM_VPC_ENTITLEMENT</div>
        <div id="vpc-mini-bars"></div>
        <div class="cds--progress-bar"><div class="cds--progress-bar__fill" id="vpc-bar" style="background-color:var(--cds-interactive-accent); width:0%;"></div></div>
      </div>

      <div class="cds--tile">
        <div class="cds--tile__label">
          <span>RU reported</span>
          <span id="cpu-pct-tag">--%</span>
        </div>
        <div class="cds--tile__value" id="tot-ru"><span class="cds--spinner"></span></div>
        <div class="cds--tile__helper" id="ru-helper">RU entitlement from IBM_RU_ENTITLEMENT</div>
        <div id="ru-mini-bars"></div>
        <div class="cds--progress-bar"><div class="cds--progress-bar__fill" id="cpu-bar" style="width:0%;"></div></div>
      </div>

      <div class="cds--tile">
        <div class="cds--tile__label">
          <span>CPU allocation</span>
          <span id="mem-pct-tag">--%</span>
        </div>
        <div class="cds--tile__value" id="tot-cpu"><span class="cds--spinner"></span></div>
        <div class="cds--tile__helper">Live/query fallback: container CPU usage vs limits</div>
        <div id="cpu-mini-bars"></div>
        <div class="cds--progress-bar"><div class="cds--progress-bar__fill" id="mem-bar" style="background-color:var(--cds-support-success); width:0%;"></div></div>
      </div>

      <div class="cds--tile">
        <div class="cds--tile__label">
          <span>Discovered Stack Services</span>
          <span class="cds--tag cds--tag--blue" id="live-cr-tag">CRDs Active</span>
        </div>
        <div class="cds--tile__value" id="svc-count-val"><span class="cds--spinner"></span></div>
        <div class="cds--tile__helper">Operators Reconciled</div>
        <div class="cds--progress-bar"><div class="cds--progress-bar__fill" style="background-color:var(--cds-support-info); width:100%;"></div></div>
      </div>
    </div>

    <!-- IBM release and license posture -->
    <div class="cds--terms-container">
      <div class="cds--terms-header">
        <div class="cds--terms-title">
          <span>Compliance posture</span>
          <span class="cds--tag cds--tag--green">watsonx.data standard / non-premium</span>
        </div>
        <div style="font-size:12px; color:var(--cds-text-helper);">
          Premium reference: <strong>L-PCPF-BJV4WW</strong> only when entitlement says Premium &middot; full guardrails under <a href="#" onclick="switchView('reference'); return false;" style="color:var(--cds-interactive-01);">Reference</a>
        </div>
      </div>
    </div>

    <div class="cds--insight-grid">
      <section class="cds--panel">
        <h2 class="cds--panel-title">Supported release map</h2>
        <div class="cds--release-grid" id="release-grid"></div>
      </section>
      <aside class="cds--callout">
        <strong>How to read this dashboard</strong>
        The product table comes from IBM License Service. The service cards add CRD sizing,
        pod CPU limits, and memory working set to explain why a t-shirt size or entitlement
        threshold is being approached. This is presentation evidence, not a replacement for IBM terms.
      </aside>
    </div>
  </section>

  <section class="cds--view-panel" data-view-panel="services" hidden>
    <!-- Content Switcher: Live vs. Simulated T-Shirt Sizing Profile -->
    <div class="cds--action-bar">
      <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
        <span style="font-size:12px; font-weight:600; text-transform:uppercase; color:var(--cds-text-secondary); letter-spacing:0.5px;">Inspect / Override CR Sizing:</span>
        <div class="cds--content-switcher">
          <button class="cds--content-switcher-btn cds--content-switcher--selected" onclick="setSimScale('live', this)">Live CRDs</button>
          <button class="cds--content-switcher-btn" onclick="setSimScale('small_mincpureq', this)">small_mincpureq (Lab)</button>
          <button class="cds--content-switcher-btn" onclick="setSimScale('small', this)">small (Std)</button>
          <button class="cds--content-switcher-btn" onclick="setSimScale('medium', this)">medium (Dept)</button>
          <button class="cds--content-switcher-btn" onclick="setSimScale('large', this)">large (Enterprise)</button>
        </div>
      </div>
    </div>

    <div class="cds--section-header">
      <h2 class="cds--section-title">Installed Services, CRD Sizing &amp; License Terms</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);">
        Showing Live Custom Resource Sizing (<code>spec.scaleConfig</code>) &amp; Dependencies
      </div>
    </div>
    <div class="cds--services-grid" id="services-grid">
      <!-- Dynamic Service Cards -->
    </div>
  </section>

  <section class="cds--view-panel" data-view-panel="graph" hidden>
    <div class="cds--section-header">
      <h2 class="cds--section-title">Product, add-on and dependency explorer</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);">
        Separates core products, optional install options, dependencies, integrated add-ons, and Premium references
      </div>
    </div>
    <div class="cds--dependency-explorer" id="dependency-explorer"></div>
  </section>

  <section class="cds--view-panel" data-view-panel="compliance" hidden>
    <!-- License compliance controls: node pinning + namespace quota — real, checkable signals -->
    <div class="cds--section-header">
      <h2 class="cds--section-title">License compliance controls</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);">Live checks against IBM's documented entitlement-application mechanism</div>
    </div>
    <div class="cds--endpoint-grid" id="compliance-controls-grid" style="margin-bottom:var(--cds-spacing-07);"></div>

    <!-- WKC/IKC sub-feature enablement: verified by CR toggle + real workload, not just CR presence -->
    <div class="cds--section-header">
      <h2 class="cds--section-title">Knowledge Catalog sub-features</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);">A sub-feature CR can exist and report "Completed" while functionally disabled — each verdict below cross-checks the parent toggle field and real running workloads, not CR presence alone</div>
    </div>
    <div class="cds--endpoint-grid" id="wkc-features-grid" style="margin-bottom:var(--cds-spacing-07);"></div>

    <!-- IBM License Service Registered Products -->
    <div class="cds--section-header">
      <h2 class="cds--section-title">IBM License Service &mdash; registered products</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);">
        IBM License Service REST API: <code>/products</code> &middot; click a card for the raw response row
      </div>
    </div>
    <div class="cds--endpoint-grid" id="license-products-grid" style="margin-bottom:var(--cds-spacing-07);"></div>

    <div class="cds--section-header">
      <h2 class="cds--section-title">License Service API coverage</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);">
        Shows what the standalone collector can use from IBM License Service
      </div>
    </div>
    <div class="cds--endpoint-grid" id="endpoint-grid"></div>
  </section>

  <section class="cds--view-panel" data-view-panel="reference" hidden>
    <div class="cds--section-header">
      <h2 class="cds--section-title">Compliance guardrails &amp; interpretation rules</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);">How this dashboard reads IBM License Service data — one rule per card, not a wall of text</div>
    </div>
    <div class="cds--option-grid" id="guardrails-grid" style="margin-bottom:var(--cds-spacing-07);"></div>

    <div class="cds--section-header">
      <h2 class="cds--section-title">IBM source map</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);">
        Public IBM pages used for release, entitlement, dependency, and metric interpretation
      </div>
    </div>
    <div class="cds--source-grid" id="source-grid" style="margin-bottom:var(--cds-spacing-07);"></div>

    <div class="cds--section-header">
      <h2 class="cds--section-title">Install options and dependency impact</h2>
      <div style="font-size:12px; color:var(--cds-text-helper);" id="install-options-source">
        install-options.yml not loaded yet
      </div>
    </div>
    <div class="cds--option-grid" id="install-options-grid"></div>
  </section>

  <footer class="cds--footer">
    <div>Target: <span id="footer-ocp-info">api.watson.ibmas-zocp-techcluster.org:6443</span></div>
    <div>IBM Software Hub / CPD 5.3-5.4 Telemetry | Carbon-inspired presentation view</div>
  </footer>

</main>
</div>
</div>

<!-- Modal for Adding Custom Services dynamically -->
<div class="cds--modal-backdrop" id="add-modal">
  <div class="cds--modal">
    <h2>Add Software Hub Service to Metering Monitor</h2>
    <div class="cds--form-item">
      <label class="cds--label">Supported Software Hub service</label>
      <select class="cds--text-input" id="supported-svc-select" onchange="applySupportedServiceSelection()">
        <option value="">Loading supported services...</option>
      </select>
    </div>
    <div class="cds--service-picker-note" id="supported-svc-notes">
      <strong>Selection guidance</strong>
      Choose from Software Hub / Cloud Pak for Data supported component IDs. Adding a service here creates a monitor card; it does not assert entitlement or convert RU to VPC.
    </div>
    <div class="cds--form-grid">
      <div class="cds--form-item">
        <label class="cds--label">Service Identifier / component ID</label>
        <input type="text" class="cds--text-input" id="new-svc-id" placeholder="my_custom_service">
      </div>
      <div class="cds--form-item">
        <label class="cds--label">Display Name</label>
        <input type="text" class="cds--text-input" id="new-svc-name" placeholder="IBM Custom Analytics Service">
      </div>
      <div class="cds--form-item">
        <label class="cds--label">Category</label>
        <input type="text" class="cds--text-input" id="new-svc-cat" placeholder="Analytics &amp; AI">
      </div>
      <div class="cds--form-item">
        <label class="cds--label">Pod Name Pattern Regex</label>
        <input type="text" class="cds--text-input" id="new-svc-regex" placeholder="custom-svc-.*|my-app-.*">
      </div>
      <div class="cds--form-item">
        <label class="cds--label">CR Sizing / scaleConfig</label>
        <select class="cds--text-input" id="new-svc-scale">
          <option value="small_mincpureq">small_mincpureq</option>
          <option value="small" selected>small</option>
          <option value="medium">medium</option>
          <option value="large">large</option>
        </select>
      </div>
      <div class="cds--form-item">
        <label class="cds--label">Default VPC / core limit</label>
        <input type="number" class="cds--text-input" id="new-svc-limit" value="16">
      </div>
    </div>
    <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:20px;">
      <button class="cds--btn cds--btn--secondary" onclick="closeAddModal()">Cancel</button>
      <button class="cds--btn cds--btn--primary" onclick="submitCustomService()">Add Monitor</button>
    </div>
  </div>
</div>

<!-- Reusable raw JSON / evidence inspector -->
<div class="cds--modal-backdrop" id="raw-modal">
  <div class="cds--modal cds--raw-modal">
    <h2 id="raw-modal-title">Raw evidence</h2>
    <div id="raw-modal-body"></div>
    <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
      <button class="cds--btn cds--btn--secondary" onclick="copyRawInspector()">Copy</button>
      <button class="cds--btn cds--btn--primary" onclick="closeRawInspector()">Close</button>
    </div>
  </div>
</div>

<script src="/vendor/3d-force-graph.min.js"></script>
<script>
let currentScaleOverride = 'live';
let rawData = null;
let supportedServices = [];
let rawInspectorStore = {};
let rawInspectorNextId = 0;
let dependencyExplorerState = { nodesById: {}, edges: [], selectedId: null };
let dependencyGraph3D = null;
let dependencyGraphSignature = '';
let dependencyGraphHasBeenFocused = false;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

// One reusable drawer/modal for everything that needs "click for more detail" — either a
// quick raw-JSON dump (the original, simplest use) or a properly formatted panel (labeled
// sections, tags, a live YAML view) with the raw JSON kept as a collapsed "Advanced" block
// underneath rather than dropped. Which one renders is decided purely by which fields the
// caller populates, so every existing addRawInspectorItem(title, payload) call site keeps
// working unchanged.
// Keyed by an ever-increasing id that is NEVER reused or reset, unlike an array index.
// renderIfChanged() lets whole sections skip re-rendering when their data is unchanged, which
// means their onclick="openRawInspector(N)" markup can sit in the DOM for many fetch cycles
// after it was written — a plain "reset the array every render" scheme would silently repoint
// those stale N's at whatever new item happens to land on that slot next.
function addRawInspectorItem(title, payload) {
  const id = rawInspectorNextId++;
  rawInspectorStore[id] = { title, payload };
  return id;
}

function addDetailItem(title, { tags = [], sectionsHtml = '', payload } = {}) {
  const id = rawInspectorNextId++;
  rawInspectorStore[id] = { title, tags, sectionsHtml, payload };
  return id;
}

function detailSection(heading, bodyHtml) {
  return `<div class="cds--detail-section"><h4>${esc(heading)}</h4><div class="cds--detail-section-body">${bodyHtml}</div></div>`;
}

// Small, dependency-free YAML highlighter — good enough to make a live CR readable at a
// glance (keys, comments, list markers, quoted/numeric values each get a distinct color)
// without vendoring a full syntax-highlighting library for one use.
function highlightYaml(yamlText) {
  return String(yamlText ?? '').split('\\n').map(rawLine => {
    const line = esc(rawLine);
    if (/^\\s*#/.test(line)) return `<span class="yaml-comment">${line}</span>`;
    const m = line.match(/^(\\s*(?:-\\s+)?)([A-Za-z0-9_.\\/-]+)(:)(\\s*)(.*)$/);
    if (m) {
      const [, indent, key, colon, sp, rest] = m;
      if (!rest) return `${indent}<span class="yaml-key">${key}</span>${colon}`;
      const cls = /^(true|false|null)$/i.test(rest) ? 'yaml-bool' : isNaN(Number(rest)) ? 'yaml-string' : 'yaml-number';
      return `${indent}<span class="yaml-key">${key}</span>${colon}${sp}<span class="${cls}">${rest}</span>`;
    }
    return line;
  }).join('\\n');
}

let currentCrRequest = { serviceId: null, crName: null };

async function viewCrYaml(serviceId, crName, displayName) {
  const index = addDetailItem(`Live CR: ${displayName || serviceId}`, {
    sectionsHtml: `<div class="cds--yaml-viewer"><span class="cds--spinner"></span> Fetching live YAML…</div>`,
  });
  openRawInspector(index);
  currentCrRequest = { serviceId, crName };
  try {
    const url = `/api/cr/${encodeURIComponent(serviceId)}` + (crName ? `?name=${encodeURIComponent(crName)}` : '');
    const res = await fetch(url);
    const result = await res.json();
    // Guard against a slower-earlier request resolving after a faster-later one.
    if (currentCrRequest.serviceId !== serviceId || currentCrRequest.crName !== crName) return;
    const item = rawInspectorStore[index];
    if (result.status === 'ok') {
      item.sectionsHtml = `
        <div class="cds--method-row" style="margin-top:0;">
          <span class="cds--tag cds--tag--blue">${esc(result.resource)}</span>
          ${result.cr_name ? `<span class="cds--tag cds--tag--gray">${esc(result.cr_name)}</span>` : ''}
          <span class="cds--tag cds--tag--gray">ns: ${esc(result.namespace)}</span>
        </div>
        <pre class="cds--yaml-viewer">${highlightYaml(result.yaml)}</pre>
      `;
    } else {
      item.sectionsHtml = `<p style="color:var(--cds-support-danger); font-size:13px;">${esc(result.message || 'Could not fetch live CR YAML.')}</p>`;
    }
    if (document.getElementById('raw-modal').style.display === 'flex') openRawInspector(index);
  } catch (e) {
    const item = rawInspectorStore[index];
    item.sectionsHtml = `<p style="color:var(--cds-support-danger); font-size:13px;">Fetch failed: ${esc(e.message)}</p>`;
    if (document.getElementById('raw-modal').style.display === 'flex') openRawInspector(index);
  }
}

function openRawInspector(index) {
  const item = rawInspectorStore[index];
  if (!item) return;
  document.getElementById('raw-modal-title').innerText = item.title || 'Raw evidence';
  const body = document.getElementById('raw-modal-body');
  const tagsHtml = (item.tags || []).length
    ? `<div class="cds--method-row" style="margin-top:0; margin-bottom:12px;">${item.tags.map(t => `<span class="cds--tag ${t.cls || 'cds--tag--gray'}">${esc(t.label)}</span>`).join('')}</div>`
    : '';
  if (item.sectionsHtml !== undefined) {
    const rawJson = item.payload !== undefined
      ? `<details class="cds--finance-details" style="margin-top:14px;"><summary>Advanced: raw JSON</summary><pre>${esc(JSON.stringify(item.payload, null, 2))}</pre></details>`
      : '';
    body.innerHTML = tagsHtml + item.sectionsHtml + rawJson;
  } else {
    body.innerHTML = `<pre>${esc(JSON.stringify(item.payload ?? {}, null, 2))}</pre>`;
  }
  document.getElementById('raw-modal').style.display = 'flex';
}

function closeRawInspector() {
  document.getElementById('raw-modal').style.display = 'none';
}

async function copyRawInspector() {
  const text = document.getElementById('raw-modal-body').innerText;
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    console.warn('Clipboard copy failed', e);
  }
}

function renderMiniBars(values, color = 'var(--cds-interactive-01)') {
  const heights = values && values.length ? values : [35, 58, 42, 76, 61, 86, 54, 69, 47, 78, 64, 90];
  return `<div class="cds--mini-bars" aria-hidden="true">${heights.slice(0, 12).map(v => `<span style="height:${Math.max(5, Math.min(100, v))}%; background:${color};"></span>`).join('')}</div>`;
}

function tagForDependencyType(type, status) {
  if (status === 'not enabled') return 'cds--tag--gray';
  if (status === 'configured' || status === 'installed') return 'cds--tag--green';
  if (String(type).includes('premium')) return 'cds--tag--purple';
  if (String(type).includes('dependency')) return 'cds--tag--blue';
  return 'cds--tag--gray';
}

function dependencyTypeLabel(type) {
  return String(type || 'relationship').replace(/_/g, ' ');
}

function showDependencyNode(nodeId) {
  const node = dependencyExplorerState.nodesById[nodeId];
  if (!node) return;
  dependencyExplorerState.selectedId = nodeId;
  highlightDependencyGraph(nodeId);
  const inbound = dependencyExplorerState.edges.filter(edge => edge.to === nodeId);
  const outbound = dependencyExplorerState.edges.filter(edge => edge.from === nodeId);
  const status = node.option_status || (node.installed ? 'installed' : dependencyTypeLabel(node.type));
  const rawIndex = node.rawIndex;
  const related = [...inbound.map(edge => `${dependencyExplorerState.nodesById[edge.from]?.label || edge.from} -> this`), ...outbound.map(edge => `this -> ${dependencyExplorerState.nodesById[edge.to]?.label || edge.to}`)];
  const panel = document.getElementById('dependency-detail-panel');
  if (!panel) return;
  panel.innerHTML = `
    <div class="cds--dependency-detail-card">
      <div class="cds--graph-node-meta">
        <span class="cds--tag ${tagForDependencyType(node.type, status)}">${esc(status)}</span>
        <span class="cds--tag cds--tag--gray">${esc(dependencyTypeLabel(node.type))}</span>
      </div>
      <h4>${esc(node.label)}</h4>
      <p>${esc(node.metering || 'Verify the License Service product row and entitlement boundary before using this as a license conclusion.')}</p>
      <div class="cds--dependency-facts">
        <strong>Meaning</strong><span>${esc(node.certainty || 'Dashboard relationship catalog')}</span>
        <strong>Evidence</strong><span>${esc(node.evidence || 'Verify with install-options, live CRs, and IBM License Service.')}</span>
        <strong>Impact</strong><span>${esc(node.type && node.type.includes('premium') ? 'Premium reference only; not default entitlement.' : node.type && node.type.includes('dependency') ? 'Explains deployed pods; count only if License Service reports usage.' : 'Can change deployed capability; verify terms and product rows.')}</span>
      </div>
      ${related.length ? `<details class="cds--finance-details" open><summary>Related graph paths</summary><ul>${related.map(item => `<li>${esc(item)}</li>`).join('')}</ul></details>` : ''}
      <div class="cds--method-row"><button class="cds--raw-link" onclick="openRawInspector(${rawIndex})">View raw node evidence</button></div>
    </div>
  `;
}

function showDependencyEdge(edgeIndex) {
  const edge = dependencyExplorerState.edges[edgeIndex];
  if (!edge) return;
  dependencyExplorerState.selectedId = edge.from;
  highlightDependencyGraph(edge.from, edgeIndex);
  const from = dependencyExplorerState.nodesById[edge.from]?.label || edge.from;
  const to = dependencyExplorerState.nodesById[edge.to]?.label || edge.to;
  const panel = document.getElementById('dependency-detail-panel');
  if (!panel) return;
  panel.innerHTML = `
    <div class="cds--dependency-detail-card">
      <div class="cds--graph-node-meta">
        <span class="cds--tag ${edge.relationship.includes('premium') ? 'cds--tag--purple' : edge.relationship.includes('restricted') ? 'cds--tag--red' : 'cds--tag--blue'}">${esc(dependencyTypeLabel(edge.relationship))}</span>
        <span class="cds--tag cds--tag--gray">${esc(edge.status || 'relationship')}</span>
      </div>
      <h4>${esc(from)} -> ${esc(to)}</h4>
      <p>${esc(edge.license_boundary || edge.evidence || 'Relationship requires validation against entitlement and License Service rows.')}</p>
      <div class="cds--dependency-facts">
        <strong>From</strong><span>${esc(from)}</span>
        <strong>To</strong><span>${esc(to)}</span>
        <strong>Impact</strong><span>${esc(edge.evidence || 'Use this path to explain installed capability; do not double count dependencies.')}</span>
      </div>
      <div class="cds--method-row"><button class="cds--raw-link" onclick="openRawInspector(${edge.rawIndex})">View raw relationship evidence</button></div>
    </div>
  `;
}

function resolveCssColor(value) {
  const match = String(value || '').match(/^var\\((--[^)]+)\\)$/);
  if (!match) return value;
  return getComputedStyle(document.documentElement).getPropertyValue(match[1]).trim() || value;
}

function collectDependencyLineage(selectedId) {
  const edges = dependencyExplorerState.edges || [];
  const nodesById = dependencyExplorerState.nodesById || {};
  const nodeIds = new Set([selectedId]);
  const edgeIndexes = new Set();

  // Walk upstream: who deploys/contains the selected node?
  // Stop expansion at platform roots (software_hub, license_service) — we include
  // them as context nodes but do NOT continue downstream from them, which would
  // pull in all sibling products.
  const PLATFORM_ROOTS = new Set(['software_hub', 'license_service']);

  const visitUpstream = (startId) => {
    const queue = [startId];
    const seen = new Set([startId]);
    while (queue.length) {
      const currentId = queue.shift();
      edges.filter(edge => edge.to === currentId).forEach(edge => {
        const nextId = edge.from;
        if (!nodesById[nextId] || seen.has(nextId)) return;
        seen.add(nextId);
        nodeIds.add(nextId);
        edgeIndexes.add(edge.edgeIndex);
        // Include the platform root as context but don't walk further upstream from it
        if (!PLATFORM_ROOTS.has(nextId)) queue.push(nextId);
      });
    }
  };

  // Walk downstream: what does the selected node deploy/bundle?
  const visitDownstream = (startId) => {
    const queue = [startId];
    const seen = new Set([startId]);
    while (queue.length) {
      const currentId = queue.shift();
      // Don't fan out downstream from platform roots — avoids pulling in all siblings
      if (PLATFORM_ROOTS.has(currentId) && currentId !== startId) return;
      edges.filter(edge => edge.from === currentId).forEach(edge => {
        const nextId = edge.to;
        if (!nodesById[nextId] || seen.has(nextId)) return;
        seen.add(nextId);
        nodeIds.add(nextId);
        edgeIndexes.add(edge.edgeIndex);
        queue.push(nextId);
      });
    }
  };

  visitUpstream(selectedId);
  visitDownstream(selectedId);

  // For every node in the lineage, also pull in direct metering edges (license_service)
  // so compliance context is always visible.
  [...nodeIds].forEach(id => {
    edges.filter(edge => edge.from === id).forEach(edge => {
      const target = nodesById[edge.to];
      const relationship = String(edge.relationship || '');
      if (String(target?.type || '') === 'metering' || relationship.includes('measured_by')) {
        nodeIds.add(edge.to);
        edgeIndexes.add(edge.edgeIndex);
      }
    });
  });

  return { nodeIds, edgeIndexes };
}

// Single source of truth for node color/label so the on-canvas rendering, the legend, and
// buildForceGraphData's sphere colors can never drift apart. Order matters: first match
// wins, most specific checks first.
const DEPENDENCY_NODE_STYLES = [
  { match: t => t === 'metering',                              label: 'License metering',                    color: () => resolveCssColor('var(--cds-support-success)') },
  { match: t => t === 'platform',                               label: 'Platform root',                       color: () => resolveCssColor('var(--cds-layer-02)') },
  { match: t => t === 'platform_dependency',                    label: 'Platform dependency',                 color: () => resolveCssColor('var(--cds-layer-02)') },
  { match: t => t === 'core_product',                           label: 'Core product',                        color: () => resolveCssColor('var(--cds-interactive-01)') },
  { match: t => t.includes('premium'),                          label: 'Premium reference',                   color: () => '#7c3aed' },
  { match: t => t.includes('integration_component'),            label: 'Integration component (bundled RU)',  color: () => '#0e7490' },
  { match: t => t.includes('add_on') || t.includes('add-on'),   label: 'Add-on',                               color: () => '#0891b2' },
  { match: t => t.includes('option'),                           label: 'Install option',                      color: () => '#6366f1' },
  { match: t => t.includes('dependency'),                       label: 'Dependency',                          color: () => resolveCssColor('var(--cds-support-info)') },
];
const DEPENDENCY_NODE_STYLE_DEFAULT = { label: 'Other', color: () => resolveCssColor('var(--cds-support-info)') };

function dependencyNodeStyle(node) {
  const t = String(node?.type || '');
  return DEPENDENCY_NODE_STYLES.find(s => s.match(t)) || DEPENDENCY_NODE_STYLE_DEFAULT;
}

// The graph that replaced Cytoscape+ELK's strict top-down layered diagram: a WebGL 3D force
// simulation (3d-force-graph, Three.js bundled in — see app/vendor/3d-force-graph.min.js).
// Nodes are draggable spheres that spring back under the physics simulation ("free to slip"),
// the camera orbits with mouse drag / scroll-to-zoom out of the box, and animated particles
// flow along "measured_by"/"bundled_as_ru" edges via the library's own linkDirectionalParticles
// — no hand-rolled dash-offset animation loop needed this time.
function highlightDependencyGraph(selectedId, selectedEdgeIndex = null) {
  if (!dependencyGraph3D) return;
  const lineage = collectDependencyLineage(selectedId);
  const { nodes, links } = dependencyGraph3D.graphData();
  nodes.forEach(n => { n.__focused = n.id === selectedId; n.__dimmed = !lineage.nodeIds.has(n.id); });
  links.forEach(l => {
    l.__dimmed = !lineage.edgeIndexes.has(l.edgeIndex);
    l.__selected = selectedEdgeIndex !== null && l.edgeIndex === selectedEdgeIndex;
  });
  dependencyGraph3D.refresh();
  dependencyGraph3D.zoomToFit(500, 90, n => lineage.nodeIds.has(n.id));
}

function reheatDependencyGraph() {
  if (dependencyGraph3D) dependencyGraph3D.d3ReheatSimulation();
}

function resizeDependencyGraph() {
  if (!dependencyGraph3D) return;
  const container = document.getElementById('dependency-graph-cy');
  if (!container) return;
  dependencyGraph3D.width(container.clientWidth).height(container.clientHeight);
}

function fitDependencyGraph() {
  if (dependencyGraph3D) dependencyGraph3D.zoomToFit(400, 70);
}

function zoomDependencyGraph(factor) {
  if (!dependencyGraph3D) return;
  const pos = dependencyGraph3D.cameraPosition();
  dependencyGraph3D.cameraPosition({ x: pos.x * factor, y: pos.y * factor, z: pos.z * factor }, undefined, 260);
}

const prefersReducedMotion = () => typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let dependencyAutoRotate = false;
let dependencyRotateRafId = null;
function toggleDependencyRotate() {
  dependencyAutoRotate = !dependencyAutoRotate;
  if (dependencyAutoRotate) startDependencyRotate();
  else if (dependencyRotateRafId) cancelAnimationFrame(dependencyRotateRafId);
  const btn = document.getElementById('dependency-rotate-btn');
  if (btn) btn.classList.toggle('is-active', dependencyAutoRotate);
}
function startDependencyRotate() {
  if (!dependencyGraph3D || prefersReducedMotion()) return;
  let angle = 0;
  const step = () => {
    if (!dependencyAutoRotate) return;
    angle += 0.0022;
    const distance = 340;
    dependencyGraph3D.cameraPosition({ x: distance * Math.sin(angle), z: distance * Math.cos(angle) });
    dependencyRotateRafId = requestAnimationFrame(step);
  };
  dependencyRotateRafId = requestAnimationFrame(step);
}

// Same single-source-of-truth principle for edges: class drives the CSS (color + dash
// pattern, so relationship kind is never color-only — see the .edge-* rules), label+dash
// feed the legend. Keep `dash`/`swatchColor` in sync with the matching `.edge-*` CSS rule
// above if either changes.
const DEPENDENCY_EDGE_STYLES = [
  { match: r => r.includes('bundled_as_ru'),                                              cls: 'edge-bundled',    label: 'Bundled as RU',                  dash: 'solid',  swatchColor: '#0e7490' },
  { match: r => r.includes('platform_dependency') || r.includes('foundation_dependency'),  cls: 'edge-platform',   label: 'Platform dependency',            dash: 'dotted', swatchColor: 'var(--cds-border-subtle-01)' },
  { match: r => r.includes('optional') || r.includes('option'),                            cls: 'edge-optional',   label: 'Optional / install option',      dash: 'dashed', swatchColor: '#6366f1' },
  { match: r => r.includes('restricted'),                                                  cls: 'edge-restricted', label: 'Restricted dependency',          dash: 'dashed', swatchColor: 'var(--cds-support-danger)' },
  { match: r => r.includes('premium'),                                                     cls: 'edge-premium',    label: 'Premium reference',              dash: 'dashed', swatchColor: '#7c3aed' },
  { match: r => r.includes('edition_uplift'),                                              cls: 'edge-edition',    label: 'Edition uplift',                 dash: 'solid',  swatchColor: 'var(--cds-interactive-01)' },
  { match: r => r.includes('measured_by'),                                                 cls: 'edge-metering',   label: 'Measured by License Service',    dash: 'dashed', swatchColor: 'var(--cds-support-success)' },
];
const DEPENDENCY_EDGE_STYLE_DEFAULT = { cls: '', label: 'Deploys / depends on', dash: 'solid', swatchColor: 'var(--cds-border-strong-01)' };

function dependencyEdgeStyle(relationship) {
  const r = String(relationship || '');
  return DEPENDENCY_EDGE_STYLES.find(s => s.match(r)) || DEPENDENCY_EDGE_STYLE_DEFAULT;
}

function renderDependencyLegend() {
  const nodeRows = DEPENDENCY_NODE_STYLES.map(s => `
    <span><span class="cds--legend-dot" style="background:${s.color()};"></span>${esc(s.label)}</span>
  `).join('');
  const edgeRows = [...DEPENDENCY_EDGE_STYLES, DEPENDENCY_EDGE_STYLE_DEFAULT].map(s => `
    <span><span style="display:inline-block;width:22px;height:0;border-top:2px ${s.dash} ${s.swatchColor};margin-right:6px;vertical-align:middle;"></span>${esc(s.label)}</span>
  `).join('');
  return `
    <strong style="font-size:11px; color:var(--cds-text-secondary); text-transform:uppercase; letter-spacing:0.5px;">Node types</strong>
    ${nodeRows}
    <strong style="font-size:11px; color:var(--cds-text-secondary); text-transform:uppercase; letter-spacing:0.5px; margin-top:6px; display:block;">Relationship types</strong>
    ${edgeRows}
    <strong style="font-size:11px; color:var(--cds-text-secondary); text-transform:uppercase; letter-spacing:0.5px; margin-top:6px; display:block;">Node size</strong>
    <span><span class="cds--legend-dot" style="background:var(--cds-text-helper); width:14px; height:14px;"></span>Platform / metering (larger sphere)</span>
    <span><span class="cds--legend-dot" style="background:var(--cds-text-helper); width:8px; height:8px;"></span>Product / dependency (smaller sphere)</span>
  `;
}

let dependencyBundlesHidden = false;
function collapseDependencyBundles() {
  dependencyBundlesHidden = true;
  rebuildDependencyGraphData();
}
function expandDependencyGraph() {
  dependencyBundlesHidden = false;
  rebuildDependencyGraphData();
}

function buildForceGraphData(nodeMap, graphEdges) {
  const bundledChildIds = new Set(graphEdges.filter(e => e.relationship === 'bundled_as_ru').map(e => e.to));
  const nodes = Object.values(nodeMap)
    .filter(n => !(dependencyBundlesHidden && bundledChildIds.has(n.id)))
    .map(n => {
      const isHub = n.type === 'platform' || n.type === 'platform_dependency' || n.type === 'metering';
      return {
        id: n.id,
        name: n.label,
        type: n.type,
        installed: n.installed,
        val: isHub ? 11 : 5.5,
        baseColor: dependencyNodeStyle(n).color(),
      };
    });
  const nodeIds = new Set(nodes.map(n => n.id));
  const links = graphEdges
    .filter(e => nodeIds.has(e.from) && nodeIds.has(e.to))
    .map(e => ({
      source: e.from,
      target: e.to,
      relationship: e.relationship,
      edgeIndex: e.edgeIndex,
      baseColor: resolveCssColor(dependencyEdgeStyle(e.relationship).swatchColor),
    }));
  return { nodes, links };
}

let dependencyRawNodeMap = {};
let dependencyRawEdges = [];

function rebuildDependencyGraphData() {
  if (!dependencyGraph3D) return;
  dependencyGraph3D.graphData(buildForceGraphData(dependencyRawNodeMap, dependencyRawEdges));
}

// A WebGL init failure here (locked-down corporate GPU policy, old hardware, a headless
// test runner with GPU disabled) must never take down the rest of the dashboard — the whole
// fetchData() render cycle used to abort on this same exception, blanking products/compliance
// too. Isolate it and fall back to the plain-text relationship list, which is always rendered.
function initDependencyGraph3D(nodeMap, graphEdges) {
  const container = document.getElementById('dependency-graph-cy');
  if (!container || typeof ForceGraph3D === 'undefined') return;
  dependencyRawNodeMap = nodeMap;
  dependencyRawEdges = graphEdges;
  dependencyBundlesHidden = false;

  if (dependencyGraph3D) {
    container.innerHTML = '';
    dependencyGraph3D = null;
  }

  try {
    const dimColor = 'rgba(130,130,130,0.18)';
    const dimLinkColor = 'rgba(130,130,130,0.12)';
    const focusColor = resolveCssColor('var(--cds-metering-accent)');
    const backgroundColor = resolveCssColor('var(--cds-background)');

    dependencyGraph3D = ForceGraph3D()(container)
      .backgroundColor(backgroundColor)
      .graphData(buildForceGraphData(nodeMap, graphEdges))
      .nodeId('id')
      .nodeLabel(n => `${n.name}`)
      .nodeVal('val')
      .nodeColor(n => n.__focused ? focusColor : (n.__dimmed ? dimColor : n.baseColor))
      .nodeOpacity(0.94)
      .linkSource('source')
      .linkTarget('target')
      .linkColor(l => l.__selected ? focusColor : (l.__dimmed ? dimLinkColor : l.baseColor))
      .linkWidth(l => l.__selected ? 2.4 : (l.__dimmed ? 0.4 : 0.9))
      .linkOpacity(0.65)
      .linkDirectionalParticles(l => l.relationship === 'measured_by' ? 4 : (l.relationship === 'bundled_as_ru' ? 2 : 0))
      .linkDirectionalParticleWidth(1.8)
      .linkDirectionalParticleSpeed(0.006)
      .linkDirectionalParticleColor(() => focusColor)
      .onNodeClick(node => { dependencyGraphHasBeenFocused = true; showDependencyNode(node.id); })
      .onLinkClick(link => { dependencyGraphHasBeenFocused = true; showDependencyEdge(link.edgeIndex); })
      .onNodeDragEnd(node => { node.fx = node.x; node.fy = node.y; node.fz = node.z; })
      .width(container.clientWidth)
      .height(container.clientHeight);

    container.classList.add('is-ready');

    setTimeout(() => {
      if (dependencyExplorerState.selectedId && dependencyGraphHasBeenFocused) {
        showDependencyNode(dependencyExplorerState.selectedId);
      } else {
        fitDependencyGraph();
      }
    }, 700);
  } catch (err) {
    dependencyGraph3D = null;
    container.innerHTML = `
      <div style="display:flex; align-items:center; justify-content:center; height:100%; padding:32px; text-align:center; color:var(--cds-text-secondary); font-size:13px;">
        3D graph rendering is unavailable in this browser (WebGL failed to initialize: ${esc(err.message || err)}).<br>
        The full relationship list below the graph area covers the same data.
      </div>
    `;
  }
}


function riskColor(pct) {
  return pct >= 95 ? 'var(--cds-support-danger)' : pct >= 80 ? 'var(--cds-support-warning)' : 'var(--cds-support-success)';
}

function riskTagClass(pct) {
  return pct >= 95 ? 'cds--tag--red' : pct >= 80 ? 'cds--tag--purple' : 'cds--tag--green';
}

function riskLabel(pct, auditReady) {
  if (!auditReady) return 'Evidence review';
  if (pct >= 95) return 'Limit breach risk';
  if (pct >= 80) return 'Watch list';
  return 'Within guardrails';
}

function renderLimitRow(label, actual, limit, unit, pct) {
  const safePct = Math.max(0, Math.min(100, pct || 0));
  return `
    <div class="cds--threshold-row">
      <strong>${esc(label)}</strong>
      <div class="cds--limit-rail">
        <div class="cds--limit-fill" style="width:${safePct}%; background:${riskColor(safePct)};"></div>
        <span class="cds--limit-marker" style="left:80%;"></span>
        <span class="cds--limit-marker" style="left:95%; background:var(--cds-support-danger);"></span>
      </div>
      <span>${esc(actual.toFixed ? actual.toFixed(1) : actual)} / ${esc(limit)} ${esc(unit)}</span>
    </div>
  `;
}

function renderDonut(pct, label, color) {
  const safePct = Math.max(0, Math.min(100, pct || 0));
  const radius = 58;
  const circumference = 2 * Math.PI * radius;
  const dash = `${(safePct / 100) * circumference} ${circumference}`;
  return `
    <svg class="cds--donut" viewBox="0 0 150 150" role="img" aria-label="${esc(label)} ${safePct}%">
      <circle class="cds--donut-bg" cx="75" cy="75" r="${radius}"></circle>
      <circle class="cds--donut-fg" cx="75" cy="75" r="${radius}" style="stroke-dasharray:${dash}; stroke:${color};"></circle>
      <text x="75" y="72" text-anchor="middle">${safePct}%</text>
      <text x="75" y="92" text-anchor="middle" style="font-size:10px; fill:var(--cds-text-helper);">${esc(label)}</text>
    </svg>
  `;
}

function renderFinanceCommandCenter(data, metrics) {
  const root = document.getElementById('finance-command-center');
  const licensing = data.licensing || {};
  const products = licensing.products || [];
  const apiCoverage = licensing.apiCoverage || [];
  const worstPct = Math.max(metrics.vpcPct, metrics.ruPct, metrics.cpuPct);
  const auditReady = Boolean(licensing.auditReady);
  const status = riskLabel(worstPct, auditReady);
  const statusTag = auditReady ? riskTagClass(worstPct) : 'cds--tag--purple';
  const rawIndex = addRawInspectorItem('Finance command center evidence', {
    licensing_status: licensing.status,
    audit_ready: auditReady,
    metric_totals: licensing.metricTotals || {},
    products,
    thresholds: {
      vpc: { actual: metrics.totalVpc, entitlement: metrics.vpcEntitled, percent: metrics.vpcPct },
      ru: { actual: metrics.totalRu, entitlement: metrics.ruEntitled, percent: metrics.ruPct },
      cpu_allocation: { actual: metrics.totalCpuUsed, limit: metrics.totalCpuLimit, percent: metrics.cpuPct }
    },
    api_coverage: apiCoverage.map(ep => ({ endpoint: ep.endpoint, status: ep.status, method: ep.method || 'GET' }))
  });
  const liveProducts = products.filter(p => String(p.status || '').toLowerCase() !== 'sample').length;
  const endpointOk = apiCoverage.filter(ep => ['ok', 'available', 'sample'].includes(String(ep.status))).length;

  root.innerHTML = `
    <article class="cds--finance-card cds--finance-card--hero">
      <div>
        <div class="cds--finance-label">License control posture</div>
        <div class="cds--finance-value">${esc(status)}</div>
        <p class="cds--finance-copy">Worst utilization is ${esc(worstPct)}%. Threshold markers show 80% watch and 95% critical review. Premium remains excluded unless entitlement and License Service explicitly identify it.</p>
        <div class="cds--method-row">
          <span class="cds--tag ${statusTag}">${auditReady ? 'audit evidence live' : 'sample / evidence review'}</span>
          <button class="cds--raw-link" onclick="openRawInspector(${rawIndex})">View finance JSON</button>
        </div>
      </div>
      <div>${renderDonut(worstPct, 'risk', riskColor(worstPct))}</div>
    </article>
    <article class="cds--finance-card">
      <div class="cds--finance-label">Entitlement usage</div>
      <div class="cds--finance-value">${esc(metrics.totalVpc.toFixed(1))} VPC</div>
      <p class="cds--finance-copy">VPC and RU are shown as separate reported metrics. No conversion is applied.</p>
      ${renderLimitRow('VPC', metrics.totalVpc, metrics.vpcEntitled, 'VPC', metrics.vpcPct)}
      ${renderLimitRow('RU', metrics.totalRu, metrics.ruEntitled, 'RU', metrics.ruPct)}
      <details class="cds--finance-details"><summary>Metering rule</summary><p>IBM License Service product and bundled-product rows are the control source. Prometheus and CR sizing explain operational capacity, not contract conversion.</p></details>
    </article>
    <article class="cds--finance-card">
      <div class="cds--finance-label">Capacity exposure</div>
      <div class="cds--finance-value">${esc(metrics.totalCpuUsed.toFixed(1))} cores</div>
      <p class="cds--finance-copy">Operational CPU allocation compared with limits. Useful for t-shirt sizing and audit explanation, not a substitute for product metering.</p>
      ${renderLimitRow('CPU', metrics.totalCpuUsed, metrics.totalCpuLimit.toFixed(1), 'core', metrics.cpuPct)}
      ${renderMiniBars([metrics.cpuPct * 0.4, 32, metrics.cpuPct * 0.55, 48, metrics.cpuPct * 0.7, 54, metrics.cpuPct, 62, Math.max(18, metrics.cpuPct - 8), metrics.cpuPct, 66, metrics.cpuPct], riskColor(metrics.cpuPct))}
    </article>
    <article class="cds--finance-card">
      <div class="cds--finance-label">Evidence quality</div>
      <div class="cds--finance-value">${esc(endpointOk)} / ${esc(apiCoverage.length || 0)}</div>
      <p class="cds--finance-copy">License Service endpoints usable for product rows, bundle rows, service attribution, health/status, and audit snapshot planning.</p>
      <div class="cds--method-row">
        <span class="cds--tag ${auditReady ? 'cds--tag--green' : 'cds--tag--blue'}">${esc(licensing.mode || licensing.status || 'collector')}</span>
        <span class="cds--tag cds--tag--gray">${esc(products.length)} product rows</span>
        <span class="cds--tag cds--tag--gray">${esc(liveProducts)} live rows</span>
      </div>
      <details class="cds--finance-details"><summary>Audit posture</summary><p>Keep deployment size records, License Service evidence, entitlement application state, and product metric rows together. This dashboard surfaces the evidence streams but does not replace IBM terms.</p></details>
    </article>
  `;
}

function toggleDarkMode() {
  const current = document.documentElement.getAttribute('data-mode') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-mode', next);
  document.getElementById('mode-text').innerText = next === 'dark' ? 'Dark Mode' : 'Light Mode';
  localStorage.setItem('cds_mode', next);
  dependencyGraphSignature = '';
  if (rawData?.dependency_explorer) {
    renderDependencyExplorer(rawData.dependency_explorer);
  }
}

function initPreferences() {
  const savedMode = localStorage.getItem('cds_mode') || 'dark';
  document.documentElement.setAttribute('data-mode', savedMode);
  document.getElementById('mode-text').innerText = savedMode === 'dark' ? 'Dark Mode' : 'Light Mode';
}

function switchView(viewName) {
  document.querySelectorAll('.cds--view-panel').forEach(panel => {
    panel.hidden = panel.dataset.viewPanel !== viewName;
  });
  document.querySelectorAll('.cds--sidenav-item').forEach(item => {
    item.classList.toggle('is-active', item.dataset.view === viewName);
  });
  localStorage.setItem('cds_view', viewName);
  if (location.hash !== '#' + viewName) history.replaceState(null, '', '#' + viewName);
  // The dependency graph is built by the very first fetchData() cycle regardless of which
  // view is active on load — if "graph" wasn't the active view yet, its container was
  // display:none (0x0) when Cytoscape measured it. Re-measure now that it's actually visible.
  if (viewName === 'graph' && dependencyGraph3D) {
    requestAnimationFrame(resizeDependencyGraph);
  }
  document.querySelector('.cds--shell-main main')?.scrollTo({ top: 0, behavior: 'instant' });
}

function initView() {
  const fromHash = location.hash.replace('#', '');
  const valid = ['overview', 'services', 'graph', 'compliance', 'reference'];
  const initial = valid.includes(fromHash) ? fromHash : (localStorage.getItem('cds_view') || 'overview');
  switchView(initial);
}

function setSimScale(scale, btn) {
  currentScaleOverride = scale;
  document.querySelectorAll('.cds--content-switcher-btn').forEach(b => b.classList.remove('cds--content-switcher--selected'));
  btn.classList.add('cds--content-switcher--selected');
  if (rawData) renderDashboard(rawData);
}

function openAddModal() {
  populateSupportedServiceDropdown();
  document.getElementById('add-modal').style.display = 'flex';
}
function closeAddModal() {
  document.getElementById('add-modal').style.display = 'none';
}

function getSelectedSupportedService() {
  const select = document.getElementById('supported-svc-select');
  const value = select ? select.value : '';
  return supportedServices.find(svc => svc.local_id === value || svc.id === value) || null;
}

function populateSupportedServiceDropdown() {
  const select = document.getElementById('supported-svc-select');
  if (!select) return;
  const selected = select.value;
  const grouped = [...supportedServices].sort((a, b) => {
    const cat = String(a.category || '').localeCompare(String(b.category || ''));
    return cat || String(a.name || '').localeCompare(String(b.name || ''));
  });

  select.innerHTML = '<option value="">Choose a supported service...</option>' + grouped.map(svc => {
    const value = esc(svc.local_id || svc.id);
    const installed = svc.installed ? ' - already monitored' : '';
    return `<option value="${value}">${esc(svc.category)} / ${esc(svc.name)} (${esc(svc.id)})${installed}</option>`;
  }).join('');

  if (selected && grouped.some(svc => (svc.local_id || svc.id) === selected)) {
    select.value = selected;
  }
  applySupportedServiceSelection();
}

function applySupportedServiceSelection() {
  const svc = getSelectedSupportedService();
  const note = document.getElementById('supported-svc-notes');
  if (!svc) {
    if (note) {
      note.dataset.description = 'Dynamically added custom workload monitor.';
      note.dataset.licenseRule = 'Custom workload registered dynamically. Verify IBM terms and License Service product rows before presenting as entitled usage.';
      note.dataset.dependencies = '[]';
      note.innerHTML = '<strong>Selection guidance</strong>Choose from Software Hub / Cloud Pak for Data supported component IDs. Adding a service here creates a monitor card; it does not assert entitlement or convert RU to VPC.';
    }
    return;
  }

  document.getElementById('new-svc-id').value = svc.local_id || svc.id;
  document.getElementById('new-svc-name').value = svc.name || svc.id;
  document.getElementById('new-svc-cat').value = svc.category || 'Software Hub service';
  document.getElementById('new-svc-regex').value = svc.pod_regex || `${svc.id}.*`;
  document.getElementById('new-svc-limit').value = svc.default_limit_vpc || 16;

  const deps = Array.isArray(svc.dependencies) ? svc.dependencies : [];
  const rule = svc.license_rule || 'Supported Software Hub service. Verify entitlement and License Service product rows before presenting usage.';
  const description = svc.notes || `${svc.name || svc.id} monitor from the supported Software Hub service catalog.`;
  if (note) {
    note.dataset.description = description;
    note.dataset.licenseRule = rule;
    note.dataset.dependencies = JSON.stringify(deps);
    note.innerHTML = `
      <strong>${esc(svc.name || svc.id)} ${svc.installed ? '<span class="cds--tag cds--tag--green">already monitored</span>' : '<span class="cds--tag cds--tag--blue">supported service</span>'}</strong>
      <div>${esc(description)}</div>
      <div style="margin-top:6px;"><strong style="display:inline;">License boundary:</strong> ${esc(rule)}</div>
      ${deps.length ? `<div style="margin-top:6px;"><strong style="display:inline;">Dependencies:</strong> ${deps.map(d => `<code class="cds--snippet">${esc(d)}</code>`).join(' ')}</div>` : ''}
    `;
  }
}

async function submitCustomService() {
  const id = document.getElementById('new-svc-id').value.trim();
  const name = document.getElementById('new-svc-name').value.trim();
  const cat = document.getElementById('new-svc-cat').value.trim();
  const regex = document.getElementById('new-svc-regex').value.trim();
  const scale = document.getElementById('new-svc-scale').value;
  const limit = parseFloat(document.getElementById('new-svc-limit').value) || 16;
  const note = document.getElementById('supported-svc-notes');
  let deps = [];
  try {
    deps = JSON.parse(note?.dataset.dependencies || '[]');
  } catch (e) {
    deps = [];
  }

  if (!id || !name) {
    alert('Please provide a valid service ID and Name');
    return;
  }

  const payload = {
    id: id,
    name: name,
    category: cat || 'Custom Workload',
    tier: 'Custom Workload',
    pod_regex: regex || `${id}.*`,
    crd_sizing: scale,
    default_limit_vpc: limit,
    desc: note?.dataset.description || 'Dynamically added custom workload monitor.',
    license_rule: note?.dataset.licenseRule || 'Custom workload registered dynamically. Verify IBM terms and License Service product rows before presenting as entitled usage.',
    dependencies: deps
  };

  try {
    await fetch('/api/services/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    closeAddModal();
    fetchData();
  } catch (e) {
    alert('Failed to register service: ' + e);
  }
}

async function fetchData() {
  const fetchBar = document.getElementById('fetch-bar');
  fetchBar?.classList.add('is-active');
  setLicenseStatusIndicator('loading', 'checking…');
  try {
    const res = await fetch('/api/telemetry');
    const data = await res.json();
    rawData = data;
    renderDashboard(data);
  } catch (err) {
    console.error('Failed to fetch telemetry:', err);
    setLicenseStatusIndicator('down', 'fetch failed');
  } finally {
    fetchBar?.classList.remove('is-active');
  }
}

function setLicenseStatusIndicator(state, text) {
  const dot = document.getElementById('status-license-dot');
  const label = document.getElementById('status-license-text');
  if (dot) dot.className = 'cds--status-dot is-' + state;
  if (label) label.textContent = text;
}

function renderStatusRail(data) {
  const cluster = data.cluster_info || {};
  const lic = data.licensing || {};
  const urlEl = document.getElementById('status-cluster-url');
  if (urlEl) {
    const short = String(cluster.ocp_url || '').replace(/^https?:\\/\\//, '');
    urlEl.textContent = short || '--';
    urlEl.title = cluster.ocp_url || '';
  }
  const stateMap = {
    connected: ['live', 'connected'],
    simulated: ['simulated', 'sample data'],
    unreachable: ['down', 'unreachable'],
    error: ['down', 'error'],
  };
  const [state, label] = stateMap[lic.status] || ['simulated', lic.status || 'unknown'];
  setLicenseStatusIndicator(state, lic.host ? `${label} – ${lic.host}`.slice(0, 40) : label);
  const updatedEl = document.getElementById('status-updated');
  if (updatedEl) updatedEl.textContent = (data.timestamp || '').replace(' UTC', 'Z').split(' ')[1] || data.timestamp || '--';
  const badge = document.getElementById('nav-badge-services');
  if (badge) badge.textContent = data.services ? Object.keys(data.services).length : '';
}

// Every render* function used to fully rebuild its target's innerHTML on every 10s poll
// regardless of whether the underlying data actually changed, resetting any open <details>,
// scroll position, or keyboard focus inside that subtree. renderDependencyExplorer already
// had a one-off signature guard for this; renderIfChanged generalizes that same pattern to
// every section so a poll that returns identical data is a no-op for the DOM.
const lastRenderSignatures = {};
function renderIfChanged(key, payload, fn) {
  const signature = JSON.stringify(payload);
  if (lastRenderSignatures[key] === signature) return;
  lastRenderSignatures[key] = signature;
  fn();
}

function renderDashboard(data) {
  const cluster = data.cluster_info;
  // rawInspectorStore is intentionally NOT reset here — sections skipped by renderIfChanged()
  // keep DOM referencing ids handed out on an earlier cycle, and those must stay resolvable.
  supportedServices = data.supported_services || [];
  if (document.getElementById('add-modal')?.style.display === 'flex' && document.getElementById('supported-svc-select')?.options.length <= 1) {
    populateSupportedServiceDropdown();
  }
  document.getElementById('footer-ocp-info').innerText = cluster.ocp_url;
  renderStatusRail(data);
  renderIfChanged('releaseMatrix', data.terms_info, () => renderReleaseMatrix(data.terms_info));
  renderIfChanged('sourceMap', data.terms_info, () => renderSourceMap(data.terms_info));
  renderIfChanged('installOptions', data.install_options, () => renderInstallOptions(data.install_options || {}));
  renderIfChanged('guardrails', data.terms_info?.dependency_policy, renderGuardrailsGrid);
  renderDependencyExplorer(data.dependency_explorer || {});

  const services = data.services;
  const count = Object.keys(services).length;
  document.getElementById('svc-count-val').innerText = count;

  let totalCpuUsed = 0;
  let totalCpuLimit = 0;
  let totalMemUsed = 0;
  let totalMemLimit = 0;

  for (const [id, s] of Object.entries(services)) {
    const activeScale = currentScaleOverride === 'live' ? s.crd_sizing : currentScaleOverride;
    const mult = activeScale === 'small_mincpureq' ? 0.6 : activeScale === 'small' ? 1.0 : activeScale === 'medium' ? 2.0 : 3.5;
    totalCpuUsed += s.cpu_used_cores * mult;
    totalCpuLimit += s.cpu_limit_cores * mult;
    totalMemUsed += s.mem_used_gb * mult;
    totalMemLimit += s.mem_limit_gb * mult;
  }

  const totals = data.licensing.metricTotals || {};
  const vpcEntitled = data.licensing.clusterPeakVpc || 128;
  const ruEntitled = data.licensing.ruEntitlement || 1000;
  const totalVpc = Number(totals.VPC ?? data.licensing.totalVpc ?? 0);
  const totalRu = Number(totals.RU ?? data.licensing.totalRu ?? 0);
  const vpcPct = Math.min(100, Math.round((totalVpc / vpcEntitled) * 100));
  const ruPct = Math.min(100, Math.round((totalRu / Math.max(ruEntitled, 1)) * 100));

  document.getElementById('tot-vpc').innerText = totalVpc.toFixed(1) + ' VPC';
  document.getElementById('vpc-helper').innerText = `Entitlement Limit: ${vpcEntitled} VPCs (${vpcPct}%)`;
  document.getElementById('vpc-mini-bars').innerHTML = renderMiniBars([22, 34, 28, vpcPct * 0.55, 42, vpcPct * 0.75, 38, 48, vpcPct, 54, Math.max(18, vpcPct - 12), vpcPct], 'var(--cds-interactive-accent)');
  document.getElementById('vpc-bar').style.width = vpcPct + '%';
  document.getElementById('vpc-status-tag').innerText = vpcPct >= 90 ? 'Review' : 'Within limit';
  document.getElementById('vpc-status-tag').className = `cds--tag ${vpcPct >= 90 ? 'cds--tag--red' : 'cds--tag--green'}`;

  document.getElementById('tot-ru').innerText = totalRu.toFixed(1) + ' RU';
  document.getElementById('ru-helper').innerText = `Entitlement Limit: ${ruEntitled} RUs (${ruPct}%)`;
  document.getElementById('ru-mini-bars').innerHTML = renderMiniBars([18, 30, ruPct * 0.42, 34, ruPct * 0.64, 42, 50, ruPct * 0.82, 58, ruPct, Math.max(15, ruPct - 8), ruPct], 'var(--cds-interactive-01)');
  document.getElementById('cpu-pct-tag').innerText = ruPct + '%';
  document.getElementById('cpu-bar').style.width = ruPct + '%';
  document.getElementById('cpu-bar').style.backgroundColor = ruPct >= 90 ? 'var(--cds-support-danger)' : 'var(--cds-interactive-01)';

  const cpuPct = Math.min(100, Math.round((totalCpuUsed / Math.max(totalCpuLimit, 1)) * 100));
  document.getElementById('tot-cpu').innerText = totalCpuUsed.toFixed(1) + ' / ' + totalCpuLimit.toFixed(1);
  document.getElementById('cpu-mini-bars').innerHTML = renderMiniBars([cpuPct * 0.35, 28, cpuPct * 0.48, 44, cpuPct * 0.7, 52, cpuPct * 0.88, 46, cpuPct, Math.max(20, cpuPct - 10), 60, cpuPct], cpuPct >= 85 ? 'var(--cds-support-danger)' : 'var(--cds-support-success)');
  document.getElementById('mem-pct-tag').innerText = cpuPct + '%';
  document.getElementById('mem-bar').style.width = cpuPct + '%';
  document.getElementById('mem-bar').style.backgroundColor = cpuPct >= 85 ? 'var(--cds-support-danger)' : 'var(--cds-support-success)';

  const financeMetrics = { totalVpc, totalRu, vpcEntitled, ruEntitled, vpcPct, ruPct, totalCpuUsed, totalCpuLimit, cpuPct };
  renderIfChanged('financeCommandCenter', { data: data.licensing, financeMetrics }, () => renderFinanceCommandCenter(data, financeMetrics));
  renderIfChanged('complianceControls', data.compliance_controls, () => renderComplianceControls(data.compliance_controls));
  renderIfChanged('wkcFeatures', data.wkc_features, () => renderWkcFeatures(data.wkc_features));
  renderIfChanged('services', services, () => renderServices(services));
  renderIfChanged('licenseTable', data.licensing.products, () => renderLicenseTable(data.licensing.products));
  renderIfChanged('apiCoverage', data.licensing.apiCoverage, () => renderApiCoverage(data.licensing.apiCoverage || []));
}

function renderReleaseMatrix(terms) {
  const grid = document.getElementById('release-grid');
  const releases = terms?.release_matrix?.releases || [];
  grid.innerHTML = releases.map(r => `
    <article class="cds--release-card">
      <h3>${esc(r.software_hub)} / watsonx ${esc(r.watsonx)}</h3>
      <div class="cds--kv">
        <strong>CPD</strong><span>${esc(r.cloud_pak_for_data)}</span>
        <strong>watsonx.data</strong><span>${esc(r.watsonx_data)}</span>
        <strong>Premium</strong><span>${esc(r.watsonx_data_premium)}</span>
        <strong>WKC</strong><span>${esc(r.wkc)}</span>
        <strong>DataStage</strong><span>${esc(r.datastage)}</span>
        <strong>Lineage</strong><span>${esc(r.lineage)}</span>
      </div>
    </article>
  `).join('');
}

function renderComplianceControls(controls) {
  const grid = document.getElementById('compliance-controls-grid');
  if (!grid) return;
  if (!controls || !controls.nodes || controls.nodes.length === 0) {
    grid.innerHTML = '<p style="color:var(--cds-text-helper); font-size:13px;">Node data unavailable — connect to a live cluster to check node pinning and quota status.</p>';
    return;
  }
  const pinIndex = addRawInspectorItem('Node pinning — per-node labels', controls.nodes);
  const pinCard = `
    <article class="cds--endpoint-card">
      <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
        <strong style="font-size:13px;">Node pinning (isc-entitlement)</strong>
        <span class="cds--tag ${controls.nodePinningConfigured ? 'cds--tag--green' : 'cds--tag--gray'}">${controls.nodePinningConfigured ? 'configured' : 'not configured'}</span>
      </div>
      <p>${controls.nodePinningConfigured
        ? `${esc(controls.pinnedNodeCount)} of ${esc(controls.nodes.length)} nodes carry an isc-entitlement label.`
        : 'No node carries the isc-entitlement label. IBM: "optional but strongly recommended if you plan to install multiple solutions in a single instance" — not required for a single-solution instance.'}</p>
      <div class="cds--method-row">
        <button class="cds--method-chip" onclick="openRawInspector(${pinIndex})">View node labels</button>
      </div>
    </article>
  `;
  const quotaIndex = addRawInspectorItem('Namespace quota check', { namespaceQuotaConfigured: controls.namespaceQuotaConfigured, note: controls.namespaceQuotaNote });
  const quotaCard = `
    <article class="cds--endpoint-card">
      <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
        <strong style="font-size:13px;">Namespace resource quota</strong>
        <span class="cds--tag ${controls.namespaceQuotaConfigured ? 'cds--tag--green' : 'cds--tag--blue'}">${controls.namespaceQuotaConfigured ? 'configured' : 'none found'}</span>
      </div>
      <p>${esc(controls.namespaceQuotaNote)}</p>
      <div class="cds--method-row">
        <button class="cds--method-chip" onclick="openRawInspector(${quotaIndex})">View detail</button>
      </div>
    </article>
  `;
  const capacityIndex = addRawInspectorItem('Worker node capacity', controls.nodes.filter(n => n.isWorker));
  const capacityCard = `
    <article class="cds--endpoint-card">
      <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
        <strong style="font-size:13px;">Worker node capacity</strong>
        <span class="cds--tag cds--tag--gray">${esc(controls.workerNodeCount)} workers</span>
      </div>
      <p>${esc(controls.totalWorkerCpuCores)} vCPU / ${esc(controls.totalWorkerMemoryGiB)} GiB total across worker nodes.${controls.gpuCapableNodeCount ? ` ${esc(controls.gpuCapableNodeCount)} node(s) advertise GPU capacity.` : ' No node advertises GPU capacity — any GPU-denominated ratio (e.g. Milvus GPGPU) has no real data to validate against here.'}</p>
      <div class="cds--method-row">
        <button class="cds--method-chip" onclick="openRawInspector(${capacityIndex})">View nodes</button>
      </div>
    </article>
  `;
  grid.innerHTML = pinCard + quotaCard + capacityCard;
}

function renderWkcFeatures(wkcFeatures) {
  const grid = document.getElementById('wkc-features-grid');
  if (!grid) return;
  if (!wkcFeatures || !wkcFeatures.features) {
    grid.innerHTML = '<p style="color:var(--cds-text-helper); font-size:13px;">Feature status unavailable — connect to a live cluster.</p>';
    return;
  }
  grid.innerHTML = wkcFeatures.features.map(f => {
    const detailIndex = addDetailItem(f.name, {
      tags: [{ label: f.enabled ? 'enabled' : 'not enabled', cls: f.enabled ? 'cds--tag--green' : 'cds--tag--gray' }],
      sectionsHtml:
        detailSection('Verdict', `<p>${esc(f.evidence)}</p>`) +
        detailSection('Evidence checked', `
          <div class="cds--detail-kv">
            <dt>CRD</dt><dd>${esc(f.cr)}</dd>
            <dt>Instances found</dt><dd>${esc(f.instances)}</dd>
            ${f.toggle_field ? `<dt>${esc(f.toggle_field)}</dt><dd>${esc(String(f.toggle_value))}</dd>` : ''}
            ${f.workload_found !== null ? `<dt>Dedicated workload running</dt><dd>${esc(String(f.workload_found))}</dd>` : ''}
          </div>
        `),
      payload: f,
    });
    return `
      <article class="cds--endpoint-card" onclick="openRawInspector(${detailIndex})" style="cursor:pointer;">
        <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
          <strong style="font-size:13px;">${esc(f.name)}</strong>
          <span class="cds--tag ${f.enabled ? 'cds--tag--green' : 'cds--tag--gray'}">${f.enabled ? 'enabled' : 'not enabled'}</span>
        </div>
        <p style="font-family:var(--cds-font-mono); font-size:11px; margin-top:6px; color:var(--cds-text-helper);">${esc(f.cr)}</p>
        <p style="margin-top:8px; font-size:12px;">${esc(f.instances)} CR instance(s)${f.workload_found !== null ? `, workload ${f.workload_found ? 'found' : 'not found'}` : ''}</p>
        <div class="cds--method-row" style="margin-top:8px;">
          <span class="cds--tag cds--tag--gray">Click for evidence</span>
        </div>
      </article>
    `;
  }).join('');
}

function renderApiCoverage(endpoints) {
  const grid = document.getElementById('endpoint-grid');
  grid.innerHTML = endpoints.map(ep => {
    const status = String(ep.status || 'unknown');
    const tag = status === 'ok' || status === 'available' ? 'cds--tag--green' : status === 'sample' ? 'cds--tag--blue' : 'cds--tag--red';
    const method = ep.method || 'GET';
    const rawPayload = ep.raw || ep;
    const rawIndex = addRawInspectorItem(`License Service ${method} ${ep.endpoint}`, rawPayload);
    const nonReadIndex = addRawInspectorItem(`Write methods for ${ep.endpoint}`, {
      endpoint: ep.endpoint,
      dashboard_behavior: 'This standalone dashboard uses read-only License Service calls for evidence and metering review.',
      supported_here: ['GET'],
      not_used_here: ['POST', 'PUT', 'PUSH'],
      reason: 'License usage quantities come from IBM License Service reported product/bundled-product rows. Entitlement changes are handled by cpd-cli workflows, not by this dashboard.'
    });
    return `
      <article class="cds--endpoint-card">
        <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
          <button class="cds--raw-link" onclick="openRawInspector(${rawIndex})">${esc(method)} ${esc(ep.endpoint)}</button>
          <span class="cds--tag ${tag}">${esc(status)}</span>
        </div>
        <p>${esc(ep.meaning || ep.error || 'IBM License Service endpoint')}</p>
        ${ep.latencyMs !== undefined ? `<p style="margin-top:6px; color:var(--cds-text-helper);">${esc(ep.latencyMs)} ms</p>` : ''}
        <div class="cds--method-row" aria-label="Endpoint methods">
          <button class="cds--method-chip" onclick="openRawInspector(${rawIndex})">GET raw</button>
          <button class="cds--method-chip" onclick="openRawInspector(${nonReadIndex})">PUT</button>
          <button class="cds--method-chip" onclick="openRawInspector(${nonReadIndex})">POST</button>
          <button class="cds--method-chip" onclick="openRawInspector(${nonReadIndex})">PUSH</button>
        </div>
      </article>
    `;
  }).join('');
}

function renderSourceMap(terms) {
  const grid = document.getElementById('source-grid');
  const entries = [
    terms?.software_hub_positioning,
    terms?.dependency_policy,
    terms?.knowledge_catalog_positioning,
    terms?.install_options_policy,
    terms?.watsonx_data_standard,
    terms?.watsonx_data_premium_reference,
    terms?.license_service_api,
    terms?.license_document_cli,
    terms?.tracking_reporting_policy
  ].filter(Boolean);

  grid.innerHTML = entries.map(entry => {
    const highlights = (entry.terms_highlights || []).slice(0, 4).map(item => `<li>${esc(item)}</li>`).join('');
    const sources = (entry.sources || []).map(src => `<a href="${esc(src)}" target="_blank" rel="noopener">${esc(src)}</a>`).join('<br>');
    const rawIndex = addRawInspectorItem(`IBM source: ${entry.name}`, entry);
    return `
      <article class="cds--source-card">
        <h3>${esc(entry.name)}</h3>
        <p><strong>Metric stance:</strong> ${esc(entry.metric || 'Use IBM License Service output')}</p>
        <details class="cds--finance-details">
          <summary>Show source interpretation</summary>
          <ul>${highlights}</ul>
          ${sources ? `<p>${sources}</p>` : ''}
        </details>
        <div class="cds--method-row">
          <button class="cds--raw-link" onclick="openRawInspector(${rawIndex})">View source JSON</button>
        </div>
      </article>
    `;
  }).join('');
}

// One rule per card instead of one seven-item bullet list — the Overview page only needs to
// know a posture badge exists; anyone reviewing the actual guardrails wants to scan them,
// not read a paragraph-length <ul> top to bottom.
const COMPLIANCE_GUARDRAILS = [
  { title: 'Metric truth', tag: 'cds--tag--blue', text: 'Read IBM License Service metricName and metricQuantity as reported; keep RU and VPC totals separate, never summed together.' },
  { title: 'Release scope', tag: 'cds--tag--gray', text: 'Covers Software Hub / CPD 5.3 with watsonx 2.3, and Software Hub / CPD 5.4 with watsonx 2.4.' },
  { title: 'Premium guardrail', tag: 'cds--tag--purple', text: 'Never label watsonx.data as Premium unless License Service and entitlement records explicitly say Premium.' },
  { title: 'Knowledge Catalog boundary', tag: 'cds--tag--blue', text: 'Base WKC is IBM Knowledge Catalog. Premium / model features are a separate entitlement and can involve GPU or remote model placement.' },
  { title: 'Data quality boundary', tag: 'cds--tag--gray', text: 'Knowledge Catalog data quality can pull in DataStage components — show that as a restricted DQ dependency, separate from standalone DataStage.' },
  { title: 'Lineage boundary', tag: 'cds--tag--gray', text: 'MANTA / data lineage is its own lineage capability, not a generic WKC data-quality toggle.' },
  { title: 'Audit trail', tag: 'cds--tag--green', text: 'Use IBM License Service snapshot evidence for audits. Prometheus/CPU limits are sizing context, not audit evidence.' },
];

function renderGuardrailsGrid() {
  const grid = document.getElementById('guardrails-grid');
  if (!grid) return;
  grid.innerHTML = COMPLIANCE_GUARDRAILS.map(g => `
    <article class="cds--option-card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:8px;">
        <h3>${esc(g.title)}</h3>
        <span class="cds--tag ${g.tag}">rule</span>
      </div>
      <p>${esc(g.text)}</p>
    </article>
  `).join('');
}

function renderInstallOptions(options) {
  const grid = document.getElementById('install-options-grid');
  const source = document.getElementById('install-options-source');
  const entries = options.entries || [];
  source.innerText = `${options.sourceFilePresent ? 'Loaded' : 'Not found'}: ${options.sourceFile || 'install-options.yml'} | COMPONENTS: ${options.components || 'not set'} | IKC_TYPE: ${options.ikcType || 'not set'}`;

  grid.innerHTML = entries.map(item => {
    const status = String(item.status || 'TBD');
    const tag = status === 'configured' ? 'cds--tag--green'
      : status === 'not enabled' ? 'cds--tag--gray'
      : status === 'dependency' ? 'cds--tag--blue'
      : 'cds--tag--purple';
    const rawIndex = addRawInspectorItem(`Install option: ${item.label}`, item);
    return `
      <article class="cds--option-card">
        <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
          <h3>${esc(item.label)}</h3>
          <span class="cds--tag ${tag}">${esc(status)}</span>
        </div>
        <div class="cds--kv">
          <strong>Component</strong><span>${esc(item.component)}</span>
          <strong>Option</strong><span><code class="cds--snippet">${esc(item.option)}</code></span>
        </div>
        <details class="cds--finance-details">
          <summary>Show dependency and license interpretation</summary>
          <p>${esc(item.dependency_impact)}</p>
          <p><strong>License boundary:</strong> ${esc(item.license_boundary)}</p>
          <p style="color:var(--cds-text-helper);">${esc(item.evidence)}</p>
        </details>
        <div class="cds--method-row">
          <button class="cds--raw-link" onclick="openRawInspector(${rawIndex})">View option JSON</button>
        </div>
      </article>
    `;
  }).join('');
}

function renderDependencyExplorer(graph) {
  const root = document.getElementById('dependency-explorer');
  const nodes = graph.nodes || [];
  const edges = graph.edges || [];
  const graphSignature = JSON.stringify({
    nodes: nodes.map(node => [node.id, node.label, node.type, node.installed, node.option_status]),
    edges: edges.map(edge => [edge.from, edge.to, edge.relationship, edge.status])
  });
  const previousSelectedId = dependencyExplorerState.selectedId;
  const sourcesIndex = addRawInspectorItem('Dependency explorer sources', {
    positioning: graph.positioning,
    sources: graph.sources || [],
    nodes,
    edges
  });
  const nodeMap = {};
  nodes.forEach(node => {
    nodeMap[node.id] = {
      ...node,
      color: dependencyNodeStyle(node).color(),
      rawIndex: addRawInspectorItem(`Dependency node: ${node.label}`, node),
    };
  });
  const graphEdges = edges.map((edge, edgeIndex) => ({
    ...edge,
    edgeIndex,
    fromNode: nodeMap[edge.from],
    toNode: nodeMap[edge.to],
    rawIndex: addRawInspectorItem(`Relationship: ${edge.from} -> ${edge.to}`, edge),
  })).filter(edge => edge.fromNode && edge.toNode);
  const selectedId = nodeMap[previousSelectedId] ? previousSelectedId : nodes.find(node => node.id === 'watsonx_data') ? 'watsonx_data' : (nodes[0]?.id || '');
  dependencyExplorerState = { nodesById: nodeMap, edges: graphEdges, selectedId };
  if (dependencyGraph3D && dependencyGraphSignature === graphSignature && root.querySelector('#dependency-graph-cy')) {
    if (dependencyGraphHasBeenFocused) showDependencyNode(selectedId);
    return;
  }
  dependencyGraphSignature = graphSignature;

  const relationshipCards = graphEdges.map((edge, edgeIndex) => {
    const from = nodes.find(n => n.id === edge.from)?.label || edge.from;
    const to = nodes.find(n => n.id === edge.to)?.label || edge.to;
    const tag = edge.relationship.includes('option') ? 'cds--tag--blue'
      : edge.relationship.includes('premium') ? 'cds--tag--purple'
      : edge.relationship.includes('restricted') ? 'cds--tag--red'
      : 'cds--tag--green';
    return `
      <article class="cds--relationship-card" onclick="showDependencyEdge(${edgeIndex})">
        <h3>${esc(from)} -> ${esc(to)}</h3>
        <div class="cds--graph-node-meta">
          <span class="cds--tag ${tag}">${esc(edge.relationship)}</span>
          <span class="cds--tag cds--tag--gray">${esc(edge.status || 'relationship')}</span>
        </div>
        <p>${esc(edge.license_boundary || edge.evidence)}</p>
      </article>
    `;
  }).join('');

  const premiumEdges = edges.filter(edge => edge.relationship.includes('premium')).length;

  const editionLabel = graph.edition_label || '';
  root.innerHTML = `
    <div class="cds--dependency-toolbar">
      <div style="font-size:12px; color:var(--cds-text-secondary); max-width:760px;">${esc(graph.positioning || 'Dependency explorer')}</div>
      <div class="cds--method-row" style="margin-top:0; gap:6px; flex-wrap:wrap;">
        ${editionLabel ? `<span class="cds--tag cds--tag--blue" title="Active WXD_EDITION">${esc(editionLabel)}</span>` : ''}
        <span class="cds--tag cds--tag--gray">${esc(nodes.length)} nodes</span>
        <span class="cds--tag cds--tag--gray">${esc(edges.length)} edges</span>
        ${premiumEdges ? `<span class="cds--tag cds--tag--purple">${esc(premiumEdges)} premium paths</span>` : ''}
        <button class="cds--raw-link" onclick="openRawInspector(${sourcesIndex})">View graph JSON</button>
      </div>
    </div>
    <div class="cds--neo-layout">
      <div class="cds--neo-canvas">
        <div class="cds--graph-controls">
          <button class="cds--raw-link" onclick="collapseDependencyBundles()" title="Collapse each bundled-as-RU group into one node">Collapse bundles</button>
          <button class="cds--raw-link" onclick="expandDependencyGraph()" title="Expand all collapsed bundle groups">Expand all</button>
          <button class="cds--raw-link" onclick="reheatDependencyGraph()" title="Re-run the 3D physics simulation">Re-layout</button>
          <button class="cds--raw-link" onclick="fitDependencyGraph()">Fit</button>
          <button class="cds--raw-link" id="dependency-rotate-btn" onclick="toggleDependencyRotate()" title="Auto-rotate the camera">&#8635; Rotate</button>
          <button class="cds--raw-link" onclick="zoomDependencyGraph(0.8)" title="Zoom out" aria-label="Zoom out">&minus;</button>
          <button class="cds--raw-link" onclick="zoomDependencyGraph(1.25)" title="Zoom in" aria-label="Zoom in">&plus;</button>
        </div>
        <div id="dependency-graph-cy" role="img" aria-label="Interactive 3D product dependency graph. Drag to orbit the camera, scroll to zoom, drag a node to reposition it, click a node or edge for detail. A full text list of every relationship follows below the graph for screen-reader and keyboard use."></div>
      </div>
      <aside class="cds--neo-side">
        <h3>How to read the graph</h3>
        <p>Only active components shown, filtered by live CRs and <code>WXD_EDITION</code>. Drag to orbit, scroll to zoom, drag any sphere to reposition it — it springs back into the simulation on release. Click any node or animated link for detail, or use the text list below the graph.</p>
        <div class="cds--neo-legend" style="flex-direction:column; gap:6px; margin-top:10px;">
          ${renderDependencyLegend()}
        </div>
        <div id="dependency-detail-panel"></div>
      </aside>
    </div>
    <h3 style="margin:var(--cds-spacing-06) 0 var(--cds-spacing-03);">Full relationship list (text / accessible view)</h3>
    <p style="font-size:12px; color:var(--cds-text-secondary); margin-bottom:var(--cds-spacing-04);">Every edge in the graph above, as plain text — the source of truth for screen readers, keyboard navigation, and anyone who prefers a list to a canvas.</p>
    <div class="cds--relationship-list">${relationshipCards}</div>
  `;
  initDependencyGraph3D(nodeMap, graphEdges);
  if (dependencyGraphHasBeenFocused) showDependencyNode(dependencyExplorerState.selectedId);
}

function renderServices(services) {
  const grid = document.getElementById('services-grid');
  grid.innerHTML = '';
  const severity = { CRITICAL: 3, WARNING: 2, HEALTHY: 1 };
  const serviceEntries = Object.entries(services).sort(([, a], [, b]) => {
    const scoreA = (severity[a.status] || 0) * 1000 + Math.max(a.cpu_utilization_pct || 0, a.mem_utilization_pct || 0);
    const scoreB = (severity[b.status] || 0) * 1000 + Math.max(b.cpu_utilization_pct || 0, b.mem_utilization_pct || 0);
    return scoreB - scoreA;
  });

  for (const [id, s] of serviceEntries) {
    const activeScale = currentScaleOverride === 'live' ? s.crd_sizing : currentScaleOverride;
    const mult = activeScale === 'small_mincpureq' ? 0.6 : activeScale === 'small' ? 1.0 : activeScale === 'medium' ? 2.0 : 3.5;

    const cpuUsed = (s.cpu_used_cores * mult).toFixed(1);
    const cpuLimit = (s.cpu_limit_cores * mult).toFixed(1);
    const cpuPct = Math.min(100, Math.round((cpuUsed / Math.max(cpuLimit, 1)) * 100));

    const memUsed = (s.mem_used_gb * mult).toFixed(1);
    const memLimit = (s.mem_limit_gb * mult).toFixed(1);
    const memPct = Math.min(100, Math.round((memUsed / Math.max(memLimit, 1)) * 100));

    const depsHtml = s.dependencies && s.dependencies.length > 0
      ? `<div class="cds--detail-kv" style="margin-top:8px;"><dt>Dependencies</dt><dd>${s.dependencies.map(d => `<code class="cds--snippet">${esc(d)}</code>`).join(' ')}</dd></div>`
      : '';
    const liveTag = s.is_live_cr
      ? `<span class="cds--tag cds--tag--green">Live — confirmed via oc get</span>`
      : `<span class="cds--tag cds--tag--gray">Estimated — cluster unreachable, catalog default</span>`;
    const serviceBars = renderMiniBars(
      [cpuPct * 0.45, memPct * 0.45, cpuPct * 0.62, memPct * 0.62, cpuPct * 0.78, memPct * 0.78, cpuPct, memPct, Math.max(cpuPct, memPct) * 0.9, Math.min(100, (cpuPct + memPct) / 2), cpuPct, memPct],
      cpuPct > 85 || memPct > 85 ? 'var(--cds-support-danger)' : 'var(--cds-interactive-01)'
    );

    const detailIndex = addDetailItem(s.name, {
      tags: [
        { label: s.status, cls: s.status === 'CRITICAL' ? 'cds--tag--red' : s.status === 'WARNING' ? 'cds--tag--purple' : 'cds--tag--green' },
        { label: `${s.category} • ${s.tier}`, cls: 'cds--tag--gray' },
      ],
      sectionsHtml:
        detailSection('Description', `<p>${esc(s.description)}</p>${liveTag}`) +
        detailSection('Metering interpretation', `<p>${esc(s.license_rule)}</p>${depsHtml}`) +
        detailSection('Custom resource', `
          <div class="cds--detail-kv">
            <dt>Kind</dt><dd>${esc(s.cr_kind)}</dd>
            <dt>Name</dt><dd>${esc(s.cr_name)}</dd>
            <dt>CR sizing</dt><dd>${esc(s.crd_sizing)}</dd>
            <dt>Status</dt><dd>${esc(s.cr_status || 'Unknown')}</dd>
          </div>
          <button class="cds--btn cds--btn--secondary" style="margin-top:10px;" onclick="event.stopPropagation(); viewCrYaml('${esc(id)}', ${s.cr_name ? `'${esc(s.cr_name)}'` : 'null'}, '${esc(s.name)}')">View live CR as YAML</button>
        `) +
        detailSection('Resource usage (active sizing: ' + esc(activeScale) + ')', `
          <div class="cds--metric-item">
            <div class="cds--metric-label"><span>CPU Allocation</span><span class="cds--metric-value">${cpuUsed} / ${cpuLimit} Cores (${cpuPct}%)</span></div>
            <div class="cds--progress-bar"><div class="cds--progress-bar__fill" style="background-color:${cpuPct > 85 ? 'var(--cds-support-danger)' : 'var(--cds-interactive-01)'}; width:${cpuPct}%;"></div></div>
          </div>
          <div class="cds--metric-item" style="margin-top:10px;">
            <div class="cds--metric-label"><span>Memory Working Set</span><span class="cds--metric-value">${memUsed} / ${memLimit} GB (${memPct}%)</span></div>
            <div class="cds--progress-bar"><div class="cds--progress-bar__fill" style="background-color:${memPct > 85 ? 'var(--cds-support-danger)' : 'var(--cds-support-success)'}; width:${memPct}%;"></div></div>
          </div>
        `),
      payload: {
        service_id: id,
        active_scale: activeScale,
        computed: { cpu_used_cores: Number(cpuUsed), cpu_limit_cores: Number(cpuLimit), cpu_percent: cpuPct, memory_used_gb: Number(memUsed), memory_limit_gb: Number(memLimit), memory_percent: memPct },
        source: s,
      },
    });

    const card = document.createElement('div');
    card.className = 'cds--service-card';
    card.onclick = () => openRawInspector(detailIndex);
    card.innerHTML = `
      <div>
        <div class="cds--svc-top">
          <div>
            <div class="cds--svc-cat">${esc(s.category)} &bull; ${esc(s.tier)}</div>
            <div class="cds--svc-name">${esc(s.name)}</div>
          </div>
          <span class="cds--tag ${s.status === 'CRITICAL' ? 'cds--tag--red' : s.status === 'WARNING' ? 'cds--tag--purple' : 'cds--tag--green'}">${esc(s.status)}</span>
        </div>
        <div class="cds--svc-desc">${esc(s.description)}</div>
        <div class="cds--license-terms-box">
          <strong>Metering interpretation:</strong>
          <span class="cds--clamp-text">${esc(s.license_rule)}</span>
        </div>
        <div class="cds--meta-row">
          <span class="cds--tag cds--tag--purple">CR sizing: ${esc(activeScale)}</span>
          <span class="cds--tag cds--tag--gray" style="cursor:pointer;" title="Click to view live YAML" onclick="event.stopPropagation(); viewCrYaml('${esc(id)}', ${s.cr_name ? `'${esc(s.cr_name)}'` : 'null'}, '${esc(s.name)}')">CR: ${esc(s.cr_name)}</span>
          <span class="cds--tag cds--tag--blue" style="cursor:pointer;" title="Click to view live YAML" onclick="event.stopPropagation(); viewCrYaml('${esc(id)}', ${s.cr_name ? `'${esc(s.cr_name)}'` : 'null'}, '${esc(s.name)}')">Kind: ${esc(s.cr_kind)}</span>
          ${s.is_live_cr ? '' : '<span class="cds--tag cds--tag--gray" title="Cluster unreachable — this card is showing a catalog default, not a confirmed live reading">estimated</span>'}
        </div>
        ${serviceBars}
        <div class="cds--metric-item">
          <div class="cds--metric-label">
            <span>CPU Allocation</span>
            <span class="cds--metric-value">${cpuUsed} / ${cpuLimit} Cores (${cpuPct}%)</span>
          </div>
          <div class="cds--progress-bar">
            <div class="cds--progress-bar__fill" style="background-color:${cpuPct > 85 ? 'var(--cds-support-danger)' : 'var(--cds-interactive-01)'}; width:${cpuPct}%;"></div>
          </div>
        </div>
        <div class="cds--metric-item">
          <div class="cds--metric-label">
            <span>Memory Working Set</span>
            <span class="cds--metric-value">${memUsed} / ${memLimit} GB (${memPct}%)</span>
          </div>
          <div class="cds--progress-bar">
            <div class="cds--progress-bar__fill" style="background-color:${memPct > 85 ? 'var(--cds-support-danger)' : 'var(--cds-support-success)'}; width:${memPct}%;"></div>
          </div>
        </div>
        <div class="cds--method-row">
          <span class="cds--tag cds--tag--gray">Click card for detail &middot; click CR/Kind for live YAML</span>
        </div>
      </div>
    `;
    grid.appendChild(card);
  }
}

function licenseMetricMeaning(rawMetricName) {
  const m = String(rawMetricName || '').toUpperCase();
  if (m === 'VIRTUAL_PROCESSOR_CORE') {
    return 'Virtual Processor Core (VPC). IBM: "Generally, 1 VPC = 1 physical core or 1 virtual core." Counted from the pod vCPU LIMIT (potential capacity), not live/instantaneous usage.';
  }
  if (m === 'RESOURCE_UNIT') {
    return 'Resource Unit (RU) — the metric watsonx.data editions are licensed under. RU and VPC are separate pools; IBM does not define a universal RU→VPC conversion (only specific documented engine ratios, e.g. Milvus, exist).';
  }
  return `Reported as-is by IBM License Service; this dashboard has no documented interpretation for "${esc(rawMetricName || 'this metric')}" yet.`;
}

function renderLicenseTable(products) {
  const grid = document.getElementById('license-products-grid');
  if (!products || products.length === 0) {
    grid.innerHTML = '<p style="color:var(--cds-text-helper); font-size:13px;">No products registered yet — connect to a live IBM License Service instance, or check the API Coverage section above for why.</p>';
    return;
  }
  grid.innerHTML = products.map(p => {
    const metricLabel = p.metricNameNormalized || p.metricName || 'Reported';
    const detailIndex = addDetailItem(p.name, {
      tags: [
        { label: p.status || 'Reported', cls: 'cds--tag--green' },
        { label: p.edition || p.release || 'Reported', cls: 'cds--tag--purple' },
      ],
      sectionsHtml:
        detailSection('Reported value', `<p style="font-weight:600; color:var(--cds-interactive-01); font-family:var(--cds-font-mono); font-size:20px;">${esc(p.metricQuantity ?? 0)} ${esc(metricLabel)}</p>`) +
        detailSection('What this metric means', `<p>${licenseMetricMeaning(p.metricName)}</p>`) +
        detailSection('Where this came from', `
          <div class="cds--detail-kv">
            <dt>IBM product ID</dt><dd>${esc(p.id || p.productId || 'n/a')}</dd>
            <dt>Raw metricName</dt><dd>${esc(p.metricName || 'n/a')}</dd>
            <dt>Peak reported</dt><dd>${esc(p.metricPeakDate || 'n/a')}</dd>
            <dt>Endpoint</dt><dd>IBM License Service <code>/products</code></dd>
          </div>
        `),
      payload: p,
    });
    return `
      <article class="cds--endpoint-card" onclick="openRawInspector(${detailIndex})" style="cursor:pointer;">
        <div style="display:flex; justify-content:space-between; gap:8px; align-items:flex-start;">
          <strong style="font-size:13px;">${esc(p.name)}</strong>
          <span class="cds--tag cds--tag--green">${esc(p.status || 'Reported')}</span>
        </div>
        <p style="font-family:var(--cds-font-mono); font-size:11px; margin-top:4px;">${esc(p.id || p.productId || 'n/a')}</p>
        <div class="cds--method-row" style="margin-top:8px;">
          <span class="cds--tag cds--tag--purple">${esc(p.edition || p.release || 'Reported')}</span>
          <span class="cds--tag cds--tag--blue">${esc(p.metricName || 'Reported')}</span>
        </div>
        <p style="margin-top:10px; font-weight:600; color:var(--cds-interactive-01); font-family:var(--cds-font-mono); font-size:16px;">${esc(p.metricQuantity ?? 0)} ${esc(metricLabel)}</p>
        ${p.metricPeakDate ? `<p style="margin-top:4px; color:var(--cds-text-helper); font-size:11px;">Peak reported: ${esc(p.metricPeakDate)}</p>` : ''}
        <div class="cds--method-row" style="margin-top:8px;">
          <span class="cds--tag cds--tag--gray">Click for meaning</span>
        </div>
      </article>
    `;
  }).join('');
}

async function exportSnapshot() {
  // Prefer the real IBM License Service /snapshot package (a signed ZIP — see
  // fetch_license_snapshot in server.py) when reachable; it's IBM's own audit evidence, not a
  // reconstruction. Fall back to a clearly-labeled local JSON reconstruction otherwise.
  try {
    const resp = await fetch('/api/audit/export');
    if (resp.ok) {
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `ibm-licensing-service-snapshot-${new Date().toISOString().slice(0, 10)}.zip`;
      a.click();
      URL.revokeObjectURL(url);
      return;
    }
  } catch (e) {
    console.warn('IBM License Service /snapshot unreachable, falling back to local reconstruction', e);
  }
  if (!rawData) return;
  const snapshot = {
    export_timestamp: new Date().toISOString(),
    warning: "IBM License Service /snapshot was unreachable — this is a LOCALLY ASSEMBLED reconstruction from this dashboard's own telemetry, NOT an IBM-signed audit package. Connect to a live License Service instance for the authoritative signed ZIP.",
    audit_reporting_window: 'calendar quarter (per IBM container-licensing policy)',
    audit_retention_years: 2,
    cluster_metadata: rawData.cluster_info,
    terms_reference: rawData.terms_info,
    services_crd_sizing: rawData.services,
    licensing_entitlements: rawData.licensing
  };
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `ibm-software-hub-audit-snapshot-LOCAL-RECONSTRUCTION-${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

initPreferences();
initView();
fetchData();
setInterval(fetchData, 10000);

// Nothing tells the WebGL canvas its container changed size on its own — it would render at
// a stale width after a viewport resize or the 900px layout breakpoint collapsing
// .cds--neo-layout to one column. Debounced so a drag-resize doesn't thrash the renderer.
let dependencyResizeTimer = null;
window.addEventListener('resize', () => {
  clearTimeout(dependencyResizeTimer);
  dependencyResizeTimer = setTimeout(resizeDependencyGraph, 200);
});
</script>
</body>
</html>
"""


class DashboardHTTPHandler(BaseHTTPRequestHandler):
    collector = ClusterTelemetryCollector()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
        elif parsed.path == "/api/telemetry":
            data = self.collector.get_cluster_overview()
            resp_bytes = json.dumps(data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_bytes)
        elif parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "healthy"}')
        elif parsed.path == "/api/audit/export":
            result = self.collector.fetch_license_snapshot()
            if result.get("status") == "ok":
                self.send_response(200)
                self.send_header("Content-Type", result.get("contentType", "application/zip"))
                self.send_header("Content-Disposition", 'attachment; filename="ibm-license-service-snapshot.zip"')
                self.end_headers()
                self.wfile.write(result["bytes"])
            else:
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"status": result.get("status", "error"), "message": result.get("reason", "IBM License Service /snapshot unavailable.")}).encode("utf-8"))
        elif parsed.path.startswith("/api/cr/"):
            service_id = urllib.parse.unquote(parsed.path[len("/api/cr/"):])
            query = urllib.parse.parse_qs(parsed.query)
            cr_name = (query.get("name") or [None])[0]
            result = self.collector.get_cr_yaml(service_id, cr_name)
            self.send_response(200 if result.get("status") == "ok" else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
        elif parsed.path.startswith("/vendor/"):
            rel_path = urllib.parse.unquote(parsed.path.removeprefix("/vendor/"))
            asset_path = os.path.abspath(os.path.join(VENDOR_DIR, rel_path))
            if not asset_path.startswith(os.path.abspath(VENDOR_DIR) + os.sep) or not os.path.isfile(asset_path):
                self.send_response(404)
                self.end_headers()
                return
            content_type = "text/javascript; charset=utf-8" if asset_path.endswith(".js") else "application/octet-stream"
            with open(asset_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/services/add":
            length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(length).decode('utf-8')
            try:
                new_svc = json.loads(body)
                svc_id = new_svc.get("id")
                if svc_id:
                    self.collector.custom_services[svc_id] = {
                        "name": new_svc.get("name", svc_id),
                        "category": new_svc.get("category", "Custom Workload"),
                        "tier": new_svc.get("tier", "Custom Service"),
                        "pod_regex": new_svc.get("pod_regex", f"{svc_id}.*"),
                        "crd_sizing": new_svc.get("crd_sizing", "small"),
                        "cr_name": f"cr-{svc_id}",
                        "cr_kind": "CustomResource",
                        "default_limit_vpc": float(new_svc.get("default_limit_vpc", 16)),
                        "desc": new_svc.get("desc", "Dynamically added service monitor."),
                        "license_rule": new_svc.get("license_rule", "Custom registered workload."),
                        "dependencies": new_svc.get("dependencies", []),
                        "is_live_cr": False
                    }
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"status": "success"}')
                    return
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(str(e).encode('utf-8'))
                return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


def run_dashboard(port=DEFAULT_PORT, host=DEFAULT_HOST):
    # ThreadingHTTPServer, not HTTPServer: the plain single-threaded server serializes every
    # request behind whichever one is currently blocked on an `oc`/urllib call (up to the 8s
    # timeouts used throughout ClusterTelemetryCollector) — that included /vendor/* asset
    # requests stalling behind an in-flight /api/telemetry poll on a slow/unreachable cluster.
    server = ThreadingHTTPServer((host, port), DashboardHTTPHandler)
    print("=" * 64)
    print(" IBM Software Hub & watsonx.data Telemetry Dashboard")
    print(f" • Local UI:         http://localhost:{port}")
    print(f" • REST API:        http://localhost:{port}/api/telemetry")
    print(f" • Design System:   IBM Carbon Design System (carbondesignsystem.com)")
    print(" • License Posture: watsonx.data standard/non-premium by default; Premium only if entitled")
    print(f" • Dependencies:    Zero external dependencies (pure Python standard library)")
    print("=" * 64)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server...")
        server.server_close()


if __name__ == "__main__":
    port = DEFAULT_PORT
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        port = int(sys.argv[1])
    run_dashboard(port=port)
