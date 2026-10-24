"""Refresh the pinned offline Swagger UI files from the official npm registry.

Run explicitly from the project root. Normal startup never accesses npm/CDNs.
The package tarball is verified against the pinned npm SHA-512 integrity value;
only the runtime assets and license notices are read, without extracting archive paths.
"""
import base64
import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request

VERSION = "5.34.0"
URL = f"https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-{VERSION}.tgz"
INTEGRITY = "sha512-H6tAIjr3XqE31PbkZl0OFya1ziRLDfTwh3J6OHdumPtPCLfjTnyLeagl/55kFNZcTOz9a9QI9WP0FXldwiGJag=="
FILES = ("swagger-ui-bundle.js", "swagger-ui.css", "LICENSE", "swagger-ui-bundle.js.LICENSE.txt")
DESTINATION = Path(__file__).resolve().parents[1] / "backend" / "static" / "swagger"


def main():
    with urllib.request.urlopen(URL, timeout=30) as response:
        package = response.read(16 * 1024 * 1024 + 1)
    if len(package) > 16 * 1024 * 1024:
        raise RuntimeError("Swagger package exceeds the download limit.")
    actual = "sha512-" + base64.b64encode(hashlib.sha512(package).digest()).decode("ascii")
    if actual != INTEGRITY:
        raise RuntimeError("Swagger package SHA-512 integrity check failed.")
    contents = {}
    with tarfile.open(fileobj=io.BytesIO(package), mode="r:gz") as archive:
        for filename in FILES:
            member = archive.getmember("package/" + filename)
            if not member.isfile() or member.size > 8 * 1024 * 1024:
                raise RuntimeError("Invalid Swagger package member.")
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("Missing Swagger file.")
            contents[filename] = source.read()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for filename, data in contents.items():
        (DESTINATION / filename).write_bytes(data)
    notes = ["# Vendored Swagger UI", "", f"Package: swagger-ui-dist {VERSION}",
             "Publisher/source: Swagger API / SmartBear", "Upstream: https://github.com/swagger-api/swagger-ui",
             f"Registry metadata: https://registry.npmjs.org/swagger-ui-dist/{VERSION}",
             f"Tarball: {URL}", f"Integrity: {INTEGRITY}",
             "License: Apache-2.0; upstream LICENSE and bundled third-party license notices are included unchanged.", "",
             "Only swagger-ui-bundle.js, swagger-ui.css, LICENSE and swagger-ui-bundle.js.LICENSE.txt were copied.",
             "Normal app startup and documentation never fetch npm or CDN resources.",
             "Swagger's external schema validator is disabled; API requests stay same-origin.",
             "Reproduce with python scripts/vendor_swagger.py. Update VERSION/INTEGRITY deliberately.", ""]
    (DESTINATION / "SOURCE.md").write_text("\n".join(notes), encoding="utf-8")
    print(f"Vendored swagger-ui-dist {VERSION}: verified SHA-512; copied assets and license notices.")


if __name__ == "__main__":
    main()
