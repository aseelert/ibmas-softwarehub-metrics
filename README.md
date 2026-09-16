# IBM Software Hub & Cloud Pak for Data: Usage Metering, Licensing & Sizing Dashboard

A standalone, lightweight, air-gapped operational telemetry dashboard for **IBM Software Hub / Cloud Pak for Data (CPD 5.3 & 5.4)** and **watsonx (2.3 & 2.4)** workloads on **Red Hat OpenShift**.

Built strictly with the official **IBM Carbon Design System v11** design language and **IBM Plex typography**, this tool provides instantaneous visibility into:
1. **IBM License Service Metrics & Entitlement Compliance**: Live and peak Virtual Processor Cores (VPC) and Resource Units (RU) across products, bundled features, and cartridges.
2. **watsonx.data & Cartridge Entitlement Grounding**: Distinguishes standard/non-premium vs. Premium editions (grounded on IBM License Information Document `L-PCPF-BJV4WW`), Common Core Services (zero VPC foundation), and restricted DataStage bundling for IBM Knowledge Catalog data quality.
3. **Live Sizing & Profile Telemetry**: Reads live Custom Resource (`spec.scaleConfig`, `parameters.scaleConfig`) allocations and offers interactive T-shirt sizing simulations (`small_mincpureq`, `small`, `medium`, `large`).
4. **Cluster Performance vs. Limits**: Live CPU and memory limits versus instantaneous utilization via OpenShift Thanos / Prometheus PromQL.

---

## ⚡ Key Highlights

* **Zero External Dependencies**: Runs entirely on the standard Python 3 library (`http.server`, `urllib`, `json`, `subprocess`). No mandatory `pip` packages, NodeJS, or external internet connection required — ideal for air-gapped clusters.
* **IBM Carbon v11 Native UI**: Responsive UI shell (`.cds--header`), tabs, Carbon data tables, linear progress bars, tag components, and dark/light mode toggle.
* **Dual-Mode Operation**: Automatically queries live OpenShift APIs when `oc` is logged in, or falls back to built-in simulation/mock mode for sizing reviews and offline presentations.

---

## 🚀 Quickstart & Installation

### Prerequisites
* **Python 3.8+**
* (Optional) **OpenShift CLI (`oc`)** logged into your cluster (`oc login ...`).

### 1. Set Up Environment
```bash
git clone https://github.com/aseelert/ibmas-softwarehub-metrics.git
cd ibmas-softwarehub-metrics

# Create and activate Python virtual environment
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Configure Environment Variables
Copy the reduced `.env.example` file to `.env`:
```bash
cp .env.example .env
```

Review and adjust variables in `.env` as needed:
```bash
# Target namespaces
PROJECT_LICENSE_SERVICE=ibm-licensing
PROJECT_CPD_INST_OPERANDS=cpd-instance

# Entitlement baselines for compliance calculations
IBM_VPC_ENTITLEMENT=128
IBM_RU_ENTITLEMENT=1000

# Optional: OpenShift API URL and storage details
OCP_URL=https://api.your-cluster.com:6443
NFS_STORAGE_CLASS=managed-nfs-storage
NFS_SERVER_LOCATION=10.10.0.40
```

### 3. Launch the Dashboard
Run the launcher script (which automatically initializes and activates `.venv`):
```bash
./bin/start-dashboard.sh [PORT] [HOST]
```
Or start directly with Python inside `.venv`:
```bash
source .venv/bin/activate
python3 app/server.py 8088 0.0.0.0
```

Open your browser at **`http://localhost:8088`**.

---

## 📊 Telemetry Architecture & Data Sources

```
+--------------------------------------------------------------------------------+
|                   IBM Software Hub Telemetry & Sizing Dashboard                |
|                    (Carbon Design System v11 / IBM Plex UI)                    |
+--------------------------------------------------------------------------------+
                                       |
                   +-------------------+-------------------+
                   |                                       |
                   v                                       v
+-------------------------------------+ +-------------------------------------+
|      IBM License Service APIs       | |      OpenShift PromQL / Thanos      |
|    (Namespace: ibm-licensing)       | |     (Cluster Monitoring Stack)      |
+-------------------------------------+ +-------------------------------------+
| • /products                         | | • container_spec_cpu_quota / limits |
| • /bundled_products                 | | • container_memory_working_set      |
| • /services                         | | • node_cpu_utilization_percent      |
| • /snapshot (Signed 30/90-day logs) | | • pod_phase_status & restarts       |
| • /health & /status                 | |                                     |
+-------------------------------------+ +-------------------------------------+
                   |                                       |
                   +-------------------+-------------------+
                                       |
                                       v
+--------------------------------------------------------------------------------+
|                       Live Custom Resource (CR) Discovery                      |
|                      (Namespace: cpd-instance / operands)                      |
+--------------------------------------------------------------------------------+
| • watsonx.data (wxd): spec.scaleConfig (e.g. small_mincpureq, small, medium)  |
| • IBM Knowledge Catalog (wkc): spec.scale & data quality engine status         |
| • DataStage Enterprise (datastage): Standalone vs. WKC-bundled execution       |
| • Common Core Services (ccs): Shared zero-VPC foundation microservices         |
+--------------------------------------------------------------------------------+
```

---

## 📖 Licensing & Sizing Rules Reference

### 1. watsonx.data Standard vs. Premium
* **Standard Edition** (Default): Covers open lakehouse architecture, Apache Iceberg tables, Presto query engine, and standard object storage integrations.
* **Premium Edition**: Governed by IBM License Information Document `L-PCPF-BJV4WW`, adding Milvus vector database capabilities, advanced governance, and vector search acceleration.

### 2. Common Core Services (CCS)
* Foundational microservices shared across Cloud Pak for Data cartridges (UI shell, user management, connections catalog).
* Incurs **0 VPC surcharge** when deployed purely as a prerequisite for licensed cartridges.

### 3. DataStage Enterprise & WKC Data Quality Bundling
* When DataStage Enterprise is installed automatically to execute Data Quality rules in IBM Knowledge Catalog (WKC), its usage is **strictly restricted to data quality workloads**.
* Standalone ETL/ELT pipelines require a dedicated DataStage Enterprise Cartridge license.

---

## 🛠️ Testing

A complete automated test suite is provided using standard Python `unittest`:

```bash
# Run tests inside the virtual environment
source .venv/bin/activate
python3 -m unittest discover tests
```

---

## 📁 Repository Structure

```
ibmas-softwarehub-metrics/
├── app/
│   └── server.py                   # Complete Carbon v11 server & telemetry engine
├── bin/
│   └── start-dashboard.sh          # Shell launcher with automated venv activation
├── config/
│   ├── cpd_vars.example.sh         # OpenShift & CPD environment template
│   └── install-options.watsonx-data-no-gpu.yml  # Sizing profile definition
├── tests/
│   └── test_server.py              # Test suite for APIs and data collectors
├── .env.example                    # Clean, minimal environment variable template
├── .env                            # Active environment configuration
├── requirements.txt                # Optional Python dependencies
├── LICENSE                         # Apache 2.0 / MIT License
└── README.md                       # Documentation & operator guide
```

---

## 📄 License
This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
