"""Regression checks for per-release dependency preservation."""
import unittest
from mkrepo import effective_depends


class Dependencies(unittest.TestCase):
    def setUp(self):
        self.repo = {"depends_by_arch": {"iphoneos-arm": "firmware (>= 6.0), firmware (<< 7.0)"}}
        self.meta = {"depends_from_package": ["iphoneos-arm"]}

    def test_new_and_old_releases_keep_their_own_limits(self):
        for own in ("firmware (>= 5.0), firmware (<< 11.0)",
                    "firmware (>= 6.0), firmware (<< 7.0)",
                    "mobilesubstrate, firmware (>= 6.0), firmware (<< 7.0)"):
            self.assertEqual(effective_depends(self.meta, self.repo, "iphoneos-arm", own), own)

    def test_other_packages_and_architectures_keep_existing_behavior(self):
        self.assertEqual(effective_depends({}, self.repo, "iphoneos-arm", "original"),
                         self.repo["depends_by_arch"]["iphoneos-arm"])
        self.assertEqual(effective_depends(self.meta, self.repo, "iphoneos-arm64", "firmware (>= 15.0)"),
                         "firmware (>= 15.0)")

    def test_explicit_preservation_overrides_old_metadata(self):
        meta = dict(self.meta, depends="firmware (>= 6.0)")
        self.assertEqual(effective_depends(meta, self.repo, "iphoneos-arm", "firmware (>= 5.0)"),
                         "firmware (>= 5.0)")

    def test_missing_or_invalid_configuration_fails_closed(self):
        with self.assertRaises(ValueError):
            effective_depends(self.meta, self.repo, "iphoneos-arm", "")
        with self.assertRaises(ValueError):
            effective_depends({"depends_from_package": "iphoneos-arm"}, self.repo, "iphoneos-arm", "original")


if __name__ == "__main__":
    unittest.main()
