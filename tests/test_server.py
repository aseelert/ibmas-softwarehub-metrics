import unittest
import os
import sys

# Add app directory to sys.path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, 'app'))

from server import ClusterTelemetryCollector, IBM_LICENSE_TERMS_INFO

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

if __name__ == '__main__':
    unittest.main()
