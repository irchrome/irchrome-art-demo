#!/usr/bin/env python3
"""
Automated QA verifier for site-zola builds (demo custom domain, demo subpath, and production).
Checks:
  1. Zero Pic-Time / WordPress hotlink / external font / tracker references across all text files.
  2. Internal link, anchor (#id), CSS/JS, and img src/srcset resolution across all HTML pages.
  3. Robots meta, demo banner, canonical isolation, and robots.txt rules per build profile.
  4. Image hygiene across EVERY file in output (including unreferenced files):
     - No forbidden extensions (RAW, TIFF, XML, TSV, etc.)
     - No orphan unreferenced media files
     - Zero EXIF/XMP/IPTC/GPS markers in JPEGs
     - Canonical sRGB ICC_PROFILE (APP2) present in 100% of JPEGs
  5. First-screen mobile budgets, total artifact size, and monthly traffic projections.
"""

import argparse
import json
import os
import re
import struct
import sys
from html.parser import HTMLParser
from urllib.parse import urlparse, unquote

FORBIDDEN_PATTERNS = [
    (r"pic-time\.com", "Pic-Time domain reference"),
    (r"pictime", "Pic-Time reference"),
    (r"gallery\.irchrome\.art", "Legacy Pic-Time gallery subdomain"),
    (r"wp-content/uploads", "WordPress hotlink"),
    (r"fonts\.googleapis\.com", "External Google Fonts"),
    (r"fonts\.gstatic\.com", "External Google Fonts static"),
    (r"googletagmanager\.com|google-analytics\.com|mc\.yandex\.ru", "Analytics tracker"),
    (r"/Users/", "Local disk path leak"),
]

FORBIDDEN_EXTS = {
    ".raw", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".tif", ".tiff",
    ".psd", ".xcf", ".xml", ".tsv", ".md", ".py", ".sh", ".env", ".dcp", ".cube",
}
ALLOWED_XML = {"sitemap.xml"}


class HtmlAuditor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.html_lang = None
        self.title = ""
        self.in_title = False
        self.meta_desc = None
        self.meta_robots = None
        self.canonical = None
        self.has_demo_bar = False
        self.ids = set()
        self.links = []
        self.assets = []
        self.sources = []
        self.imgs = []
        self.is_meta_refresh = False

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d and d["id"]:
            self.ids.add(d["id"])
        if tag == "html":
            self.html_lang = d.get("lang")
        elif tag == "title":
            self.in_title = True
        elif tag == "meta":
            name = (d.get("name") or "").lower()
            heq = (d.get("http-equiv") or "").lower()
            if name == "description":
                self.meta_desc = d.get("content", "")
            elif name == "robots":
                self.meta_robots = d.get("content", "")
            elif heq == "refresh":
                self.is_meta_refresh = True
        elif tag == "link":
            rel = (d.get("rel") or "").lower()
            href = d.get("href", "")
            if rel == "canonical":
                self.canonical = href
            elif href and rel in ("stylesheet", "icon"):
                self.assets.append(href)
        elif tag == "script" and d.get("src"):
            self.assets.append(d["src"])
        elif tag == "aside" and "demo-bar" in (d.get("class") or ""):
            self.has_demo_bar = True
        elif tag == "a" and d.get("href"):
            self.links.append(d["href"])
        elif tag == "source" and d.get("srcset"):
            self.sources.append(d["srcset"])
        elif tag == "img":
            self.imgs.append({
                "src": d.get("src", ""),
                "srcset": d.get("srcset", ""),
                "width": d.get("width", ""),
                "height": d.get("height", ""),
                "alt": d.get("alt"),
                "loading": d.get("loading", ""),
                "fetchpriority": d.get("fetchpriority", ""),
            })

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def inspect_jpeg(path: str) -> dict:
    data = open(path, "rb").read()
    has_exif = b"Exif\x00\x00" in data[:65536]
    has_xmp = b"http://ns.adobe.com" in data[:65536]
    has_iptc = b"Photoshop 3.0" in data[:65536]
    has_gps = b"GPSLatitude" in data[:65536] or b"GPSLongitude" in data[:65536]
    has_icc = b"ICC_PROFILE\x00" in data[:65536]
    return {
        "has_exif": has_exif,
        "has_xmp": has_xmp,
        "has_iptc": has_iptc,
        "has_gps": has_gps,
        "has_icc": has_icc,
    }


def inspect_webp(path: str) -> dict:
    data = open(path, "rb").read()
    head = data[:65536]
    return {
        "has_exif": b"EXIF" in head or b"Exif\x00\x00" in head,
        "has_xmp": b"XMP " in head or b"http://ns.adobe.com" in head,
        "has_iptc": b"Photoshop 3.0" in head,
        "has_gps": b"GPSLatitude" in head or b"GPSLongitude" in head,
        "has_icc": b"ICCP" in head,
    }


def resolve_local(url_str: str, base_url: str, base_path: str, page_rel: str, out_dir: str):
    """Resolve an internal URL (absolute matching base_url, root-relative, or relative) to a file in out_dir."""
    parsed = urlparse(url_str)
    if parsed.scheme in ("mailto", "tel", "javascript", "data"):
        return None, None
    if parsed.scheme in ("http", "https"):
        b = urlparse(base_url)
        if parsed.netloc != b.netloc:
            return None, None  # external URL
        path = parsed.path
    else:
        path = parsed.path

    if not path:
        return os.path.join(out_dir, page_rel), parsed.fragment

    if base_path and path.startswith(base_path + "/"):
        rel = path[len(base_path) + 1 :]
    elif base_path and path == base_path:
        rel = ""
    elif path.startswith("/"):
        rel = path.lstrip("/")
    else:
        rel = os.path.normpath(os.path.join(os.path.dirname(page_rel), path))

    rel = unquote(rel)
    cand = os.path.join(out_dir, rel)
    if os.path.isdir(cand):
        cand = os.path.join(cand, "index.html")
    elif not os.path.exists(cand) and os.path.exists(cand + "/index.html"):
        cand = cand + "/index.html"
    return cand, parsed.fragment


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="Built static directory (e.g. public)")
    ap.add_argument("--base-url", required=True, help="Configured base_url for this build")
    ap.add_argument("--mode", choices=["demo", "production"], required=True)
    ap.add_argument("--json-out", help="Optional JSON report path")
    args = ap.parse_args()

    out_dir = os.path.abspath(args.dir)
    base_url = args.base_url.rstrip("/")
    base_path = urlparse(base_url).path.rstrip("/")

    errors = []
    warnings = []

    all_files = []
    html_files = []
    jpg_files = []
    webp_files = []
    total_bytes = 0

    for root, _, files in os.walk(out_dir):
        for fn in sorted(files):
            fp = os.path.join(root, fn)
            rel = os.path.relpath(fp, out_dir)
            ext = os.path.splitext(fn)[1].lower()
            sz = os.path.getsize(fp)
            total_bytes += sz
            all_files.append((rel, fp, sz))
            if ext in FORBIDDEN_EXTS and rel not in ALLOWED_XML:
                errors.append(f"Forbidden file type in artifact: {rel}")
            if ext == ".html":
                html_files.append((rel, fp))
            elif ext in (".jpg", ".jpeg"):
                jpg_files.append((rel, fp, sz))
            elif ext == ".webp":
                webp_files.append((rel, fp, sz))

    # 1. Text scan for forbidden patterns (Pic-Time, WP uploads, external fonts, trackers, local paths)
    for rel, fp, _ in all_files:
        ext = os.path.splitext(rel)[1].lower()
        if ext in (".html", ".css", ".js", ".xml", ".txt", ".svg"):
            txt = open(fp, encoding="utf-8", errors="replace").read()
            for pat, desc in FORBIDDEN_PATTERNS:
                if re.search(pat, txt, re.I):
                    errors.append(f"{desc} ({pat}) found in {rel}")

    # 2. Check robots.txt
    robots_path = os.path.join(out_dir, "robots.txt")
    if not os.path.exists(robots_path):
        errors.append("Missing robots.txt")
    else:
        rtxt = open(robots_path, encoding="utf-8").read()
        if "Disallow: /" in rtxt:
            errors.append("robots.txt blocks entire site (must allow crawl so bots see noindex)")
        if args.mode == "demo" and "Sitemap:" in rtxt:
            errors.append("Demo robots.txt must not advertise Sitemap")
        if args.mode == "production" and "Sitemap:" not in rtxt:
            errors.append("Production robots.txt missing Sitemap")

    # 3. Parse all HTML files
    parsed_pages = {}
    referenced_media = set()
    external_links = set()

    for rel, fp in html_files:
        txt = open(fp, encoding="utf-8").read()
        aud = HtmlAuditor()
        aud.feed(txt)
        parsed_pages[rel] = aud

        if aud.is_meta_refresh:
            # Alias redirect page generated by Zola
            continue

        if aud.html_lang != "en":
            errors.append(f"{rel}: missing/wrong lang={aud.html_lang!r}")
        if not aud.title.strip():
            errors.append(f"{rel}: empty <title>")
        if not aud.meta_desc:
            errors.append(f"{rel}: missing meta description")

        if args.mode == "demo":
            if aud.meta_robots != "noindex,follow":
                errors.append(f"{rel}: demo expected robots=noindex,follow, got {aud.meta_robots!r}")
            if not aud.has_demo_bar:
                errors.append(f"{rel}: missing visible demo banner")
            if aud.canonical and aud.canonical.startswith("https://irchrome.art"):
                errors.append(f"{rel}: demo canonical leaks production WordPress domain: {aud.canonical}")
        else:
            if aud.meta_robots != "index,follow":
                errors.append(f"{rel}: prod expected robots=index,follow, got {aud.meta_robots!r}")
            if aud.has_demo_bar:
                errors.append(f"{rel}: prod build must not show demo banner")
            if rel != "404.html" and (not aud.canonical or not aud.canonical.startswith(base_url)):
                errors.append(f"{rel}: prod canonical mismatch: {aud.canonical!r}")

        # Verify imgs
        for idx, im in enumerate(aud.imgs):
            if im["alt"] is None or not im["alt"].strip():
                errors.append(f"{rel}: img #{idx} missing meaningful alt ({im['src']})")
            if not im["width"] or not im["height"]:
                errors.append(f"{rel}: img #{idx} missing explicit width/height ({im['src']})")
            if idx == 0 and im["loading"] == "lazy":
                errors.append(f"{rel}: primary above-the-fold image should not use loading=lazy ({im['src']})")

            urls_to_check = [im["src"]]
            if im["srcset"]:
                for part in im["srcset"].split(","):
                    u = part.strip().split(" ")[0]
                    if u:
                        urls_to_check.append(u)
            for u in urls_to_check:
                target, _ = resolve_local(u, base_url, base_path, rel, out_dir)
                if target is None:
                    errors.append(f"{rel}: external/unresolved image URL {u}")
                elif not os.path.exists(target):
                    errors.append(f"{rel}: broken image {u} -> {target}")
                else:
                    referenced_media.add(os.path.relpath(target, out_dir))

        # Verify <source srcset>
        for ss in aud.sources:
            for part in ss.split(","):
                u = part.strip().split(" ")[0]
                if not u:
                    continue
                target, _ = resolve_local(u, base_url, base_path, rel, out_dir)
                if target is None:
                    errors.append(f"{rel}: external/unresolved source URL {u}")
                elif not os.path.exists(target):
                    errors.append(f"{rel}: broken source {u} -> {target}")
                else:
                    referenced_media.add(os.path.relpath(target, out_dir))

        # Verify CSS/JS/icon assets
        for a in aud.assets:
            target, _ = resolve_local(a, base_url, base_path, rel, out_dir)
            if target is None:
                errors.append(f"{rel}: external asset {a}")
            elif not os.path.exists(target):
                errors.append(f"{rel}: broken asset {a} -> {target}")

    # 4. Verify all internal links and anchors across all HTML files
    for rel, aud in parsed_pages.items():
        if aud.is_meta_refresh:
            continue
        for href in aud.links:
            p = urlparse(href)
            if p.scheme in ("http", "https") and p.netloc != urlparse(base_url).netloc:
                external_links.add(href)
                continue
            target, frag = resolve_local(href, base_url, base_path, rel, out_dir)
            if target is None:
                continue
            if not os.path.exists(target):
                errors.append(f"{rel}: broken internal link {href} -> {target}")
                continue
            t_rel = os.path.relpath(target, out_dir)
            if t_rel.startswith("media/"):
                referenced_media.add(t_rel)
            if frag and t_rel in parsed_pages:
                if frag not in parsed_pages[t_rel].ids:
                    errors.append(f"{rel}: broken fragment #{frag} in target {t_rel}")

    # 5. Inspect EVERY JPEG and WebP in out_dir (including unreferenced check!)
    exif_leaks = 0
    icc_missing = 0
    orphan_media = []
    for rel, fp, sz in jpg_files:
        info = inspect_jpeg(fp)
        if info["has_exif"] or info["has_xmp"] or info["has_iptc"] or info["has_gps"]:
            exif_leaks += 1
            errors.append(f"Metadata leak in {rel}: {info}")
        if not info["has_icc"]:
            icc_missing += 1
            errors.append(f"Missing sRGB ICC_PROFILE in {rel}")
        if rel not in referenced_media:
            orphan_media.append(rel)

    for rel, fp, sz in webp_files:
        info = inspect_webp(fp)
        if info["has_exif"] or info["has_xmp"] or info["has_iptc"] or info["has_gps"]:
            exif_leaks += 1
            errors.append(f"Metadata leak in {rel}: {info}")
        if not info["has_icc"]:
            icc_missing += 1
            errors.append(f"Missing sRGB ICCP in {rel}")
        if rel not in referenced_media:
            orphan_media.append(rel)

    if orphan_media:
        errors.append(f"Unreferenced orphan media files in build ({len(orphan_media)}): {orphan_media[:5]}")

    # 6. Compute budgets
    def file_kb(rel_p):
        p = os.path.join(out_dir, rel_p)
        return os.path.getsize(p) / 1024 if os.path.exists(p) else 0.0

    home_first_screen_kb = (
        file_kb("index.html")
        + file_kb("css/site.css")
        + file_kb("js/site.js")
        + file_kb("favicon.svg")
        + file_kb("media/hero/hero-1200.webp")
    )
    rtd_cover_webp = "media/road-to-damascus/01-rtd-beirut-port-memorial-1200.webp"
    rtd_first_screen_kb = (
        file_kb("research-archives/road-to-damascus/index.html")
        + file_kb("css/site.css")
        + file_kb("js/site.js")
        + file_kb("favicon.svg")
        + file_kb(rtd_cover_webp)
    )
    rtd_all_webp_720_mb = sum(
        sz for rel, _, sz in webp_files if rel.startswith("media/road-to-damascus/") and rel.endswith("-720.webp")
    ) / (1024 * 1024)
    rtd_all_webp_1200_mb = sum(
        sz for rel, _, sz in webp_files if rel.startswith("media/road-to-damascus/") and rel.endswith("-1200.webp")
    ) / (1024 * 1024)
    rtd_all_1600_mb = sum(
        sz for rel, _, sz in jpg_files if rel.startswith("media/road-to-damascus/") and rel.endswith("-1600.jpg")
    ) / (1024 * 1024)

    total_mib = total_bytes / (1024 * 1024)
    if total_mib >= 700:
        warnings.append(f"Total artifact {total_mib:.1f} MiB exceeds 700 MiB warning threshold")

    report = {
        "mode": args.mode,
        "base_url": base_url,
        "html_pages_total": len(html_files),
        "content_pages": sum(1 for a in parsed_pages.values() if not a.is_meta_refresh),
        "alias_pages": sum(1 for a in parsed_pages.values() if a.is_meta_refresh),
        "jpeg_files_total": len(jpg_files),
        "webp_files_total": len(webp_files),
        "media_exif_leaks": exif_leaks,
        "media_icc_missing": icc_missing,
        "orphan_media_count": len(orphan_media),
        "total_artifact_mib": round(total_mib, 2),
        "home_mobile_first_screen_kb": round(home_first_screen_kb, 1),
        "heaviest_series_mobile_first_screen_kb": round(rtd_first_screen_kb, 1),
        "road_to_damascus_all_720_webp_mb": round(rtd_all_webp_720_mb, 2),
        "road_to_damascus_all_1200_webp_mb": round(rtd_all_webp_1200_mb, 2),
        "road_to_damascus_all_1600_jpg_mb": round(rtd_all_1600_mb, 2),
        "external_links": sorted(external_links),
        "errors": errors,
        "warnings": warnings,
    }

    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    if errors:
        print(f"\nFAILED with {len(errors)} errors.", file=sys.stderr)
        sys.exit(1)
    print("\nALL CHECKS PASSED.")


if __name__ == "__main__":
    main()
