# IBM Software Hub & Cloud Pak for Data: Usage Metering, Licensing & Sizing Dashboard

A standalone, lightweight, air-gapped operational telemetry dashboard for **IBM Software Hub / Cloud Pak for Data (CPD 5.3 & 5.4)** and **watsonx (2.3 & 2.4)** workloads on **Red Hat OpenShift**.

Built on the official **IBM Carbon Design System v11** and **IBM Plex typography**, the dashboard delivers instantaneous visibility into:

1. **IBM License Service Metrics & Entitlement Compliance** — Live and peak Virtual Processor Cores (VPC) and Resource Units (RU) across products, bundled features, and cartridges.
2. **watsonx.data & Cartridge Entitlement Grounding** — Distinguishes Standard vs. Premium editions (grounded on IBM License Information Document `L-PCPF-BJV4WW`), Common Core Services (zero-VPC foundation), and restricted DataStage bundling for IBM Knowledge Catalog data quality.
3. **Live Sizing & Profile Telemetry** — Reads live Custom Resource (`spec.scaleConfig`) allocations and offers interactive T-shirt sizing simulations (`small_mincpureq`, `small`, `medium`, `large`).
4. **Cluster Performance vs. Limits** — Live CPU and memory limits vs. instantaneous utilization via OpenShift Thanos / Prometheus PromQL.

---

## ⚡ Key Highlights

- **Zero Python Dependencies** — The server runs on the Python 3 standard library only (`http.server`, `urllib`, `json`, `subprocess`). No mandatory `pip` packages, no internet connection needed at runtime — ideal for air-gapped clusters. The dependency graph's rendering library (Cytoscape.js + ELK layout) is vendored as static JS in `app/vendor/` for the same reason — no CDN, no npm install.
- **App-shell UI with persistent navigation** — A left sidebar (Overview, Services, Dependency Graph, Compliance & Audit, Reference) replaces a single long scroll, with a live status rail (cluster URL, License Service connection state, last-updated time) and an animated fetch indicator so a slow poll never looks like a frozen page.
- **Interactive dependency graph** — Cytoscape.js with an ELK layered layout, gradient node fills, click-to-focus lineage highlighting, collapsible bundled-component groups, zoom controls, and a full text/table fallback view for accessibility.
- **Real compliance checks, not just metering** — Beyond IBM License Service VPC/RU numbers, it checks live node-pinning configuration (`isc-entitlement` labels) and namespace resource quota presence against IBM's own documented entitlement-application mechanism.
- **IBM Carbon-inspired UI** — IBM Plex typography, Carbon-style cards/tags/progress bars, and a dark/light mode toggle.
- **Dual-Mode Operation** — Automatically queries live OpenShift APIs when `oc` is logged in, or falls back to built-in simulation/mock mode for offline sizing reviews and presentations.

---

## 🚀 Installation & Quickstart

### Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.8+** | `python3 --version` to verify |
| **OpenShift CLI (`oc`)** | Required for live mode; install from [mirror.openshift.com](https://mirror.openshift.com/pub/openshift-v4/clients/ocp/latest/) |
| `oc login …` active session | Required for live mode; the dashboard calls `oc whoami -t` to obtain a bearer token |

> **Offline / simulation mode** works without `oc` and without a cluster connection — useful for sizing reviews and demos.

---

### Step 1 — Clone the repository

```bash
git clone https://github.com/aseelert/ibmas-softwarehub-metrics.git
cd ibmas-softwarehub-metrics
```

---

### Step 2 — Create a Python virtual environment

A virtual environment (`.venv`) isolates the project's Python runtime from the system Python. This avoids version conflicts and is the recommended way to run any Python project.

```bash
# Create the virtual environment (only needed once)
python3 -m venv .venv

# Activate it — you must do this in every new terminal session
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows (PowerShell)

# Verify you are inside the venv
which python3               # should print …/ibmas-softwarehub-metrics/.venv/bin/python3
```

> **Tip:** The launcher script `bin/start-dashboard.sh` creates and activates the venv automatically, so you only need to do this manually when running the server directly with `python3`.

#### Install optional test dependencies (pytest)

The dashboard itself has zero runtime dependencies, but `pytest` is listed in `requirements.txt` for running the test suite:

```bash
pip install -r requirements.txt   # installs pytest only
```

---

### Step 3 — Configure the environment

Copy the example file and edit the two required values:

```bash
cp .env.example .env
```

Open `.env` in your editor and set your cluster namespaces and entitlement thresholds (see the [Environment Variables Reference](#-environment-variables-reference) section below for full details).

The dashboard's built-in loader reads `.env` from the repository root automatically at startup — **no `python-dotenv` package or shell exports needed**.

---

### Step 4 — Start the dashboard

**Option A — using the launcher script (recommended)**

```bash
./bin/start-dashboard.sh           # defaults: port 8088, host 0.0.0.0
./bin/start-dashboard.sh 9090      # custom port
./bin/start-dashboard.sh 9090 127.0.0.1  # custom port + bind address
```

The script creates `.venv` if it does not yet exist, activates it, loads `.env`, and starts the server.

**Option B — directly with Python**

```bash
source .venv/bin/activate
python3 app/server.py              # port 8088, host 0.0.0.0 (defaults)
python3 app/server.py 9090
python3 app/server.py 9090 127.0.0.1
```

Open your browser at **`http://localhost:8088`** (or your chosen port).

---

## 🔌 Ports & Network Access

| Port | Direction | Purpose | Required? |
|---|---|---|---|
| `8088` | Inbound to this app | The dashboard UI + API, served to your browser | Always (or whichever port you choose) |
| `6443` | Outbound to cluster | OpenShift API server (`oc login`, `oc get ...`) | Always, for live mode |
| `18080` → cluster `8080` | Outbound via `oc port-forward` | IBM License Service API | Only if the License Service Route's `apps.*` domain doesn't resolve from where this runs (common when running from a laptop outside the cluster's DNS) |
| `19091` → cluster `9091` | Outbound via `oc port-forward` | OpenShift Thanos/Prometheus (CPU/memory telemetry) | Same caveat as above |

**Symptom that means you need one of the fixes below**: the "IBM License Service — registered products" section shows *"No products registered yet"* and the licensing status is `unreachable` (not `simulated` — `unreachable` means the host and token both resolved but the actual HTTPS calls failed). This happens when your machine can resolve the cluster's `api.*` apiserver hostname (so `oc login`/`oc get` work fine) but *not* its `apps.*` wildcard route domain — a DNS-scoping gap some corporate/VPN networks have, not a cluster problem.

**Option A — `/etc/hosts` entry (permanent, no background process to keep running).** Find your cluster's ingress IP — it's whatever IP your other `*.apps.<your-cluster-domain>` routes already resolve to, or ask whoever manages the cluster/VPN — then add two lines mapping that IP to the two routes this app needs:
```bash
sudo sh -c 'cat >> /etc/hosts <<EOF
<ingress-ip>  ibm-licensing-service-instance-ibm-licensing.apps.<your-cluster-domain>
<ingress-ip>  thanos-querier-openshift-monitoring.apps.<your-cluster-domain>
EOF'
```
Get the exact two hostnames for your cluster with:
```bash
oc get route ibm-licensing-service-instance -n ibm-licensing -o jsonpath='{.spec.host}'
oc get route thanos-querier -n openshift-monitoring -o jsonpath='{.spec.host}'
```
No `.env` changes or restart-dependent background processes needed — once `/etc/hosts` resolves them, the dashboard talks to the Routes directly.

**Option B — `oc port-forward` (quicker to try, but must keep running in a terminal):**
```bash
oc port-forward -n ibm-licensing svc/ibm-licensing-service-instance 18080:8080
oc port-forward -n openshift-monitoring svc/thanos-querier 19091:9091
```
Then uncomment in `.env` and restart the dashboard:
```bash
LICENSE_SERVICE_HOST=localhost:18080
THANOS_HOST=localhost:19091
```

---

## 🔧 Environment Variables Reference

The dashboard reads variables from `.env` (auto-loaded at startup) or from the shell environment. Every variable has a built-in default, so the dashboard starts with an empty `.env` — but the **Required** variables should be set for accurate results.

### Required

| Variable | Default | Purpose |
|---|---|---|
| `PROJECT_LICENSE_SERVICE` | `ibm-licensing` | Namespace where the IBM License Service operator is installed |
| `PROJECT_CPD_INST_OPERANDS` | `cpd-instance` | Namespace where CPD / Software Hub operand pods run |
| `IBM_VPC_ENTITLEMENT` | `128` | Your purchased Virtual Processor Core (VPC) pool — used for compliance status |
| `IBM_RU_ENTITLEMENT` | `1000` | Your purchased Resource Unit (RU) pool — used for compliance status |

> Even these have safe defaults, but the namespace values must match your actual cluster deployment and the entitlement values must reflect your IBM contract for compliance calculations to be meaningful.

### Optional

| Variable | Default | Purpose |
|---|---|---|
| `OCP_TOKEN` | *(auto via `oc whoami -t`)* | OpenShift bearer token. Only needed when there is no active `oc` CLI session (e.g. CI pipelines, containers) |
| `OCP_URL` | *(hardcoded example URL)* | OpenShift API URL — displayed in the dashboard header for reference only |
| `COMPONENTS` | *(empty)* | Comma-separated installed component IDs (e.g. `watsonx_data,wkc`). Used to improve sizing inference in offline mode |
| `IKC_TYPE` | *(empty)* | IBM Knowledge Catalog edition hint: `standard` or `premium`. Affects entitlement display |
| `LOCAL_INSTALL_OPTIONS_FILE` | `config/install-options.watsonx-data-no-gpu.yml` | Path to an install-options YAML for offline sizing inspection |
| `IBM_SOFTWARE_HUB_VERSION` | *(long default string)* | Human-readable version label shown in the dashboard header |

---

## 📊 Architecture & Data Sources

```
┌─────────────────────────────────────────────────────────────────────────────┐
│              IBM Software Hub Telemetry & Sizing Dashboard                  │
│               (Carbon Design System v11 / IBM Plex UI)                      │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │
               ┌───────────────┴───────────────┐
               ▼                               ▼
┌──────────────────────────┐   ┌──────────────────────────────┐
│  IBM License Service API │   │  OpenShift PromQL / Thanos   │
│  (ns: ibm-licensing)     │   │  (Cluster Monitoring Stack)  │
├──────────────────────────┤   ├──────────────────────────────┤
│ /products                │   │ container_spec_cpu_quota     │
│ /bundled_products        │   │ container_memory_working_set │
│ /services                │   │ node_cpu_utilization_percent │
│ /snapshot (signed ZIP)   │   │ pod_phase_status & restarts  │
│ /health & /status        │   │                              │
└──────────────────────────┘   └──────────────────────────────┘
               │                               │
               └───────────────┬───────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    Live Custom Resource (CR) Discovery                      │
│                    (ns: cpd-instance / operands)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ watsonx.data (Wxd CRD, watsonxdata.ibm.com) — spec.scaleConfig             │
│ IBM Knowledge Catalog (WKC, wkc.wkc.cpd.ibm.com) — data quality status     │
│ DataStage Enterprise (ds.cpd.ibm.com) — standalone vs. WKC-bundled         │
│ Common Core Services (CCS, ccs.ccs.cpd.ibm.com) — shared platform base    │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│              Node-level compliance checks (oc get nodes)                    │
│  isc-entitlement node-pinning labels · namespace ResourceQuota/LimitRange   │
│  · raw worker CPU/memory capacity (used when no namespace quota exists)     │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📖 Licensing & Sizing Rules Reference

### watsonx.data Standard vs. Premium

- **Standard Edition** (default): "A single watsonx.data license is equal to 1 VPC" (IBM docs). Presto (Java/C++) and Spark engines convert to VPC at documented ratios; a flat 20 VPC "Core" floor applies regardless of engine count. Milvus is licensed here too, at its own VPC-denominated ratio — it is **not** Premium-exclusive.
- **Premium Edition**: Governed by IBM License Information Document `L-PCPF-BJV4WW` (Program 5900-BQE). Meters Milvus at a higher, RU-pool-denominated ratio instead of Standard's flat VPC ratio, and adds advanced governance/vector-search capabilities. Verify the current document ID against IBM's CSOL system before citing it — these get superseded by "Update" revisions without the change always propagating to IBM's own index pages.

### Common Core Services (CCS)

Foundational microservices shared across CPD cartridges (UI shell, user management, connections catalog). Incurs **0 VPC** when deployed purely as a prerequisite for licensed cartridges.

### DataStage Enterprise & WKC Data Quality Bundling

When DataStage Enterprise is automatically installed to execute Data Quality rules in IBM Knowledge Catalog, its usage is **restricted to data quality workloads**. Standalone ETL/ELT pipelines require a dedicated DataStage Enterprise Cartridge license.

---

## 🛠️ Running Tests

```bash
source .venv/bin/activate

# Standard library test runner
python3 -m unittest discover tests

# Or with pytest (requires: pip install -r requirements.txt)
pytest tests/
```

---

## 📁 Repository Structure

```
ibmas-softwarehub-metrics/
├── app/
│   ├── server.py                            # Complete server, HTML/CSS/JS & telemetry engine
│   └── vendor/                               # Vendored JS (no CDN/npm at runtime)
│       ├── cytoscape.min.js                 # Graph rendering
│       ├── elk.bundled.js                   # ELK layered-DAG layout engine
│       ├── cytoscape-elk.js                 # Cytoscape ↔ ELK adapter
│       └── cytoscape-expand-collapse.js     # Collapsible bundled-component groups
├── bin/
│   └── start-dashboard.sh                   # Launcher — auto-creates/activates venv, loads .env
├── config/
│   ├── cpd_vars.example.sh                  # OpenShift & CPD environment template
│   └── install-options.watsonx-data-no-gpu.yml  # Sizing profile definition
├── design-system/                            # UI/UX design-system reference (tokens, patterns)
├── tests/
│   └── test_server.py                       # Unit test suite
├── .env.example                             # Annotated environment variable template
├── .env                                     # Your local config (git-ignored)
├── requirements.txt                         # Optional: pytest for test suite
├── LICENSE                                  # Apache 2.0 License
└── README.md                                # This file
```

---

## 📄 License

This project is licensed under the [Apache License 2.0](LICENSE).
