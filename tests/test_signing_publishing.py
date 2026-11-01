import hashlib
import io
import json
import os
import pathlib
import stat
import tarfile
import tempfile
import urllib.parse
import unittest
import zipfile
from unittest import mock

from scripts import fdroid_git, github_publish, signing


APP_FINGERPRINT = "a" * 64
REPO_FINGERPRINT = "b" * 64
REPO_URL = "https://example.invalid/repo/"


def zip_bytes(name, content):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, content)
    return output.getvalue()


class SigningBackupTests(unittest.TestCase):
    def test_init_refuses_partial_key_state_without_generating_replacements(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = pathlib.Path(temporary)
            (home / "app-release.p12").write_bytes(b"existing key")
            with mock.patch.object(signing, "signing_home", return_value=home):
                with self.assertRaises(SystemExit):
                    signing.create_keys()
            self.assertEqual(["app-release.p12"], sorted(path.name for path in home.iterdir()))

    def test_backup_archive_contains_only_four_mode_0600_verified_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            backup = root / "backup"
            backup.mkdir(mode=0o700)
            for key_name, password_name, _alias in signing.KEYS.values():
                for name in (key_name, password_name):
                    path = backup / name
                    path.write_bytes(("fixture:" + name).encode("ascii"))
                    os.chmod(path, 0o600)
            target = root / "download" / "signing-backup.tar.gz"
            with mock.patch.object(signing, "backup_archive", return_value=target):
                created = signing.create_verified_archive(backup)
            self.assertEqual(target, created)
            self.assertEqual(0o600, stat.S_IMODE(target.stat().st_mode))
            with tarfile.open(target, "r:gz") as archive:
                members = archive.getmembers()
                self.assertEqual(
                    {name for names in signing.KEYS.values() for name in names[:2]},
                    {member.name for member in members},
                )
                self.assertTrue(all(member.isfile() and stat.S_IMODE(member.mode) == 0o600
                                    for member in members))

    def test_publish_readme_gets_verified_public_repository_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            clone = pathlib.Path(temporary)
            readme = clone / "README.md"
            readme.write_text(
                "# F-Droid repository\n\n"
                "The HTTPS repository URL and signing fingerprint will be published with the first release.\n",
                encoding="utf-8",
            )
            fingerprints = f"app: {'a' * 64}\nrepo: {REPO_FINGERPRINT}\n"
            with mock.patch.object(fdroid_git, "CLONE", clone), \
                    mock.patch.object(fdroid_git.subprocess, "run",
                                      return_value=mock.Mock(stdout=fingerprints)):
                fdroid_git.update_public_details()
            updated = readme.read_text(encoding="utf-8")
            self.assertIn(fdroid_git.PUBLIC_REPO_URL, updated)
            self.assertIn(REPO_FINGERPRINT, updated)
            self.assertNotIn("will be published with the first release", updated)


class PublishedRepositoryVerificationTests(unittest.TestCase):
    def fixture(self, apk_bytes):
        apk_name = "com.hulloanson_100.apk"
        index_v2 = json.dumps({
            "packages": {
                "com.hulloanson": {
                    "versions": {
                        "100": {
                            "file": {
                                "name": apk_name,
                                "size": len(apk_bytes),
                                "sha256": hashlib.sha256(apk_bytes).hexdigest(),
                            },
                            "manifest": {
                                "versionCode": 100,
                                "signer": {"sha256": [APP_FINGERPRINT]},
                            },
                        }
                    }
                }
            }
        }, sort_keys=True).encode("utf-8")
        entry = json.dumps({
            "index": {
                "name": "index-v2.json",
                "size": len(index_v2),
                "sha256": hashlib.sha256(index_v2).hexdigest(),
            }
        }, sort_keys=True).encode("utf-8")
        return {
            "index-v1.jar": zip_bytes("index-v1.json", b'{"packages":{"com.hulloanson":{}}}'),
            "entry.jar": zip_bytes("entry.json", entry),
            "entry.json": entry,
            "index-v2.json": index_v2,
            apk_name: apk_bytes,
        }

    def run_verifier(self, files, expected_apk_sha256=None):
        with tempfile.TemporaryDirectory() as temporary:
            target = pathlib.Path(temporary)

            def fake_fetch(url, destination, *, cache_bust=False):
                name = pathlib.PurePosixPath(urllib.parse.urlsplit(url).path).name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(files[name])
                return destination

            with mock.patch.object(github_publish, "fetch_https", side_effect=fake_fetch), \
                    mock.patch.object(github_publish, "verify_jar"), \
                    mock.patch.object(github_publish.shutil, "which", return_value="/usr/bin/apksigner"), \
                    mock.patch.object(github_publish.subprocess, "run",
                                      return_value=mock.Mock(stdout=(
                                          f"Signer #1 certificate SHA-256 digest: {APP_FINGERPRINT}\n"
                                      ))):
                github_publish.verify_repository_inner(
                    REPO_URL, REPO_FINGERPRINT, APP_FINGERPRINT,
                    expected_apk_sha256, target,
                )

    def test_v2_signed_entry_hashes_and_apk_signer_are_checked(self):
        apk = b"signed APK fixture"
        files = self.fixture(apk)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.run_verifier(files, hashlib.sha256(apk).hexdigest())

    def test_v2_apk_hash_mismatch_is_rejected(self):
        apk = b"signed APK fixture"
        files = self.fixture(apk)
        files["com.hulloanson_100.apk"] = b"different hosted APK bytes"
        with self.assertRaises(SystemExit):
            self.run_verifier(files)

    def test_v2_index_size_and_digest_are_bound_by_signed_entry(self):
        files = self.fixture(b"signed APK fixture")
        files["index-v2.json"] += b" "
        with self.assertRaises(SystemExit):
            self.run_verifier(files)


if __name__ == "__main__":
    unittest.main()
