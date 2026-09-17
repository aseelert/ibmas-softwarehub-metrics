import unittest
import os
import sys

# Add app directory to sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'app'))

from server import ClusterTelemetryCollector, IBM_LICENSE_TERMS_INFO, KNOWN_SERVICES_CATALOG

class TestTelemetryCollector(unittest.TestCase):
    def setUp(self):
        self.collector = ClusterTelemetryCollector()

    def test_license_terms_catalog(self):
        self.assertIn("release_matrix", IBM_LICENSE_TERMS_INFO)
        self.assertIn("watsonx_data_standard", IBM_LICENSE_TERMS_INFO)
        self.assertIn("watsonx_data_premium_reference", IBM_LICENSE_TERMS_INFO)
        self.assertIn("dependency_policy", IBM_LICENSE_TERMS_INFO)
        self.assertIn("tracking_reporting_policy", IBM_LICENSE_TERMS_INFO)

    def test_cluster_overview_structure(self):
        overview = self.collector.get_cluster_overview()
        self.assertIn("timestamp", overview)
        self.assertIn("cluster_info", overview)
        self.assertIn("terms_info", overview)
        self.assertIn("licensing", overview)
        self.assertIn("install_options", overview)
        self.assertIn("dependency_explorer", overview)
        self.assertIn("supported_services", overview)
        self.assertIn("services", overview)
        self.assertIn("totals", overview)

    def test_install_options_overview(self):
        opts = self.collector.get_install_options_overview()
        self.assertIn("sourceFile", opts)
        self.assertIn("entries", opts)
        self.assertIn("sources", opts)

    def test_service_telemetry_schema(self):
        data = self.collector.get_service_telemetry()
        self.assertIn("watsonx_data", data)
        self.assertIn("wkc", data)
        self.assertIn("datastage", data)
        self.assertIn("ccs", data)

    def test_custom_service_registration(self):
        custom_svc = {
            "name": "Custom Test Service",
            "installed": True,
            "profile": "medium",
            "nodes": 3,
            "cpu_limit": 12,
            "mem_limit_gb": 32,
            "vpc": 12.0
        }
        self.collector.custom_services["test-svc"] = custom_svc
        telemetry = self.collector.get_service_telemetry()
        self.assertIn("test-svc", telemetry)
        self.assertEqual(telemetry["test-svc"]["name"], "Custom Test Service")

class TestLicenseServiceParsing(unittest.TestCase):
    """Regression tests for two bugs found by live-cluster testing against a real IBM
    License Service 4.2.20 instance: (1) /products and /bundled_products return raw JSON
    arrays, not {"products": [...]}-wrapped objects, so live mode was silently returning zero
    rows despite a successful connection; (2) metricName is the full ILMT code name
    (VIRTUAL_PROCESSOR_CORE / RESOURCE_UNIT), not the short VPC/RU label the dashboard
    displays and totals against."""

    def setUp(self):
        self.collector = ClusterTelemetryCollector()

    def test_as_rows_accepts_raw_array(self):
        raw = [{"name": "IBM watsonx.data", "metricName": "VIRTUAL_PROCESSOR_CORE", "metricQuantity": 32}]
        self.assertEqual(self.collector._as_rows(raw, "products"), raw)

    def test_as_rows_accepts_wrapped_object(self):
        wrapped = {"products": [{"name": "IBM watsonx.data"}]}
        self.assertEqual(self.collector._as_rows(wrapped, "products"), wrapped["products"])

    def test_as_rows_empty_on_unrecognized_shape(self):
        self.assertEqual(self.collector._as_rows({"unexpected": []}, "products"), [])
        self.assertEqual(self.collector._as_rows(None, "products"), [])

    def test_normalize_metric_name_maps_ilmt_codes(self):
        self.assertEqual(self.collector.normalize_metric_name("VIRTUAL_PROCESSOR_CORE"), "VPC")
        self.assertEqual(self.collector.normalize_metric_name("RESOURCE_UNIT"), "RU")
        self.assertEqual(self.collector.normalize_metric_name("VPC"), "VPC")

    def test_aggregate_license_metrics_uses_normalized_names(self):
        products = [
            {"metricName": "VIRTUAL_PROCESSOR_CORE", "metricQuantity": 18},
            {"metricName": "VIRTUAL_PROCESSOR_CORE", "metricQuantity": 32},
            {"metricName": "RESOURCE_UNIT", "metricQuantity": 10},
        ]
        totals = self.collector.aggregate_license_metrics(products)
        self.assertEqual(totals.get("VPC"), 50.0)
        self.assertEqual(totals.get("RU"), 10.0)


class TestServiceCatalogCorrections(unittest.TestCase):
    """Spot-checks the KNOWN_SERVICES_CATALOG corrections against facts confirmed live on a
    running Software Hub 5.4 cluster (2026-09-17) — these were previously guessed and wrong."""

    def test_watsonx_data_crd_group_matches_live_cluster(self):
        self.assertEqual(KNOWN_SERVICES_CATALOG["watsonx_data"]["cr_group"], "watsonxdata.ibm.com")
        self.assertEqual(KNOWN_SERVICES_CATALOG["watsonx_data"]["cr_kind"], "Wxd")

    def test_datastage_crd_group_matches_live_cluster(self):
        self.assertEqual(KNOWN_SERVICES_CATALOG["datastage"]["cr_group"], "ds.cpd.ibm.com")

    def test_analyticsengine_crd_group_matches_live_cluster(self):
        self.assertEqual(KNOWN_SERVICES_CATALOG["analyticsengine"]["cr_group"], "ae.cpd.ibm.com")

    def test_opensearch_crd_matches_live_cluster(self):
        self.assertEqual(KNOWN_SERVICES_CATALOG["opencontent_opensearch"]["cr_group"], "opensearch.cloudpackopen.ibm.com")
        self.assertEqual(KNOWN_SERVICES_CATALOG["opencontent_opensearch"]["cr_kind"], "Cluster")

    def test_newly_tracked_services_present(self):
        self.assertIn("datastage_px", KNOWN_SERVICES_CATALOG)
        self.assertIn("datarefinery", KNOWN_SERVICES_CATALOG)


class TestSupportedServicesPodRegex(unittest.TestCase):
    """Regression test: Kubernetes pod names can never contain an underscore, so the
    fallback pod_regex generator must not turn hyphens into underscores (it previously did,
    which meant it could never match a real pod for any hyphenated component id)."""

    def setUp(self):
        self.collector = ClusterTelemetryCollector()

    def test_hyphenated_ids_get_hyphenated_fallback_regex(self):
        supported = self.collector.get_supported_services({})
        by_id = {item["id"]: item for item in supported}
        self.assertIn("ibm-streamsets-sdi", by_id)
        regex = by_id["ibm-streamsets-sdi"]["pod_regex"]
        self.assertNotIn("_", regex)
        self.assertIn("ibm-streamsets-sdi", regex)


if __name__ == '__main__':
    unittest.main()
