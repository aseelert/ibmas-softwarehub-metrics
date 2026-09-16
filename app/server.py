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
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any, Optional, List

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

DEFAULT_PORT = 8088
DEFAULT_HOST = "0.0.0.0"
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
        "doc_id": "IBM watsonx.data 2.3 / 2.4",
        "name": "IBM watsonx.data standard / non-premium",
        "program_name": "IBM watsonx.data",
        "metric": "Use IBM License Service metricName as reported: RU, VPC, or contract-specific metric",
        "terms_highlights": [
            "Default posture for this dashboard: standard/non-premium watsonx.data.",
            "Do not infer Premium features or Premium license terms from a standard watsonx.data CR.",
            "Meter by the product metric reported by IBM License Service; do not convert RU to VPC unless the customer's IBM agreement explicitly defines that conversion.",
            "Track CR sizing, pod limits, and live CPU/memory as operational evidence, not as a replacement for IBM License Service audit data."
        ],
        "sources": [
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-53-and-related-services",
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-54-and-related-services",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=new-watsonxdata"
        ]
    },
    "watsonx_data_premium_reference": {
        "doc_id": "L-PCPF-BJV4WW",
        "name": "IBM watsonx.data Premium Edition 2.4.x reference only",
        "program_name": "IBM watsonx.data Premium",
        "metric": "Use the License Information document and License Service metricName",
        "terms_highlights": [
            "Premium is a separate IBM offering and must not be assumed for a non-premium installation.",
            "The example document supplied by the user is for watsonx.data Premium 2.4 update 1.",
            "Use Premium references only when the customer entitlement and License Service product row identify Premium."
        ],
        "sources": [
            "https://www.ibm.com/support/customer/csol/terms/?id=L-PCPF-BJV4WW&lc=de",
            "https://www.ibm.com/support/pages/license-information-ibm-software-hub-54-and-related-services"
        ]
    },
    "license_service_api": {
        "name": "IBM License Service API endpoints used by this dashboard",
        "metric": "Reported per endpoint; products are authoritative for product usage",
        "terms_highlights": [
            "/products returns deployed product license usage and metricName/metricQuantity.",
            "/bundled_products returns Cloud Pak bundled product usage; keep it separate to avoid double counting.",
            "/services returns service contribution to products and is useful for drill-down attribution.",
            "/snapshot produces audit evidence for a reporting period.",
            "/health and /status explain collector health and API readiness."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/cloud-paks/foundational-services/4.x?topic=pcfls-apis",
            "https://www.ibm.com/docs/en/cloud-paks/foundational-services/4.x?topic=api-calls-retrieving-license-service-reporter-data"
        ]
    },
    "license_document_cli": {
        "name": "License document retrieval with cpd-cli",
        "metric": "License URL discovery, not runtime usage metering",
        "terms_highlights": [
            "cpd-cli manage get-license returns license URLs for a Software Hub release and can be scoped by component/license type.",
            "Use get-license during planning and entitlement review to confirm which license documents apply.",
            "Use IBM License Service products/bundled_products for live usage quantities.",
            "This dashboard keeps those two evidence streams separate: terms documents explain entitlement, License Service explains measured use."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=manage-get-license",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=planning-licenses-entitlements",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=entitlements-licensing-guidance-enterprise-edition"
        ]
    },
    "tracking_reporting_policy": {
        "name": "Tracking and reporting use against license terms",
        "metric": "License Service measures use; apply/remove entitlements when services change",
        "terms_highlights": [
            "IBM states that License Service measures Software Hub use against license terms.",
            "When services are added, apply-entitlement might need to be rerun if the service is not covered by an existing license.",
            "When services are removed, remove-entitlement should only be run when no remaining service uses that license.",
            "This dashboard therefore separates selected services, installed dependencies, applied entitlements, and measured License Service output."
        ],
        "sources": [
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=1-tracking-reporting-use-against-license-terms",
            "https://www.ibm.com/docs/en/software-hub/5.4.x?topic=puish-applying-your-entitlements-1"
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
        # Standalone WKC (VPC-based, not bundled)
        {"from": "software_hub",          "to": "wkc",                   "relationship": "deploys_product",         "license_boundary": "Standalone WKC → VPC. Do not double-count if already covered by WXD_INTELLIGENCE."},
        {"from": "wkc",                   "to": "data_quality",          "relationship": "optional_install_option", "license_boundary": "enableDataQuality: true pulls DataStage as restricted DQ dependency."},
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
        "cr_kind": "WatsonxData",
        "cr_group": "watsonxdata.cpd.ibm.com",
        "category": "Lakehouse & Query Engine",
        "tier": "Core Service",
        "edition_options": ["Standard / non-premium", "Premium only when IBM entitlement says Premium"],
        "license_rule": "Default dashboard assumption: watsonx.data standard/non-premium. Use IBM License Service metricName and metricQuantity exactly as reported; do not label this Premium unless the installed entitlement row says Premium.",
        "dependencies": ["ccs", "zen", "opencontent_opensearch"],
        "default_limit_vpc": 48,
        "default_scale": "small_mincpureq",
        "pod_regex": "ibm-lh-.*|wxd-.*|presto-.*|lakehouse-.*",
        "desc": "Open lakehouse service. CR sizing and pod limits are operational indicators; IBM License Service is the metering source."
    },
    "wkc": {
        "name": "IBM Knowledge Catalog (WKC)",
        "cr_kind": "WKC",
        "cr_group": "wkc.cpd.ibm.com",
        "category": "Governance & Quality",
        "tier": "Core Service",
        "edition_options": ["Knowledge Catalog", "Knowledge Catalog Standard", "Knowledge Catalog Premium only when explicitly entitled"],
        "license_rule": "Display the installed service as IBM Knowledge Catalog unless IBM License Service reports a Standard or Premium cartridge product row. Premium/model-driven capabilities are separate and involve enableModelsOn: gpu/remote/cpu options. DataStage is only a dependency when enableDataQuality: true — do not list it as an unconditional dependency.",
        "dependencies": ["ccs", "zen", "opencontent_opensearch"],
        "conditional_dependencies": {"enableDataQuality": "datastage"},
        "default_limit_vpc": 28,
        "default_scale": "small",
        "pod_regex": "wkc-.*|wdp-.*|glossary-.*|curation-.*",
        "desc": "Automated data discovery, data quality evaluation rules, policy enforcement, and business glossary."
    },
    "datastage": {
        "name": "IBM DataStage Enterprise Cartridge",
        "cr_kind": "DataStage",
        "cr_group": "datastage.cpd.ibm.com",
        "category": "Data Integration",
        "tier": "Core / Cartridge",
        "edition_options": [
            "Standalone Enterprise Cartridge (VPC)",
            "WKC Data Quality Bundle — restricted to DQ workloads (VPC, no standalone ETL)",
            "WXD_INTEGRATION bundle — metered as RU, no separate VPC row",
            "WXD_INTELLIGENCE bundle — metered as RU, no separate VPC row"
        ],
        "license_rule": "Metric depends on deployment context: (1) Standalone Enterprise Cartridge → VPC. (2) WKC data quality only → VPC but restricted to DQ workloads; not a standalone ETL license. (3) Bundled in watsonx.data integration or intelligence (WXD_EDITION=WXD_INTEGRATION/WXD_INTELLIGENCE) → RU, no separate VPC row emitted by License Service. Check WXD_EDITION and the License Service metricName before displaying.",
        "dependencies": ["ccs", "zen"],
        "default_limit_vpc": 24,
        "default_scale": "small",
        "pod_regex": "datastage.*|ibm-cpd-datastage.*|px-runtime.*|px-compute.*",
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
        "license_rule": "Standalone MANTA/Data Lineage → VPC; requires a Knowledge Catalog family parent (WKC, IKC Standard, or IKC Premium). When bundled in watsonx.data intelligence (WXD_EDITION=WXD_INTELLIGENCE) → RU, no separate VPC row. Use License Service metricName as the authority.",
        "dependencies": ["ccs", "zen"],
        "optional_parent": ["wkc", "ikc_standard", "ikc_premium"],
        "default_limit_vpc": 16,
        "default_scale": "small",
        "pod_regex": "manta-.*|lineage-.*|metadata-asset-.*",
        "desc": "End-to-end automated graph parsing, scanner jobs, and column-level historical data lineage."
    },
    "analyticsengine": {
        "name": "IBM Analytics Engine (Apache Spark)",
        "cr_kind": "AnalyticsEngine",
        "cr_group": "analyticsengine.cpd.ibm.com",
        "category": "Analytics Compute",
        "tier": "Compute Engine",
        "edition_options": [
            "Serverless Spark — standalone (VPC)",
            "WXD_INTELLIGENCE bundle — metered as RU, no separate VPC row"
        ],
        "license_rule": "Standalone Analytics Engine → VPC. Auto-installed as dependency of WKC, watsonx.data, Data Product Hub, and others. When bundled in watsonx.data intelligence (WXD_EDITION=WXD_INTELLIGENCE) → RU. Count only if IBM License Service reports a product or bundled-product row; otherwise treat as operational dependency.",
        "dependencies": ["ccs", "zen"],
        "default_limit_vpc": 16,
        "default_scale": "small",
        "pod_regex": "spark-.*|analyticsengine-.*|iae-.*",
        "desc": "Serverless Apache Spark kernel pools and distributed compute executor instances."
    },
    "ccs": {
        "name": "Common Core Services (CCS)",
        "cr_kind": "CommonCoreServices",
        "cr_group": "ccs.cpd.ibm.com",
        "category": "Platform Foundation",
        "tier": "Foundational Dependency",
        "edition_options": ["Included with Platform"],
        "license_rule": "Foundational dependency with component ID ccs. Do not mark CCS as Standard or Premium; attribute it to the parent product and count it only when IBM License Service reports a product/bundled-product metric row.",
        "dependencies": ["zen"],
        "default_limit_vpc": 8,
        "default_scale": "small",
        "pod_regex": "ccs-.*|connections-.*|flight-.*",
        "desc": "Universal data connectivity, Arrow Flight transport, collaborative project workspaces, and catalog asset previews."
    },
    "opencontent_opensearch": {
        "name": "IBM OpenContent OpenSearch",
        "cr_kind": "OpenSearchCluster",
        "cr_group": "opensearch.cpd.ibm.com",
        "category": "Search & Indexing",
        "tier": "Platform Foundation",
        "edition_options": ["Included with Platform"],
        "license_rule": "Platform internal support program. Usage is restricted to platform metadata search, indexing, and audit logging.",
        "dependencies": [],
        "default_limit_vpc": 8,
        "default_scale": "small",
        "pod_regex": "opencontent-opensearch-.*|opensearch-.*",
        "desc": "Distributed search and analytics engine for platform metadata, asset indexing, and audit logging."
    },
    "zen": {
        "name": "IBM Software Hub Control Plane (Zen)",
        "cr_kind": "ZenService",
        "cr_group": "zen.cpd.ibm.com",
        "category": "Platform Foundation",
        "tier": "Platform Foundation",
        "edition_options": ["Software Hub Core"],
        "license_rule": "Unified platform control plane. Hosts the microservices gateway, IAM, access control, and user interface.",
        "dependencies": [],
        "default_limit_vpc": 6,
        "default_scale": "small",
        "pod_regex": "zen-.*|ibm-nginx-.*|usermgmt-.*",
        "desc": "Core control plane, unified experience UI, security gateway, and user management."
    }
}


class ClusterTelemetryCollector:
    """Discovers installed CRDs/CRs and collects live telemetry from Thanos and License Service."""

    def __init__(self):
        self.namespace_license = os.environ.get("PROJECT_LICENSE_SERVICE", "ibm-licensing")
        self.namespace_cpd = os.environ.get("PROJECT_CPD_INST_OPERANDS", "cpd-instance")
        self.custom_services: Dict[str, Any] = {}

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
        # Priority 2: username + password — perform 'oc login' automatically
        user = os.environ.get("OCP_USER")
        password = os.environ.get("OCP_PASSWORD")
        ocp_url = os.environ.get("OCP_URL")
        if user and password and ocp_url:
            self.run_cmd([
                "oc", "login", ocp_url,
                "-u", user, "-p", password,
                "--insecure-skip-tls-verify=true"
            ])
        # Priority 3: active oc session
        return self.run_cmd(["oc", "whoami", "-t"])

    def aggregate_license_metrics(self, products: List[Dict[str, Any]]) -> Dict[str, float]:
        totals: Dict[str, float] = {}
        for product in products:
            metric = str(product.get("metricName") or product.get("metric") or "UNKNOWN").upper()
            try:
                quantity = float(product.get("metricQuantity", product.get("quantity", 0)) or 0)
            except (TypeError, ValueError):
                quantity = 0.0
            totals[metric] = round(totals.get(metric, 0.0) + quantity, 2)
        return totals

    def call_license_api(self, host: str, token: str, endpoint: str) -> Dict[str, Any]:
        url = f"https://{host}/{endpoint}?token={urllib.parse.quote(token)}"
        started = time.time()
        try:
            req = urllib.request.Request(url)
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
            cr_json_str = self.run_cmd([
                "oc", "get", kind.lower(), "-n", self.namespace_cpd, "-o", "json"
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

    def get_license_service_data(self) -> Dict[str, Any]:
        host = self.run_cmd([
            "oc", "get", "route", "ibm-licensing-service-instance",
            "-n", self.namespace_license, "-o", "jsonpath={.spec.host}"
        ])
        token_b64 = self.run_cmd([
            "oc", "get", "secret", "ibm-licensing-token",
            "-n", self.namespace_license, "-o", "jsonpath={.data.token}"
        ])

        if not host or not token_b64:
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
                "retentionDays": 90,
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
            token = base64.b64decode(token_b64).decode("utf-8")
            calls = {
                endpoint: self.call_license_api(host, token, endpoint)
                for endpoint in ["products", "bundled_products", "services", "health", "status"]
            }
            products_data = calls["products"].get("data") if calls["products"]["status"] == "ok" else {}
            bundles_data = calls["bundled_products"].get("data") if calls["bundled_products"]["status"] == "ok" else {}
            services_data = calls["services"].get("data") if calls["services"]["status"] == "ok" else {}

            products = products_data.get("products", []) if isinstance(products_data, dict) else []
            bundled_products = bundles_data.get("products", []) if isinstance(bundles_data, dict) else []
            service_contributions = services_data.get("services", []) if isinstance(services_data, dict) else []
            metric_totals = self.aggregate_license_metrics(products)
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
                        "meaning": "Use with start_date/end_date for signed audit evidence.",
                        "data": {"request": {"method": "GET", "path": "/snapshot", "query": {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}}},
                    }, "Signed audit evidence package."),
                    (calls["health"], "License Service health."),
                    (calls["status"], "License Service status page/API readiness."),
                ]
            ]
            return {
                "status": "connected",
                "mode": "Live IBM License Service API",
                "host": host,
                "products": products,
                "bundledProducts": bundled_products,
                "serviceContributions": service_contributions,
                "metricTotals": metric_totals,
                "totalVpc": metric_totals.get("VPC", 0.0),
                "totalRu": metric_totals.get("RU", 0.0),
                "clusterPeakVpc": float(os.environ.get("IBM_VPC_ENTITLEMENT", "128")),
                "ruEntitlement": float(os.environ.get("IBM_RU_ENTITLEMENT", "1000")),
                "retentionDays": 90,
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

    def query_prometheus(self, query: str) -> Optional[Any]:
        thanos_host = self.run_cmd([
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

    def get_install_options_overview(self) -> Dict[str, Any]:
        local = self.read_local_install_options()
        text = local.get("text", "")
        components = os.environ.get("COMPONENTS", "")
        ikc_type = os.environ.get("IKC_TYPE", "")
        entries = []

        for option in INSTALL_OPTIONS_CATALOG:
            inferred = self.infer_option_status(option, text, components)
            entry = {
                **option,
                "status": inferred["status"],
                "evidence": inferred["evidence"],
            }
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
                "pod_regex": item.get("pod_regex") or component_id.replace("-", "_") + ".*",
                "license_rule": (
                    "Supported Software Hub service. Add for monitoring only after entitlement and "
                    "License Service product/bundled-product rows are verified."
                )
            })
        return supported

    def get_dependency_explorer(self, installed_services: Dict[str, Any], install_options: Dict[str, Any]) -> Dict[str, Any]:
        installed_ids = set(installed_services.keys())
        option_status = {entry.get("id"): entry for entry in install_options.get("entries", [])}
        nodes = []
        for node in DEPENDENCY_EXPLORER_CATALOG["nodes"]:
            option = option_status.get(node["id"])
            installed = node["id"] in installed_ids
            nodes.append({
                **node,
                "installed": installed,
                "option_status": option.get("status") if option else None,
                "evidence": option.get("evidence") if option else "Relationship catalog entry; verify with live CRs, install-options, entitlement, and License Service rows.",
            })

        edges = []
        for edge in DEPENDENCY_EXPLORER_CATALOG["edges"]:
            option = option_status.get(edge["to"]) or option_status.get(edge["from"])
            edges.append({
                **edge,
                "status": option.get("status") if option else "relationship",
                "evidence": option.get("evidence") if option else edge.get("license_boundary", "Verify with IBM docs and License Service."),
            })

        return {
            "nodes": nodes,
            "edges": edges,
            "sources": DEPENDENCY_EXPLORER_CATALOG["sources"],
            "positioning": (
                "This explorer is intentionally conservative: optional capabilities and dependencies explain deployment shape, "
                "but License Service product/bundled-product rows and entitlement terms decide metering."
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

    def get_cluster_overview(self) -> Dict[str, Any]:
        lic = self.get_license_service_data()
        services = self.get_service_telemetry()
        supported_services = self.get_supported_services(services)
        install_options = self.get_install_options_overview()
        dependency_explorer = self.get_dependency_explorer(services, install_options)

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
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:ital,wght@0,400;0,500;0,600;0,700;1,400&family=IBM+Plex+Sans:ital,wght@0,300;0,400;0,500;0,600;0,700;1,400&display=swap" rel="stylesheet">
<style>
  /* -------------------------------------------------------------------------
     OFFICIAL IBM CARBON DESIGN SYSTEM (carbondesignsystem.com) SPEC
     ------------------------------------------------------------------------- */
  :root {
    --cds-font-sans: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    --cds-font-mono: "IBM Plex Mono", "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
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
  .cds--terms-list {
    list-style: none;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
    gap: 8px 16px;
    margin-top: 8px;
    font-size: 12px;
    color: var(--cds-text-secondary);
  }
  .cds--terms-list li {
    position: relative;
    padding-left: 14px;
  }
  .cds--terms-list li::before {
    content: "•";
    position: absolute;
    left: 0;
    color: var(--cds-interactive-accent);
    font-weight: bold;
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
    min-height: 640px;
    background:
      linear-gradient(90deg, color-mix(in srgb, var(--cds-layer-02) 26%, transparent) 1px, transparent 1px),
      linear-gradient(0deg, color-mix(in srgb, var(--cds-layer-02) 26%, transparent) 1px, transparent 1px),
      var(--cds-background);
    background-size: 40px 40px;
    position: relative;
  }
  .cds--neo-canvas svg {
    width: 100%;
    height: 640px;
    display: block;
  }
  .cds--neo-link {
    stroke: var(--cds-border-strong-01);
    stroke-width: 1.4;
    fill: none;
    opacity: 0.72;
    transition: stroke 0.18s, opacity 0.18s, stroke-width 0.18s;
    cursor: pointer;
    pointer-events: stroke;
  }
  .cds--neo-link-hit {
    stroke: transparent;
    stroke-width: 18;
    fill: none;
    cursor: pointer;
    pointer-events: stroke;
  }
  .cds--neo-link.is-emphasis {
    stroke: var(--cds-interactive-01);
    stroke-width: 2.4;
    opacity: 1;
  }
  .cds--neo-link.is-selected-path {
    stroke: var(--cds-support-success);
    stroke-width: 2.8;
    opacity: 1;
    stroke-dasharray: 8 5;
    animation: cdsFlowDash 1.8s linear infinite;
  }
  .cds--neo-node {
    cursor: pointer;
  }
  .cds--neo-node circle {
    stroke: var(--cds-background);
    stroke-width: 3;
    transition: stroke 0.18s, stroke-width 0.18s;
  }
  .cds--neo-node:hover circle,
  .cds--neo-node.is-selected circle {
    stroke: var(--cds-text-primary);
    stroke-width: 4;
  }
  .cds--neo-node.is-focus circle {
    stroke: #ffffff;
    stroke-width: 5;
  }
  .cds--neo-label-bg {
    fill: var(--cds-background);
    stroke: var(--cds-border-subtle-01);
    stroke-width: 1;
    opacity: 0.96;
  }
  .cds--neo-node text {
    fill: var(--cds-text-primary);
    font-size: 11px;
    font-family: var(--cds-font-sans);
    pointer-events: none;
  }
  .cds--neo-node .cds--node-type {
    fill: var(--cds-text-helper);
    font-size: 9px;
  }
  @keyframes cdsFlowDash {
    to { stroke-dashoffset: -26; }
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
  .cds--collapsible-section {
    margin: var(--cds-spacing-07) 0;
    border: 1px solid var(--cds-border-subtle-01);
    background: var(--cds-layer-01);
  }
  .cds--collapsible-section > summary {
    list-style: none;
    cursor: pointer;
    padding: var(--cds-spacing-05);
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  .cds--collapsible-section > summary::-webkit-details-marker {
    display: none;
  }
  .cds--collapsible-section > summary::after {
    content: "+";
    font-family: var(--cds-font-mono);
    color: var(--cds-interactive-01);
    font-size: 18px;
  }
  .cds--collapsible-section[open] > summary::after {
    content: "-";
  }
  .cds--collapsible-content {
    padding: 0 var(--cds-spacing-05) var(--cds-spacing-05);
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
  .cds--dependency-grid {
    display: grid;
    grid-template-columns: minmax(220px, 0.9fr) minmax(280px, 1.2fr) minmax(280px, 1.2fr) minmax(220px, 0.9fr);
    gap: 1px;
    background: var(--cds-border-subtle-01);
  }
  .cds--dependency-lane {
    background: var(--cds-layer-01);
    min-height: 360px;
    padding: var(--cds-spacing-04);
  }
  .cds--dependency-lane h3 {
    font-size: 12px;
    text-transform: uppercase;
    color: var(--cds-text-helper);
    letter-spacing: 0.6px;
    margin-bottom: var(--cds-spacing-04);
  }
  .cds--graph-node {
    width: 100%;
    text-align: left;
    border: 1px solid var(--cds-border-subtle-01);
    background: var(--cds-background);
    color: var(--cds-text-primary);
    padding: 10px 12px;
    margin-bottom: 10px;
    cursor: pointer;
    position: relative;
    transition: transform 0.18s cubic-bezier(0.2, 0, 0.38, 0.9), border-color 0.18s, background-color 0.18s;
  }
  .cds--graph-node:hover,
  .cds--graph-node.is-active {
    transform: translateX(2px);
    border-color: var(--cds-interactive-01);
    background: var(--cds-layer-02);
  }
  .cds--graph-node::after {
    content: "";
    position: absolute;
    right: -14px;
    top: 50%;
    width: 18px;
    height: 1px;
    background: var(--cds-interactive-01);
    opacity: 0.42;
  }
  .cds--dependency-lane:last-child .cds--graph-node::after {
    display: none;
  }
  .cds--graph-node-title {
    display: block;
    font-size: 13px;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .cds--graph-node-meta {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-bottom: 6px;
  }
  .cds--graph-node p {
    font-size: 11px;
    color: var(--cds-text-secondary);
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
    min-height: 32px;
  }
  .cds--license-terms-box {
    background-color: var(--cds-layer-02);
    border-left: 3px solid var(--cds-interactive-accent);
    padding: 8px 10px;
    font-size: 11px;
    color: var(--cds-text-secondary);
    margin-bottom: 12px;
    border-radius: 0 2px 2px 0;
  }
  .cds--license-terms-box strong {
    color: var(--cds-text-primary);
    display: block;
    margin-bottom: 2px;
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

  /* Carbon Data Table */
  .cds--data-table-container {
    background-color: var(--cds-layer-01);
    border: 1px solid var(--cds-border-subtle-01);
    overflow-x: auto;
    margin-bottom: var(--cds-spacing-08);
  }
  table.cds--data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  table.cds--data-table th {
    background-color: var(--cds-layer-02);
    color: var(--cds-text-primary);
    text-align: left;
    padding: 12px 16px;
    font-weight: 600;
    border-bottom: 1px solid var(--cds-border-subtle-01);
  }
  table.cds--data-table td {
    padding: 12px 16px;
    border-bottom: 1px solid var(--cds-border-subtle-01);
    color: var(--cds-text-primary);
  }
  table.cds--data-table tr:hover td {
    background-color: var(--cds-layer-02);
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
    .cds--dependency-grid {
      grid-template-columns: 1fr;
    }
    .cds--graph-node::after {
      display: none;
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

  <section class="cds--finance-grid" id="finance-command-center"></section>

  <!-- IBM release and license posture -->
  <div class="cds--terms-container">
    <div class="cds--terms-header">
      <div class="cds--terms-title">
        <span>Compliance posture</span>
        <span class="cds--tag cds--tag--green">watsonx.data standard / non-premium</span>
      </div>
      <div style="font-size:12px; color:var(--cds-text-helper);">
        Premium reference: <strong>L-PCPF-BJV4WW</strong> only when entitlement says Premium
      </div>
    </div>
    <details class="cds--finance-details">
      <summary>Show compliance guardrails and interpretation rules</summary>
      <ul class="cds--terms-list">
        <li><strong>Metric truth:</strong> read IBM License Service <code>metricName</code> and <code>metricQuantity</code>; keep RU and VPC separate.</li>
        <li><strong>Release scope:</strong> includes Software Hub / CPD 5.3 with watsonx 2.3 and Software Hub / CPD 5.4 with watsonx 2.4.</li>
        <li><strong>Premium guardrail:</strong> do not label watsonx.data as Premium unless License Service and entitlement records say Premium.</li>
        <li><strong>Knowledge Catalog boundary:</strong> base WKC is IBM Knowledge Catalog; Premium/model features are a separate entitlement and can involve GPU/remote model placement.</li>
        <li><strong>Data quality boundary:</strong> Knowledge Catalog data quality can use DataStage components; show restricted DQ use separately from standalone DataStage.</li>
        <li><strong>Lineage boundary:</strong> MANTA / data lineage is tracked as its own lineage capability, not as a generic WKC data quality toggle.</li>
        <li><strong>Audit trail:</strong> use License Service snapshot evidence for audits; Prometheus limits are sizing context.</li>
      </ul>
    </details>
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

  <details class="cds--collapsible-section">
    <summary>
      <div>
        <h2 class="cds--section-title">IBM source map</h2>
        <div style="font-size:12px; color:var(--cds-text-helper);">
          Public IBM pages used for release, entitlement, dependency, and metric interpretation
        </div>
      </div>
    </summary>
    <div class="cds--collapsible-content">
      <div class="cds--source-grid" id="source-grid"></div>
    </div>
  </details>

  <details class="cds--collapsible-section">
    <summary>
      <div>
        <h2 class="cds--section-title">Install options and dependency impact</h2>
        <div style="font-size:12px; color:var(--cds-text-helper);" id="install-options-source">
          install-options.yml not loaded yet
        </div>
      </div>
    </summary>
    <div class="cds--collapsible-content">
      <div class="cds--option-grid" id="install-options-grid"></div>
    </div>
  </details>

  <div class="cds--section-header">
    <h2 class="cds--section-title">Product, add-on and dependency explorer</h2>
    <div style="font-size:12px; color:var(--cds-text-helper);">
      Separates core products, optional install options, dependencies, integrated add-ons, and Premium references
    </div>
  </div>
  <div class="cds--dependency-explorer" id="dependency-explorer"></div>

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

  <!-- High-Level Metric Tiles (Carbon 2x Grid) -->
  <div class="cds--grid-overview">
    <div class="cds--tile">
      <div class="cds--tile__label">
        <span>VPC reported</span>
        <span class="cds--tag cds--tag--green" id="vpc-status-tag">Within limit</span>
      </div>
      <div class="cds--tile__value" id="tot-vpc">--</div>
      <div class="cds--tile__helper" id="vpc-helper">VPC entitlement from IBM_VPC_ENTITLEMENT</div>
      <div id="vpc-mini-bars"></div>
      <div class="cds--progress-bar"><div class="cds--progress-bar__fill" id="vpc-bar" style="background-color:var(--cds-interactive-accent); width:0%;"></div></div>
    </div>

    <div class="cds--tile">
      <div class="cds--tile__label">
        <span>RU reported</span>
        <span id="cpu-pct-tag">--%</span>
      </div>
      <div class="cds--tile__value" id="tot-ru">--</div>
      <div class="cds--tile__helper" id="ru-helper">RU entitlement from IBM_RU_ENTITLEMENT</div>
      <div id="ru-mini-bars"></div>
      <div class="cds--progress-bar"><div class="cds--progress-bar__fill" id="cpu-bar" style="width:0%;"></div></div>
    </div>

    <div class="cds--tile">
      <div class="cds--tile__label">
        <span>CPU allocation</span>
        <span id="mem-pct-tag">--%</span>
      </div>
      <div class="cds--tile__value" id="tot-cpu">--</div>
      <div class="cds--tile__helper">Live/query fallback: container CPU usage vs limits</div>
      <div id="cpu-mini-bars"></div>
      <div class="cds--progress-bar"><div class="cds--progress-bar__fill" id="mem-bar" style="background-color:var(--cds-support-success); width:0%;"></div></div>
    </div>

    <div class="cds--tile">
      <div class="cds--tile__label">
        <span>Discovered Stack Services</span>
        <span class="cds--tag cds--tag--blue" id="live-cr-tag">CRDs Active</span>
      </div>
      <div class="cds--tile__value" id="svc-count-val">--</div>
      <div class="cds--tile__helper">Operators Reconciled</div>
      <div class="cds--progress-bar"><div class="cds--progress-bar__fill" style="background-color:var(--cds-support-info); width:100%;"></div></div>
    </div>
  </div>

  <!-- Services & CR Sizing Grid -->
  <div class="cds--section-header">
    <h2 class="cds--section-title">Installed Services, CRD Sizing &amp; License Terms</h2>
    <div style="font-size:12px; color:var(--cds-text-helper);">
      Showing Live Custom Resource Sizing (<code>spec.scaleConfig</code>) &amp; Dependencies
    </div>
  </div>
  <div class="cds--services-grid" id="services-grid">
    <!-- Dynamic Service Cards -->
  </div>

  <details class="cds--collapsible-section">
    <summary>
      <div>
        <h2 class="cds--section-title">License Service API coverage</h2>
        <div style="font-size:12px; color:var(--cds-text-helper);">
          Shows what the standalone collector can use from IBM License Service
        </div>
      </div>
    </summary>
    <div class="cds--collapsible-content">
      <div class="cds--endpoint-grid" id="endpoint-grid"></div>
    </div>
  </details>

  <!-- IBM License Service Registered Products Table -->
  <div class="cds--section-header">
    <h2 class="cds--section-title">IBM License Service &mdash; registered products</h2>
    <div style="font-size:12px; color:var(--cds-text-helper);">
      IBM License Service REST API: <code>/products</code>
    </div>
  </div>
  <div class="cds--data-table-container">
    <table class="cds--data-table">
      <thead>
        <tr>
          <th>Product Name</th>
          <th>Product Identifier</th>
          <th>Edition / Tier</th>
          <th>Metric Unit</th>
          <th>Sub-Capacity Quantity</th>
          <th>Reported Status</th>
        </tr>
      </thead>
      <tbody id="lic-table-body">
        <!-- Dynamic Product Rows -->
      </tbody>
    </table>
  </div>

  <footer class="cds--footer">
    <div>Target: <span id="footer-ocp-info">api.watson.ibmas-zocp-techcluster.org:6443</span></div>
    <div>IBM Software Hub / CPD 5.3-5.4 Telemetry | Carbon-inspired presentation view</div>
  </footer>

</main>

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
    <pre id="raw-modal-body">{}</pre>
    <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
      <button class="cds--btn cds--btn--secondary" onclick="copyRawInspector()">Copy JSON</button>
      <button class="cds--btn cds--btn--primary" onclick="closeRawInspector()">Close</button>
    </div>
  </div>
</div>

<script>
let currentScaleOverride = 'live';
let rawData = null;
let supportedServices = [];
let rawInspectorItems = [];
let dependencyExplorerState = { nodesById: {}, edges: [], selectedId: null };

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function addRawInspectorItem(title, payload) {
  rawInspectorItems.push({ title, payload });
  return rawInspectorItems.length - 1;
}

function openRawInspector(index) {
  const item = rawInspectorItems[index];
  if (!item) return;
  document.getElementById('raw-modal-title').innerText = item.title || 'Raw evidence';
  document.getElementById('raw-modal-body').innerText = JSON.stringify(item.payload ?? {}, null, 2);
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
  drawDependencyLineage(nodeId);
  document.querySelectorAll('.cds--neo-node').forEach(el => el.classList.toggle('is-selected', el.dataset.nodeId === nodeId));
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
  drawDependencyLineage(edge.from);
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

function dependencyNodeColor(node) {
  const type = node?.type || '';
  if (type === 'metering') return 'var(--cds-support-success)';
  if (type.includes('premium')) return 'var(--cds-interactive-accent)';
  if (type.includes('option') || type.includes('add_on')) return 'var(--cds-interactive-01)';
  if (type.includes('dependency')) return 'var(--cds-support-info)';
  return 'var(--cds-text-helper)';
}

function compactLabel(label, max = 22) {
  label = String(label || '');
  return label.length > max ? label.slice(0, max - 3) + '...' : label;
}

function wrapSvgLabel(label, max = 19) {
  const words = String(label || '').split(/\\s+/).filter(Boolean);
  const lines = [];
  let current = '';
  words.forEach(word => {
    const next = current ? `${current} ${word}` : word;
    if (next.length <= max || !current) {
      current = next;
    } else {
      lines.push(current);
      current = word;
    }
  });
  if (current) lines.push(current);
  if (lines.length > 2) {
    return [lines[0], compactLabel(lines.slice(1).join(' '), max)];
  }
  return lines.length ? lines : [''];
}

function dependencyDepthSort(a, b) {
  const priority = type => {
    type = String(type || '');
    if (type === 'metering') return 0;
    if (type === 'platform') return 1;
    if (type.includes('core_product')) return 2;
    if (type.includes('dependency')) return 3;
    if (type.includes('option') || type.includes('add_on')) return 4;
    if (type.includes('premium')) return 5;
    return 6;
  };
  return priority(a.type) - priority(b.type) || String(a.label).localeCompare(String(b.label));
}

function layoutLineage(selectedId) {
  const nodesById = dependencyExplorerState.nodesById;
  const edges = dependencyExplorerState.edges;
  const selected = nodesById[selectedId] || Object.values(nodesById)[0];
  if (!selected) return { nodes: [], edges: [] };
  selectedId = selected.id;

  const incoming = new Map();
  const outgoing = new Map();
  edges.forEach((edge, index) => {
    edge.edgeIndex = edge.edgeIndex ?? index;
    if (!incoming.has(edge.to)) incoming.set(edge.to, []);
    if (!outgoing.has(edge.from)) outgoing.set(edge.from, []);
    incoming.get(edge.to).push(edge);
    outgoing.get(edge.from).push(edge);
  });

  const depthById = new Map([[selectedId, 0]]);
  const lineageEdgeIndexes = new Set();
  const visit = (startId, direction, maxDepth = 4) => {
    const queue = [{ id: startId, depth: 0 }];
    const seen = new Set([startId]);
    while (queue.length) {
      const current = queue.shift();
      if (Math.abs(current.depth) >= maxDepth) continue;
      const nextEdges = direction === 'upstream' ? (incoming.get(current.id) || []) : (outgoing.get(current.id) || []);
      nextEdges.forEach(edge => {
        const nextId = direction === 'upstream' ? edge.from : edge.to;
        if (!nodesById[nextId] || seen.has(nextId)) return;
        seen.add(nextId);
        lineageEdgeIndexes.add(edge.edgeIndex);
        const nextDepth = current.depth + (direction === 'upstream' ? -1 : 1);
        const previous = depthById.get(nextId);
        if (previous === undefined || Math.abs(nextDepth) < Math.abs(previous)) {
          depthById.set(nextId, nextDepth);
        }
        queue.push({ id: nextId, depth: nextDepth });
      });
    }
  };

  visit(selectedId, 'upstream');
  visit(selectedId, 'downstream');

  // Keep shared platform and metering context visible for ancestor products.
  [...depthById.keys()].forEach(id => {
    if ((depthById.get(id) || 0) > 0) return;
    (outgoing.get(id) || []).forEach(edge => {
      const target = nodesById[edge.to];
      if (!target) return;
      const contextType = String(target.type || '');
      const relationship = String(edge.relationship || '');
      if (contextType === 'metering' || relationship.includes('platform_dependency') || relationship.includes('measured_by')) {
        const contextDepth = Math.min(-1, (depthById.get(id) || 0) + 1);
        if (!depthById.has(edge.to)) depthById.set(edge.to, contextDepth);
      }
    });
  });

  // Foundation chains should continue left-to-right instead of stacking on top of each other.
  edges.forEach(edge => {
    if (!depthById.has(edge.from) || !depthById.has(edge.to)) return;
    const fromDepth = depthById.get(edge.from);
    const toDepth = depthById.get(edge.to);
    if (fromDepth >= 0 && toDepth <= fromDepth && edge.from !== selectedId) {
      depthById.set(edge.to, fromDepth + 1);
    }
  });

  const depths = [...depthById.values()];
  const minDepth = Math.min(...depths);
  const maxDepth = Math.max(...depths);
  const step = Math.min(210, 900 / Math.max(maxDepth - minDepth, 1));
  let shift = 0;
  const left = 540 + minDepth * step;
  const right = 540 + maxDepth * step;
  if (left < 90) shift = 90 - left;
  if (right + shift > 1010) shift = 1010 - right;

  const columns = new Map();
  depthById.forEach((depth, id) => {
    if (!columns.has(depth)) columns.set(depth, []);
    columns.get(depth).push(nodesById[id]);
  });

  const positioned = {};
  [...columns.entries()].forEach(([depth, columnNodes]) => {
    const ordered = columnNodes.slice().sort(dependencyDepthSort);
    const spacing = Math.min(92, 520 / Math.max(ordered.length - 1, 1));
    const startY = ordered.length === 1 ? 320 : 320 - ((ordered.length - 1) * spacing) / 2;
    ordered.forEach((node, index) => {
      positioned[node.id] = {
        ...node,
        x: Math.round(540 + depth * step + shift),
        y: node.id === selectedId ? 320 : Math.round(startY + spacing * index),
        depth,
        role: depth < 0 ? 'upstream' : depth > 0 ? 'dependencies' : 'selected'
      };
    });
  });

  const visibleIds = new Set(Object.keys(positioned));
  const visibleEdges = edges
    .filter(edge => visibleIds.has(edge.from) && visibleIds.has(edge.to))
    .map(edge => ({ ...edge, isLineagePath: lineageEdgeIndexes.has(edge.edgeIndex) || edge.from === selectedId || edge.to === selectedId }));
  return { nodes: Object.values(positioned), edges: visibleEdges, selectedId };
}

function drawDependencyLineage(selectedId) {
  const svg = document.getElementById('dependency-graph-svg');
  if (!svg) return;
  const lineage = layoutLineage(selectedId);
  const laneLabels = [
    ['Upstream products', 170],
    ['Selected focus', 540],
    ['Dependencies and foundation', 890]
  ].map(([label, x]) => `<text x="${x}" y="28" text-anchor="middle" fill="var(--cds-text-helper)" style="font-size:12px; text-transform:uppercase; letter-spacing:0.6px;">${esc(label)}</text>`).join('');
  const nodeById = Object.fromEntries(lineage.nodes.map(node => [node.id, node]));
  const linkHtml = lineage.edges.map((edge) => {
    const fromNode = nodeById[edge.from];
    const toNode = nodeById[edge.to];
    if (!fromNode || !toNode) return '';
    const edgeIndex = edge.edgeIndex ?? dependencyExplorerState.edges.findIndex(item => item.from === edge.from && item.to === edge.to && item.relationship === edge.relationship);
    const fromRadius = fromNode.id === lineage.selectedId ? 34 : fromNode.installed ? 28 : 24;
    const toRadius = toNode.id === lineage.selectedId ? 34 : toNode.installed ? 28 : 24;
    const direction = toNode.x >= fromNode.x ? 1 : -1;
    const sameColumn = Math.abs(toNode.x - fromNode.x) < 24;
    const x1 = sameColumn ? fromNode.x + 42 : fromNode.x + direction * (fromRadius + 10);
    const y1 = fromNode.y;
    const x2 = sameColumn ? toNode.x + 42 : toNode.x - direction * (toRadius + 10);
    const y2 = toNode.y;
    const cx = sameColumn ? x1 + 70 : (x1 + x2) / 2;
    const d = `M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`;
    const important = edge.relationship.includes('premium') || edge.relationship.includes('restricted');
    const selectedPath = edge.isLineagePath;
    const className = `cds--neo-link ${important ? 'is-emphasis' : ''} ${selectedPath ? 'is-selected-path' : ''}`;
    return `
      <path class="cds--neo-link-hit" d="${d}" onclick="showDependencyEdge(${edgeIndex})"><title>${esc(edge.relationship)}</title></path>
      <path class="${className}" d="${d}" marker-end="url(#dependency-arrow)"><title>${esc(edge.relationship)}</title></path>
    `;
  }).join('');
  const nodeHtml = lineage.nodes.map(node => {
    const radius = node.id === lineage.selectedId ? 34 : node.installed ? 28 : 24;
    const status = node.option_status || (node.installed ? 'installed' : dependencyTypeLabel(node.type));
    const labelLines = wrapSvgLabel(node.label, 19);
    const statusLine = compactLabel(status, 22);
    const labelWidth = Math.min(168, Math.max(96, Math.max(...labelLines.map(line => line.length), statusLine.length) * 6.2 + 18));
    const labelHeight = 30 + labelLines.length * 13;
    const labelY = radius + 16;
    const textLines = labelLines.map((line, index) => `<text y="${labelY + 13 + index * 13}" text-anchor="middle">${esc(line)}</text>`).join('');
    return `
      <g class="cds--neo-node ${node.id === lineage.selectedId ? 'is-focus is-selected' : ''}" data-node-id="${esc(node.id)}" onclick="showDependencyNode('${esc(node.id)}')" transform="translate(${node.x} ${node.y})">
        <circle r="${radius}" fill="${dependencyNodeColor(node)}"><title>${esc(node.label)} - ${esc(status)}</title></circle>
        <rect class="cds--neo-label-bg" x="${-labelWidth / 2}" y="${labelY}" width="${labelWidth}" height="${labelHeight}" rx="2"></rect>
        ${textLines}
        <text class="cds--node-type" y="${labelY + 16 + labelLines.length * 13}" text-anchor="middle">${esc(statusLine)}</text>
      </g>
    `;
  }).join('');
  svg.innerHTML = `
    <defs>
      <marker id="dependency-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M 0 0 L 8 4 L 0 8 z" fill="var(--cds-border-strong-01)"></path>
      </marker>
    </defs>
    ${laneLabels}
    ${linkHtml}
    ${nodeHtml}
  `;
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
}

function initPreferences() {
  const savedMode = localStorage.getItem('cds_mode') || 'dark';
  document.documentElement.setAttribute('data-mode', savedMode);
  document.getElementById('mode-text').innerText = savedMode === 'dark' ? 'Dark Mode' : 'Light Mode';
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
  try {
    const res = await fetch('/api/telemetry');
    const data = await res.json();
    rawData = data;
    renderDashboard(data);
  } catch (err) {
    console.error('Failed to fetch telemetry:', err);
  }
}

function renderDashboard(data) {
  const cluster = data.cluster_info;
  rawInspectorItems = [];
  supportedServices = data.supported_services || [];
  if (document.getElementById('add-modal')?.style.display === 'flex' && document.getElementById('supported-svc-select')?.options.length <= 1) {
    populateSupportedServiceDropdown();
  }
  document.getElementById('footer-ocp-info').innerText = cluster.ocp_url;
  renderReleaseMatrix(data.terms_info);
  renderSourceMap(data.terms_info);
  renderInstallOptions(data.install_options || {});
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

  renderFinanceCommandCenter(data, {
    totalVpc,
    totalRu,
    vpcEntitled,
    ruEntitled,
    vpcPct,
    ruPct,
    totalCpuUsed,
    totalCpuLimit,
    cpuPct
  });

  renderServices(services);
  renderLicenseTable(data.licensing.products);
  renderApiCoverage(data.licensing.apiCoverage || []);
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
      color: dependencyNodeColor(node),
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
  dependencyExplorerState = { nodesById: nodeMap, edges: graphEdges, selectedId: nodes.find(node => node.id === 'watsonx_data') ? 'watsonx_data' : (nodes[0]?.id || '') };

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
  const optionNodes = nodes.filter(node => node.type.includes('option')).length;
  const dependencyNodes = nodes.filter(node => node.type.includes('dependency')).length;

  root.innerHTML = `
    <div class="cds--dependency-toolbar">
      <div style="font-size:12px; color:var(--cds-text-secondary); max-width:860px;">${esc(graph.positioning || 'Dependency explorer')}</div>
      <div class="cds--method-row" style="margin-top:0;">
        <span class="cds--tag cds--tag--blue">${esc(optionNodes)} options/add-ons</span>
        <span class="cds--tag cds--tag--gray">${esc(dependencyNodes)} dependencies</span>
        <span class="cds--tag cds--tag--purple">${esc(premiumEdges)} premium-reference paths</span>
        <button class="cds--raw-link" onclick="openRawInspector(${sourcesIndex})">View graph JSON</button>
      </div>
    </div>
    <div class="cds--neo-layout">
      <div class="cds--neo-canvas">
        <svg id="dependency-graph-svg" viewBox="0 0 1100 640" role="img" aria-label="Interactive product dependency graph"></svg>
      </div>
      <aside class="cds--neo-side">
        <h3>How to read the graph</h3>
        <p>Click any node or relationship to open a plain-language impact card here. Solid emphasis lines mark relationships that need special care, such as Premium references or restricted dependency interpretation.</p>
        <ul>
          <li>Blue nodes are optional install options or integrated add-ons.</li>
          <li>Light-blue nodes are dependencies or dependency/product boundary cases.</li>
          <li>Purple nodes are Premium references, not default entitlement.</li>
          <li>Green means metering evidence, especially IBM License Service.</li>
        </ul>
        <div class="cds--neo-legend">
          <span><span class="cds--legend-dot" style="background:var(--cds-interactive-01);"></span>option/add-on</span>
          <span><span class="cds--legend-dot" style="background:var(--cds-support-info);"></span>dependency</span>
          <span><span class="cds--legend-dot" style="background:var(--cds-interactive-accent);"></span>premium reference</span>
          <span><span class="cds--legend-dot" style="background:var(--cds-support-success);"></span>metering</span>
        </div>
        <div id="dependency-detail-panel"></div>
      </aside>
    </div>
    <div class="cds--relationship-list">${relationshipCards}</div>
  `;
  showDependencyNode(dependencyExplorerState.selectedId);
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
      ? `<div style="font-size:11px; color:var(--cds-text-helper); margin-top:6px;"><strong>Dependencies:</strong> ${s.dependencies.map(d => `<code class="cds--snippet">${esc(d)}</code>`).join(' ')}</div>`
      : '';
    const rawIndex = addRawInspectorItem(`Service telemetry: ${s.name}`, {
      service_id: id,
      active_scale: activeScale,
      computed: {
        cpu_used_cores: Number(cpuUsed),
        cpu_limit_cores: Number(cpuLimit),
        cpu_percent: cpuPct,
        memory_used_gb: Number(memUsed),
        memory_limit_gb: Number(memLimit),
        memory_percent: memPct
      },
      source: s
    });
    const serviceBars = renderMiniBars(
      [cpuPct * 0.45, memPct * 0.45, cpuPct * 0.62, memPct * 0.62, cpuPct * 0.78, memPct * 0.78, cpuPct, memPct, Math.max(cpuPct, memPct) * 0.9, Math.min(100, (cpuPct + memPct) / 2), cpuPct, memPct],
      cpuPct > 85 || memPct > 85 ? 'var(--cds-support-danger)' : 'var(--cds-interactive-01)'
    );

    const card = document.createElement('div');
    card.className = 'cds--service-card';
    card.onclick = () => openRawInspector(rawIndex);
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
          ${esc(s.license_rule)}
          ${depsHtml}
        </div>
        <div class="cds--meta-row">
          <span class="cds--tag cds--tag--purple">CR sizing: ${esc(activeScale)}</span>
          <span class="cds--tag cds--tag--gray">CR: ${esc(s.cr_name)}</span>
          <span class="cds--tag cds--tag--blue">Kind: ${esc(s.cr_kind)}</span>
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
          <span class="cds--tag cds--tag--gray">Click card for pod, CR and JSON evidence</span>
        </div>
      </div>
    `;
    grid.appendChild(card);
  }
}

function renderLicenseTable(products) {
  const tbody = document.getElementById('lic-table-body');
  tbody.innerHTML = '';
  if (!products || products.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--cds-text-helper);">No products registered</td></tr>';
    return;
  }
  products.forEach(p => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td style="font-weight:600;">${esc(p.name)}</td>
      <td><code class="cds--snippet">${esc(p.id || p.productId || 'n/a')}</code></td>
      <td><span class="cds--tag cds--tag--purple">${esc(p.edition || p.release || 'Reported')}</span></td>
      <td>${esc(p.metricName || 'Reported')}</td>
      <td style="font-weight:600; color:var(--cds-interactive-01); font-family:var(--cds-font-mono);">${esc(p.metricQuantity ?? 0)} ${esc(p.metricName || '')}</td>
      <td><span class="cds--tag cds--tag--green">${esc(p.status || 'Reported')}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

function exportSnapshot() {
  if (!rawData) return;
  const snapshot = {
    export_timestamp: new Date().toISOString(),
    audit_period_retention_days: 90,
    cluster_metadata: rawData.cluster_info,
    terms_reference: rawData.terms_info,
    services_crd_sizing: rawData.services,
    licensing_entitlements: rawData.licensing
  };
  const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `ibm-software-hub-audit-snapshot-${new Date().toISOString().slice(0,10)}.json`;
  a.click();
  URL.revokeObjectURL(url);
}

initPreferences();
fetchData();
setInterval(fetchData, 10000);
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
    server = HTTPServer((host, port), DashboardHTTPHandler)
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
