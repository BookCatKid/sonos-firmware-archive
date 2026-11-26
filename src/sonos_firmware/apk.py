"""Shared static APK signature and manifest inspection for every Android source."""
from pathlib import Path
import hashlib
import re
import subprocess
from xml.etree import ElementTree
from .android_store import StoreError, store_apps

ROOT = Path(__file__).resolve().parents[2]
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def verify_apk(path: Path, expected: str | None = None) -> dict:
    output = subprocess.check_output(
        [str(ROOT / "tools/android/verify-apk"), str(path)], text=True, timeout=120
    )
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    signer = fields.get("signer.1.sha256")
    if (fields.get("verified") != "true" or not re.fullmatch(r"[0-9a-f]{64}", signer or "")
            or (expected is not None and signer != expected)):
        raise StoreError('APKSignatureVerificationFailed')
    signers = [{'sha256': value, 'subject': fields.get(key.removesuffix('sha256')+'subject')}
               for key, value in sorted(fields.items()) if re.fullmatch(r'signer\.[1-9][0-9]*\.sha256', key)]
    if any(not re.fullmatch(r'[0-9a-f]{64}', row['sha256']) for row in signers):
        raise StoreError('APKSignatureVerificationFailed')
    return {
        "verified": True,
        "signer_sha256": signer,
        "signer_subject": fields.get("signer.1.subject"),
        "signers": signers,
        "schemes": [name for name in ("v1", "v2", "v3", "v4")
                    if fields.get(name) == "true"],
    }


def apk_metadata(path: Path, scratch: Path) -> dict:
    decoded = scratch / (path.name + "-decoded")
    subprocess.run([
        "java", "-jar", str(ROOT / "tools/vendor/apktool-3.0.3.jar"),
        "d", "-f", "-s", str(path), "-o", str(decoded),
    ], check=True, stdout=subprocess.DEVNULL, timeout=180)
    root = ElementTree.parse(decoded / "AndroidManifest.xml").getroot()
    apktool_yml = (decoded / "apktool.yml").read_text()
    yml_code = re.search(r"^\s*versionCode:\s*['\"]?([^'\"\n]+)", apktool_yml, re.MULTILINE)
    yml_name = re.search(r"^\s*versionName:\s*['\"]?([^'\"\n]+)", apktool_yml, re.MULTILINE)
    return {
        "package": root.attrib["package"],
        "version_code": root.attrib.get(ANDROID_NS + "versionCode") or
                        (yml_code.group(1).strip() if yml_code else None),
        "version_name": root.attrib.get(ANDROID_NS + "versionName") or
                        (yml_name.group(1).strip() if yml_name else None),
        "split": root.attrib.get('split'),
    }


def inspection_revision():
    digest = hashlib.sha256()
    for path in (Path(__file__), ROOT/'tools/android/PrintApkSigners.java',
                 ROOT/'tools/android/verify-apk', ROOT/'tools/vendor/SHA256SUMS', ROOT/'config/android-apps.json'):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def inspect_official(candidate, path):
    """Verify APK signatures and record actual identity independently of URL labels.

    Store signer pins are comparison evidence for official Fire OS deliveries;
    their distribution certificates need not equal Google Play certificates.
    Explicit package/version expectations, when advertised, must match.
    """
    import tempfile
    verification = verify_apk(path)
    with tempfile.TemporaryDirectory(prefix='apk-manifest-', dir=path.parent) as temporary:
        metadata = apk_metadata(path, Path(temporary))
    if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+', metadata.get('package') or '')
            or not str(metadata.get('version_code', '')).isdecimal() or int(metadata['version_code']) <= 0):
        raise StoreError('APKMetadataInvalid')
    expected_package = candidate.get('expected_package') or candidate.get('package')
    expected_code = candidate.get('expected_version_code', candidate.get('version_code'))
    expected_name = candidate.get('expected_version_name')
    if expected_package and expected_package != metadata['package']:
        raise StoreError('APKPackageMismatch')
    if expected_code is not None and str(expected_code) != str(metadata['version_code']):
        raise StoreError('APKVersionMismatch')
    if expected_name is not None and expected_name != metadata.get('version_name'):
        raise StoreError('APKVersionMismatch')
    configured = store_apps(ROOT).get(metadata['package'], {}).get('signer_sha256')
    return {**verification, **metadata, 'advertised_version': candidate.get('version'),
            'matches_store_signer': (verification['signer_sha256'] == configured if configured else None)}


