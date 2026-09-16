#!/usr/bin/env bash
# IBM Software Hub 5.3.1 example for the watson OpenShift cluster.
# Install and automatically source this file on the bastion after replacing secrets:
#   cp config/cpd_vars.example.sh ~/.config/ibm-software-hub/cpd_vars.sh
#   chmod 600 ~/.config/ibm-software-hub/cpd_vars.sh
#   grep -qxF 'source "$HOME/.config/ibm-software-hub/cpd_vars.sh"' ~/.bashrc || echo 'source "$HOME/.config/ibm-software-hub/cpd_vars.sh"' >> ~/.bashrc
#   source ~/.config/ibm-software-hub/cpd_vars.sh

# Client workstation
export PATH="$HOME/.local/bin:$PATH"

# ── Mirror registry quick reference ─────────────────────────────────────────────
# Both registries on NFS VM (10.10.0.40), plain HTTP (no certs), rootless podman:
#   ocp-mirror-registry  :5001  /mnt/data/ocp-mirror  — OCP 4.19 release images
#   cpd-mirror-registry  :5002  /mnt/data/cpd-mirror  — IBM CPD / Software Hub
#
# Mirror CPD images (run from Mac, 8+ hours):
#   ./mirror-cpd.sh --remote          # runs in tmux on bastion
#   ./mirror-cpd.sh --apply-icsp      # apply IDMS + set IMAGE_PULL_PREFIX
#
# Verify:
#   curl http://10.10.0.40:5001/v2/_catalog    # OCP images
#   curl http://10.10.0.40:5002/v2/_catalog    # CPD images

export CPD_CLI_MANAGE_WORKSPACE="$HOME/cpd-cli-workspace"
export KUBECONFIG="$HOME/.kube/config"

# OpenShift cluster
export OCP_URL="https://api.watson.ibmas-zocp-techcluster.org:6443"
export OPENSHIFT_TYPE="self-managed"
export IMAGE_ARCH="amd64"
export OCP_USERNAME="kubeadmin"
export OCP_PASSWORD="<set-kubeadmin-password>"
export SERVER_ARGUMENTS="--server=${OCP_URL}"
export LOGIN_ARGUMENTS="--username=${OCP_USERNAME} --password=${OCP_PASSWORD}"
export CPDM_OC_LOGIN="cpd-cli manage login-to-ocp ${SERVER_ARGUMENTS} ${LOGIN_ARGUMENTS}"
export OC_LOGIN="oc login ${SERVER_ARGUMENTS} ${LOGIN_ARGUMENTS}"

# IBM Software Hub projects
export PROJECT_LICENSE_SERVICE="ibm-licensing"
export PROJECT_SCHEDULING_SERVICE="ibm-cpd-scheduler"
export PROJECT_CPD_INST_OPERATORS="cpd-operators"
export PROJECT_CPD_INST_OPERANDS="cpd-instance"

# NFS dynamic provisioning
# Existing NFS VM: ibmas-zocp-nfs, 10.10.0.40
# /mnt/data/wxd        — CPD workload PVCs (dynamic provisioner)
# /mnt/data/cpd-mirror — CPD image mirror (cpd-mirror-registry port 5002 on NFS VM 10.10.0.40)
export NFS_SERVER_LOCATION="10.10.0.40"
export NFS_PATH="/mnt/data/wxd"
export PROJECT_NFS_PROVISIONER="nfs-provisioner"
export NFS_STORAGE_CLASS="managed-nfs-storage"

export NFS_IMAGE="registry.k8s.io/sig-storage/nfs-subdir-external-provisioner:v4.0.2"

# IBM documents managed-nfs-storage for both RWO and RWX when using NFS.
export STG_CLASS_BLOCK="${NFS_STORAGE_CLASS}"
export STG_CLASS_FILE="${NFS_STORAGE_CLASS}"
export RUN_STORAGE_TESTS="false"

# IBM Entitled Registry
export IBM_ENTITLEMENT_KEY="<set-ibm-entitlement-key>"

# ── Mirror registries — both run on the NFS VM (10.10.0.40) via systemd Quadlets ──
#
# OCP internal registry cannot be used: it only supports 2-level image paths
# (<project>/<name>) but IBM's mirror tool generates 3-level paths (cpopen/cpfs/<img>).
#
# Two registry:2 containers on NFS VM — direct disk access, no NFS-over-NFS:
#   cpd-mirror-registry  port 5002  /mnt/data/cpd-mirror  (CPD/Software Hub images)
#   ocp-mirror-registry  port 5001  /mnt/data/ocp-mirror  (OpenShift mirror images)
# Managed by /etc/containers/systemd/*.container (Quadlets) on the NFS VM.
#
# OCP insecure registry config (applied once):
#   oc patch image.config.openshift.io/cluster --type=merge \
#     --patch='{"spec":{"registrySources":{"insecureRegistries":["10.10.0.40:5002","10.10.0.40:5001"]}}}'

# OCP mirror registry scheme — both registries run plain HTTP (no TLS).
# manage.sh mirror reads this to build the registry URL correctly.
export MIRROR_REGISTRY_SCHEME=http

# CPD mirror registry (used by mirror-cpd.sh)
export PRIVATE_REGISTRY_LOCATION="10.10.0.40:5002"
export REGISTRY_HOST="10.10.0.40"
export REGISTRY_PORT="5002"
export REGISTRY_DATA_DIR="/mnt/data/cpd-mirror"
export REGISTRY_CONTAINER_NAME="cpd-mirror-registry"
# No auth for unauthenticated registry:2 — leave PUSH_USER/PASSWORD unset.

# Image pull configuration.
# Online mode (default): pull directly from IBM Entitled Registry.
# Airgap mode: IMAGE_PULL_PREFIX is set to ${PRIVATE_REGISTRY_LOCATION} automatically
# by mirror-cpd.sh --apply-icsp or manage.sh image-config after mirroring completes.
# To set manually:
#   sed -i 's|^export IMAGE_PULL_PREFIX=.*|export IMAGE_PULL_PREFIX=10.10.0.40:5002|' \
#     ~/.config/ibm-software-hub/cpd_vars.sh
export IMAGE_PULL_SECRET="ibm-entitlement-key"
export IMAGE_PULL_CREDENTIALS="$(printf '%s' "cp:${IBM_ENTITLEMENT_KEY}" | base64 | tr -d '\n')"
export IMAGE_PULL_PREFIX="icr.io"

# IBM Software Hub release
# Patch history (olm-utils-v4 tags verified against icr.io/cpopen/cpd/olm-utils-v4):
#   patch 12  — deployed 2026-09-09
#   patch 13  — CURRENT TARGET (image tag 5.3.1.13 + CASE published in OCI 2026-09-10)
#   patch 14  — image tag exists in icr.io but CASE NOT YET published to cp.icr.io/cpopen OCI
#               upgrade to 14 once IBM publishes the CASE: re-run apply-patch13.sh with PATCH_ID=14
#
# OCI CASE registry (used by cpd-cli case-download --from_oci=true):
#   oci:cp.icr.io/cpopen  — IBM's authoritative patch CASE source (no GitHub needed)
#
# To apply a new patch after IBM publishes the CASE:
#   1. Update PATCH_ID below and on the bastion cpd_vars.sh
#   2. On bastion: bash ~/apply-patch13.sh   (script auto-downloads CASE via OCI)
#   3. Verify: eval "$CPD_VALIDATE_SERVICES"
export VERSION="5.3.1"
export PATCH_ID="13"
export OLM_UTILS_IMAGE="icr.io/cpopen/cpd/olm-utils-v4:${VERSION}.${PATCH_ID}"

# Install watsonx.data as the only data service, without GPU-dependent features.
# IBM Software Hub has no DISABLE_GPU variable. A CPU-only installation is
# achieved by not installing GPU operators or GPU-only services/options.
export WATSONX_DATA_HARDWARE_PROFILE="cpu-only"
# cpd-cli runs install-components inside the olm-utils container. The host
# workspace/work directory is mounted there as /tmp/work.
export WATSONX_DATA_INSTALL_OPTIONS="/tmp/work/install-options.yml"

# Do not constrain image mirroring to an image group. The watsonx_data
# component selection resolves the required image groups for Version 5.3.1.
unset IMAGE_GROUPS

# Complete requested Software Hub stack. COMPONENTS contains component IDs.
# PROJECT_* variables contain Kubernetes namespace names and are never passed
# to --components. To add services, append their component IDs here (cpd-cli
# auto-expands dependencies). Full ID catalog + an install-options.yml example:
# docs/SOFTWARE_HUB_5.3_INSTALL.md (sections 7a/7b).
export COMPONENTS="ibm-licensing,scheduler,cpfs,cpd_platform,watsonx_data,wkc,datastage_ent"
export RESOLVED_STACK_COMPONENTS="ibm-licensing,scheduler,cpfs,ibm_usage_metering,cpd_platform,analyticsengine,ccs,opencontent_opensearch,watsonx_data,wkc,datastage_ent,datarefinery,zen"

# Useful commands after sourcing this file
export CPD_SETUP_NFS="cpd-cli manage setup-nfs-provisioner --nfs_server=${NFS_SERVER_LOCATION} --nfs_path=${NFS_PATH} --nfs_provisioner_ns=${PROJECT_NFS_PROVISIONER} --nfs_storageclass_name=${NFS_STORAGE_CLASS} --nfs_provisioner_image=${NFS_IMAGE}"
export CPD_PATCH_NFS_STORAGE_CLASS="oc patch storageclass ${NFS_STORAGE_CLASS} --type=json --patch='[{\"op\":\"replace\",\"path\":\"/mountOptions\",\"value\":[\"nfsvers=3\",\"nolock\"]}]'"
export CPD_PREPARE_SCHEDULER_RESOURCES="cd ${CPD_CLI_MANAGE_WORKSPACE}/work && cpd-cli manage case-download --components=scheduler --release=${VERSION} --patch_id=${PATCH_ID} --scheduler_ns=${PROJECT_SCHEDULING_SERVICE} --cluster_resources=true --from_oci=true && oc apply -f cluster_scoped_resources.yaml --server-side --force-conflicts && mv -f cluster_scoped_resources.yaml ${VERSION}-${PROJECT_SCHEDULING_SERVICE}-cluster_scoped_resources.yaml"
export CPD_PREPARE_INSTANCE_RESOURCES="cd ${CPD_CLI_MANAGE_WORKSPACE}/work && cpd-cli manage case-download --components=${COMPONENTS} --release=${VERSION} --patch_id=${PATCH_ID} --operator_ns=${PROJECT_CPD_INST_OPERATORS} --cluster_resources=true --from_oci=true && oc apply -f cluster_scoped_resources.yaml --server-side --force-conflicts && mv -f cluster_scoped_resources.yaml ${VERSION}-${PROJECT_CPD_INST_OPERATORS}-cluster_scoped_resources.yaml"
export CPD_INSTALL_LICENSE_SERVICE="cpd-cli manage apply-cluster-components --release=${VERSION} --license_acceptance=true --cert_manager_enabled=true --licensing_enabled=true --licensing_ns=${PROJECT_LICENSE_SERVICE} --from_oci=true"
export CPD_INSTALL_SCHEDULER="cpd-cli manage apply-scheduler --release=${VERSION} --license_acceptance=true --scheduler_ns=${PROJECT_SCHEDULING_SERVICE} --from_oci=true"
export CPD_AUTHORIZE_INSTANCE="cpd-cli manage authorize-instance-topology --cpd_operator_ns=${PROJECT_CPD_INST_OPERATORS} --cpd_instance_ns=${PROJECT_CPD_INST_OPERANDS}"
export CPD_INSTALL_PLATFORM="cpd-cli manage install-components --license_acceptance=true --components=cpd_platform --release=${VERSION} --patch_id=${PATCH_ID} --operator_ns=${PROJECT_CPD_INST_OPERATORS} --instance_ns=${PROJECT_CPD_INST_OPERANDS} --block_storage_class=${STG_CLASS_BLOCK} --file_storage_class=${STG_CLASS_FILE} --image_pull_prefix=${IMAGE_PULL_PREFIX} --image_pull_secret=${IMAGE_PULL_SECRET} --run_storage_tests=${RUN_STORAGE_TESTS}"
# Service type selectors — change these to switch editions without editing commands.
# IKC_TYPE: wkc | ikc_premium | ikc_standard
export IKC_TYPE="wkc"
# DATASTAGE_TYPE: datastage_ent | datastage_ent_plus
export DATASTAGE_TYPE="datastage_ent"

# Per-service install commands (each service is a separate install-components call).
export CPD_INSTALL_WKC="cpd-cli manage install-components --license_acceptance=true --components=${IKC_TYPE} --release=${VERSION} --patch_id=${PATCH_ID} --operator_ns=${PROJECT_CPD_INST_OPERATORS} --instance_ns=${PROJECT_CPD_INST_OPERANDS} --block_storage_class=${STG_CLASS_BLOCK} --file_storage_class=${STG_CLASS_FILE} --image_pull_prefix=${IMAGE_PULL_PREFIX} --image_pull_secret=${IMAGE_PULL_SECRET} --param-file=${WATSONX_DATA_INSTALL_OPTIONS}"
export CPD_INSTALL_DATASTAGE="cpd-cli manage install-components --license_acceptance=true --components=${DATASTAGE_TYPE} --release=${VERSION} --patch_id=${PATCH_ID} --operator_ns=${PROJECT_CPD_INST_OPERATORS} --instance_ns=${PROJECT_CPD_INST_OPERANDS} --block_storage_class=${STG_CLASS_BLOCK} --file_storage_class=${STG_CLASS_FILE} --image_pull_prefix=${IMAGE_PULL_PREFIX} --image_pull_secret=${IMAGE_PULL_SECRET}"
export CPD_INSTALL_WATSONX_DATA="cpd-cli manage install-components --license_acceptance=true --components=watsonx_data --release=${VERSION} --patch_id=${PATCH_ID} --operator_ns=${PROJECT_CPD_INST_OPERATORS} --instance_ns=${PROJECT_CPD_INST_OPERANDS} --block_storage_class=${STG_CLASS_BLOCK} --file_storage_class=${STG_CLASS_FILE} --image_pull_prefix=${IMAGE_PULL_PREFIX} --image_pull_secret=${IMAGE_PULL_SECRET} --param-file=${WATSONX_DATA_INSTALL_OPTIONS} --run_storage_tests=${RUN_STORAGE_TESTS}"
export CPD_VALIDATE_SERVICES="cpd-cli manage get-cr-status --cpd_instance_ns=${PROJECT_CPD_INST_OPERANDS} --components=${COMPONENTS} --include_dependency=true"
export CPD_GET_LICENSE="cpd-cli manage get-license --release=${VERSION}"
export CPD_SETUP_INSTANCE_PREVIEW="cpd-cli manage setup-instance --release=${VERSION} --license_acceptance=true --cpd_operator_ns=${PROJECT_CPD_INST_OPERATORS} --cpd_instance_ns=${PROJECT_CPD_INST_OPERANDS} --block_storage_class=${STG_CLASS_BLOCK} --file_storage_class=${STG_CLASS_FILE} --run_storage_tests=${RUN_STORAGE_TESTS} --preview=true"
export CPD_GET_SCHEDULER_STATUS="cpd-cli manage get-cr-status --cluster_component_ns=${PROJECT_SCHEDULING_SERVICE} --components=scheduler"
export CPD_GET_CR_STATUS="cpd-cli manage get-cr-status --cpd_instance_ns=${PROJECT_CPD_INST_OPERANDS} --components=${COMPONENTS} --include_dependency=true"
export CPD_HEALTH_OPERATORS="cpd-cli health operators --operator_ns=${PROJECT_CPD_INST_OPERATORS} --control_plane_ns=${PROJECT_CPD_INST_OPERANDS}"
export CPD_HEALTH_OPERANDS="cpd-cli health operands --control_plane_ns=${PROJECT_CPD_INST_OPERANDS}"
