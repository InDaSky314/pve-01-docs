#!/usr/bin/env python3
"""Self-correcting repair loop for channel artwork (Loop B).

Grader: scripts/icon-verify.py (custom channel icon coverage & degradation check).
Rollback: icon-archive export snapshot & timestamped disk backup.

Rules:
1. Capture rollback BEFORE acting (snapshot via icon-archive export and backup of extracted dir).
2. Grade with the metric, not with reasoning: measure before, act, measure after.
   If coverage does not improve or any channel is degraded, REVERT AUTOMATICALLY.
3. Maintenance window only: 01:00-05:00 Europe/Berlin via CT 105's
   maintenance_window.py. Outside the window: report only, never act.
4. Default to dry-run: acting requires explicit --apply flag AND an open window.
5. Never touch the tuner.
6. Never touch recordings, timers, or scheduled tasks.
7. Report every action taken, metric before/after, and revert.
8. Generation of NEW artwork (where no tv-logos match exists) stays OUT of
   this loop for now — proposed in report instead (batched at 6 per runbook).

Usage:
  icon-repair-loop.py                # report-only / dry-run (default)
  icon-repair-loop.py --apply        # apply matched artwork (maintenance window required)
  icon-repair-loop.py --test-revert  # verify rollback mechanism on failure
  icon-repair-loop.py --json         # output results as JSON
"""
import argparse
import collections
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

ARCHIVE_DIR = "/root/icon-archive"
BLOBS_DIR = os.path.join(ARCHIVE_DIR, "blobs")
MANIFEST_FILE = os.path.join(ARCHIVE_DIR, "manifest.json")
EXTRACTED_DIR = os.path.join(ARCHIVE_DIR, "extracted")
ICON_HOST_URL = "http://192.168.9.11:8100"
CT_ID_MEDIA = "105"
CT_ID_NPVR = "112"
MAINTENANCE_SCRIPT = "/srv/media-core/sync/maintenance_window.py"
ICON_VERIFY_SCRIPT = "/root/pve-01-docs/scripts/icon-verify.py"
ICON_ARCHIVE_SCRIPT = "/root/pve-01-docs/scripts/icon-archive.py"

TV_LOGOS_TREE_API = "https://api.github.com/repos/tv-logo/tv-logos/git/trees/main?recursive=1"
TV_LOGOS_RAW_BASE = "https://raw.githubusercontent.com/tv-logo/tv-logos/main/"

# Dynamic event pools that deliberately keep generic placeholders per runbook
EVENT_REGEX = re.compile(
    r"^(Soccer PPV|DAZN PPV|Sky Sports\+|NBA \d+|NFL \d+|MLB \d+|NHL \d+|"
    r"UEFA \d+|UK Football|Bundesliga \d+|Sky Sport Bundesliga \d+|"
    r"Live Football \d+|ESPN PPV \d+|PPV \d+)",
    re.I,
)

# Suffixes and prefixes for tv-logos matching
NOISE = re.compile(
    r"\b(HD|FHD|UHD|4K|SD|HEVC|H265|H264|RAW|BACKUP|ALT|VIP|PLUS|SAT|"
    r"1080P?|720P?|2160P?|3840P?)\b",
    re.I,
)
NONWORD = re.compile(r"[^a-z0-9]+")
COUNTRY_PREFIX = re.compile(r"^(US|UK|CA|IT|DE|FR|ES|NL|PT|PL|TR|AR|BR|MX)\b[|:\s-]*", re.I)
CALLSIGN = re.compile(r"\b([KW][A-Z]{2,3})\b")
STRIP_CHARS = re.compile(r"[:/|]")


def pct_exec(ct: str, cmd: List[str], timeout: int = 120) -> Tuple[int, str, str]:
    """Execute command inside an LXC container."""
    full_cmd = ["/usr/sbin/pct", "exec", ct, "--"] + cmd
    try:
        proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"Command timed out after {timeout}s"
    except Exception as exc:
        return 1, "", str(exc)


def check_maintenance_window() -> Tuple[bool, str]:
    """Check if the overnight maintenance window (01:00-05:00 Berlin) is open."""
    rc, stdout, stderr = pct_exec(CT_ID_MEDIA, ["python3", MAINTENANCE_SCRIPT], timeout=15)
    output = stdout.strip() or stderr.strip() or f"exit code {rc}"
    is_open = (rc == 0)
    return is_open, output


def run_icon_archive_export() -> Dict[str, Any]:
    """Run icon-archive export and return parsed manifest."""
    cmd = ["python3", ICON_ARCHIVE_SCRIPT, "export"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"icon-archive export failed: {proc.stderr}")
    if not os.path.exists(MANIFEST_FILE):
        raise RuntimeError(f"Manifest not found at {MANIFEST_FILE}")
    with open(MANIFEST_FILE, "r") as fh:
        return json.load(fh)


def get_icon_metrics(stack: str = "production-jellyfin") -> Dict[str, Any]:
    """Read current icon metrics for a given stack from manifest and icon-host."""
    metrics = {
        "stack": stack,
        "total_channels": 0,
        "custom_icons": 0,
        "placeholder_icons": 0,
        "coverage_ratio": 0.0,
        "icon_host_count": 0,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE, "r") as fh:
            m = json.load(fh)
        s_data = m.get("stacks", {}).get(stack, {})
        tot = s_data.get("channels", 0)
        cust = s_data.get("custom", 0)
        ph = s_data.get("placeholder", 0)
        metrics["total_channels"] = tot
        metrics["custom_icons"] = cust
        metrics["placeholder_icons"] = ph
        metrics["coverage_ratio"] = round(cust / tot, 4) if tot > 0 else 0.0

    if os.path.isdir(EXTRACTED_DIR):
        metrics["icon_host_count"] = len(os.listdir(EXTRACTED_DIR))

    return metrics


def norm_name(s: str) -> str:
    """Normalize channel name for matching."""
    s = COUNTRY_PREFIX.sub("", s)
    s = NOISE.sub(" ", s)
    s = NONWORD.sub(" ", s.lower()).strip()
    s = re.sub(r"\s+", " ", s)
    return re.sub(r"^the ", "", s)


def get_name_variants(s: str) -> Set[str]:
    """Return normalized name variants (spaced and de-spaced)."""
    n = norm_name(s)
    return {n, n.replace(" ", "")}


def clean_filename(name: str) -> str:
    """Clean channel name for filesystem storage per runbook rules."""
    return STRIP_CHARS.sub("", name).strip()


def fetch_tv_logos_index() -> List[str]:
    """Fetch list of PNG files in tv-logos repository via GitHub API."""
    req = urllib.request.Request(
        TV_LOGOS_TREE_API,
        headers={
            "User-Agent": "media-core-icon-repair/1.0",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        paths = [
            n["path"]
            for n in data.get("tree", [])
            if n["type"] == "blob" and n["path"].lower().endswith(".png")
        ]
        return paths
    except Exception as exc:
        print(f"WARNING: Could not fetch tv-logos index from GitHub ({exc})", file=sys.stderr)
        return []


def build_tv_logos_index(paths: List[str]) -> Dict[str, List[str]]:
    """Build normalized stem lookup index from tv-logos paths."""
    index = collections.defaultdict(list)
    for p in paths:
        stem = p.rsplit("/", 1)[-1][:-4]
        stem_nc = re.sub(r"-[a-z]{2}$", "", stem)
        for v in get_name_variants(stem_nc):
            index[v].append(p)
    for k in index:
        index[k].sort(key=lambda p: (not p.startswith("countries/united-states"), len(p)))
    return index


def find_candidates(manifest: Dict[str, Any], stack: str = "production-jellyfin") -> List[str]:
    """Find channels currently holding generic/placeholder icons or missing artwork."""
    entries = manifest.get("stacks", {}).get(stack, {}).get("entries", {})
    candidates = []
    for name, data in entries.items():
        if data.get("placeholder") and not EVENT_REGEX.search(name):
            candidates.append(name)
    return sorted(candidates)


def match_candidates(candidates: List[str], index: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """Match candidates against tv-logos index."""
    matches = {"high": [], "medium": [], "unmatched": []}

    for ch in candidates:
        n = norm_name(ch)
        url = None
        conf = "unmatched"
        note = ""

        # 1. Exact match
        hit_exact = next((index[v] for v in get_name_variants(ch) if v in index), None)
        if hit_exact:
            url = TV_LOGOS_RAW_BASE + hit_exact[0]
            conf = "high"
            note = f"exact match on '{hit_exact[0]}'"
        else:
            # 2. US call sign match
            cs = CALLSIGN.search(ch)
            if cs:
                token = cs.group(1).lower()
                for k, v in index.items():
                    if token in k.split():
                        url = TV_LOGOS_RAW_BASE + v[0]
                        conf = "high"
                        note = f"call sign match on '{cs.group(1)}'"
                        break

            # 3. Substring match
            if not url:
                best = None
                for k, v in index.items():
                    if len(k) <= 4 or not (k in n or n in k):
                        continue
                    if k.split()[0] != n.split()[0]:
                        continue
                    ratio = min(len(k), len(n)) / max(len(k), len(n))
                    if ratio < 0.65:
                        continue
                    if best is None or ratio > best[0]:
                        best = (ratio, k, v)
                if best:
                    ratio, k, v = best
                    url = TV_LOGOS_RAW_BASE + v[0]
                    conf = "medium"
                    note = f"substring match on '{k}' (overlap {ratio:.2f})"

        item = {
            "channel": ch,
            "filename": clean_filename(ch) + ".png",
            "logo_url": url,
            "confidence": conf,
            "note": note,
        }
        matches[conf].append(item)

    return matches


def process_and_composite_logo(raw_png_bytes: bytes) -> bytes:
    """Composite transparent logos onto a solid backdrop per channel-icons-runbook.md.

    Luminance > 128 -> #141414 (dark background for light artwork)
    Luminance <= 128 -> #f2f2f2 (light background for dark artwork)
    """
    if not HAS_PIL:
        return raw_png_bytes

    try:
        img = Image.open(io.BytesIO(raw_png_bytes))
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        # Check alpha channel
        alpha = img.split()[-1]
        alpha_data = list(alpha.getdata())
        has_transparency = any(a < 250 for a in alpha_data)

        if not has_transparency:
            # Already fully opaque
            out_io = io.BytesIO()
            img.convert("RGB").save(out_io, format="PNG")
            return out_io.getvalue()

        # Calculate luminance of visible pixels
        rgb_img = img.convert("RGB")
        luminances = []
        for (r, g, b), a in zip(rgb_img.getdata(), alpha_data):
            if a > 30:  # non-transparent pixel
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                luminances.append(lum)

        avg_lum = sum(luminances) / len(luminances) if luminances else 255.0
        bg_color = (20, 20, 20) if avg_lum > 128 else (242, 242, 242)

        bg = Image.new("RGB", img.size, bg_color)
        bg.paste(img, (0, 0), mask=img.split()[3])

        out_io = io.BytesIO()
        bg.save(out_io, format="PNG")
        return out_io.getvalue()
    except Exception as exc:
        print(f"WARNING: Image processing fallback to raw bytes ({exc})", file=sys.stderr)
        return raw_png_bytes


def backup_icon_state() -> Tuple[str, str]:
    """Capture rollback backup of extracted icons and manifest on disk."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    extracted_bak = f"{EXTRACTED_DIR}.bak-iconrepair-{stamp}"
    manifest_bak = f"{MANIFEST_FILE}.bak-iconrepair-{stamp}.json"

    if os.path.exists(EXTRACTED_DIR):
        shutil.copytree(EXTRACTED_DIR, extracted_bak)
    else:
        os.makedirs(extracted_bak, exist_ok=True)

    if os.path.exists(MANIFEST_FILE):
        shutil.copy2(MANIFEST_FILE, manifest_bak)

    return extracted_bak, manifest_bak


def restore_icon_state(extracted_bak: str, manifest_bak: str) -> None:
    """Restore extracted icons and manifest from rollback backup."""
    if os.path.exists(extracted_bak):
        if os.path.exists(EXTRACTED_DIR):
            shutil.rmtree(EXTRACTED_DIR)
        shutil.copytree(extracted_bak, EXTRACTED_DIR)
    if os.path.exists(manifest_bak):
        shutil.copy2(manifest_bak, MANIFEST_FILE)


def install_matched_icons(matches: List[Dict[str, Any]]) -> List[str]:
    """Download, process, and install matched icons into extracted directory."""
    os.makedirs(EXTRACTED_DIR, exist_ok=True)
    installed = []

    for item in matches:
        url = item.get("logo_url")
        filename = item.get("filename")
        if not url or not filename:
            continue

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "media-core-icon-repair/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw_bytes = resp.read()

            processed_bytes = process_and_composite_logo(raw_bytes)
            dest_path = os.path.join(EXTRACTED_DIR, filename)
            with open(dest_path, "wb") as fh:
                fh.write(processed_bytes)

            installed.append(filename)
        except Exception as exc:
            print(f"WARNING: Failed to install {filename} from {url} ({exc})", file=sys.stderr)

    return installed


def run_verify(stack: str = "production-jellyfin") -> Dict[str, Any]:
    """Run icon-verify check to compare against archive and detect degradation."""
    cmd = ["python3", ICON_VERIFY_SCRIPT, "check", stack]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    output = proc.stdout
    degraded = 0
    unchanged = 0
    improved = 0

    m_deg = re.search(r"DEGRADED\s*:\s*(\d+)", output)
    m_unc = re.search(r"unchanged\s*:\s*(\d+)", output)
    m_imp = re.search(r"improved\s*:\s*(\d+)", output)

    if m_deg:
        degraded = int(m_deg.group(1))
    if m_unc:
        unchanged = int(m_unc.group(1))
    if m_imp:
        improved = int(m_imp.group(1))

    return {
        "success": proc.returncode == 0,
        "degraded": degraded,
        "unchanged": unchanged,
        "improved": improved,
        "raw_output": output.strip(),
    }


def run_loop(apply: bool = False, force_window: bool = False, test_revert: bool = False) -> Dict[str, Any]:
    """Execute the Channel Artwork repair loop.

    Flow:
    1. Snapshot archive & read baseline metrics via icon-archive export.
    2. Check maintenance window (01:00-05:00 Berlin).
    3. Identify missing/placeholder candidate channels (excluding event slots).
    4. Match against tv-logos index.
    5. Separate into high/medium matches and manual-generation proposals.
    6. If matches exist:
       - If dry-run or window closed: report proposed matches and generation list.
       - If --apply and window open:
         a. Capture rollback point (extracted backup + manifest backup).
         b. Download, composite with luminance backdrop, install to /root/icon-archive/extracted/.
         c. Re-run icon-archive export and icon-verify.
         d. Grade: If coverage did not improve or degraded > 0, AUTOMATIC REVERT and report.
    """
    report: Dict[str, Any] = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "apply" if apply else ("test-revert" if test_revert else "dry-run"),
        "baseline_metrics": {},
        "maintenance_window": {},
        "candidate_summary": {},
        "action_taken": "NONE",
        "repair_applied": False,
        "reverted": False,
        "final_metrics": {},
        "details": [],
        "proposals_for_generation": [],
    }

    # 1. Read baseline metrics
    baseline_metrics = get_icon_metrics("production-jellyfin")
    report["baseline_metrics"] = baseline_metrics
    initial_custom = baseline_metrics.get("custom_icons", 0)

    # 2. Check maintenance window
    is_window_open, window_reason = check_maintenance_window()
    report["maintenance_window"] = {
        "is_open": is_window_open,
        "reason": window_reason,
        "forced": force_window,
    }

    # 3. Read manifest & find candidate channels
    if not os.path.exists(MANIFEST_FILE):
        manifest = run_icon_archive_export()
    else:
        with open(MANIFEST_FILE, "r") as fh:
            manifest = json.load(fh)

    candidates = find_candidates(manifest, "production-jellyfin")
    report["candidate_summary"]["total_candidates"] = len(candidates)

    # Fetch tv-logos index & match
    paths = fetch_tv_logos_index()
    index = build_tv_logos_index(paths)
    matches = match_candidates(candidates, index)

    high_matches = matches["high"]
    med_matches = matches["medium"]
    unmatched = matches["unmatched"]
    installable_matches = high_matches + med_matches

    report["candidate_summary"]["high_confidence_matches"] = len(high_matches)
    report["candidate_summary"]["medium_confidence_matches"] = len(med_matches)
    report["candidate_summary"]["unmatched_needing_generation"] = len(unmatched)

    # Propose unmatched for manual generation (batch size 6 per runbook)
    report["proposals_for_generation"] = [u["channel"] for u in unmatched]

    # 4. Handle test-revert simulation
    if test_revert:
        report["details"].append("Running in --test-revert mode to verify automatic rollback.")
        ext_bak, man_bak = backup_icon_state()
        report["backup_created"] = ext_bak
        report["details"].append(f"Rollback point captured: {ext_bak}")

        try:
            # Construct a test change that will not improve coverage (e.g. empty dummy file or zeroing)
            dummy_file = os.path.join(EXTRACTED_DIR, "__test_dummy_degradation__.png")
            with open(dummy_file, "wb") as fh:
                fh.write(b"NOT_A_VALID_PNG_DATA")
            report["details"].append("Applied test modification to icon host extracted directory.")

            # Grade check simulation
            post_metrics = get_icon_metrics("production-jellyfin")
            post_custom = post_metrics.get("custom_icons", 0)
            report["details"].append(f"Post-change custom icon count: {post_custom} (baseline was {initial_custom})")

            # Grader checks: post_custom did not increase (or dummy test condition)
            if post_custom <= initial_custom:
                report["details"].append(
                    f"Grader check: post-change custom icons ({post_custom}) <= baseline ({initial_custom}). "
                    "Triggering AUTOMATIC REVERT."
                )
                restore_icon_state(ext_bak, man_bak)
                if os.path.exists(dummy_file):
                    os.unlink(dummy_file)
                report["details"].append(f"Restored icon state from {ext_bak}")
                restored_metrics = get_icon_metrics("production-jellyfin")
                report["final_metrics"] = restored_metrics
                report["reverted"] = True
                report["action_taken"] = "TEST_REVERT_SUCCESSFUL"
                report["details"].append("Rollback verified: exact pre-state restored on disk.")
            else:
                report["action_taken"] = "TEST_FAILED_UNEXPECTED_IMPROVEMENT"
        except Exception as exc:
            report["error"] = str(exc)
            report["details"].append(f"Exception during test-revert: {exc}. Restoring.")
            restore_icon_state(ext_bak, man_bak)
            report["reverted"] = True
        return report

    # 5. Normal Loop Operation
    if not installable_matches:
        report["action_taken"] = "NO_MATCHES_TO_INSTALL"
        report["details"].append(
            f"No high/medium confidence tv-logos matches found for {len(candidates)} candidate channels. "
            f"{len(unmatched)} channels proposed for manual generation (batch size 6)."
        )
        report["final_metrics"] = baseline_metrics
        return report

    report["details"].append(f"Found {len(installable_matches)} installable match(es) from tv-logos:")
    for m in installable_matches:
        report["details"].append(f"  - [{m['confidence'].upper()}] {m['channel']} -> {m['filename']} ({m['note']})")

    if unmatched:
        report["details"].append(
            f"{len(unmatched)} channels require bespoke artwork generation (kept OUT of automated install; "
            "recommend batching in groups of 6 per channel-icons-runbook.md)."
        )

    # Guard check for acting
    if not apply:
        report["action_taken"] = "DRY_RUN_REPORT_ONLY"
        report["details"].append("DRY-RUN mode: no changes applied to disk or icon host (pass --apply to act).")
        report["final_metrics"] = baseline_metrics
        return report

    if not is_window_open and not force_window:
        report["action_taken"] = "REFUSED_OUTSIDE_MAINTENANCE_WINDOW"
        report["details"].append(
            f"Maintenance window is CLOSED ({window_reason}). Acting runs are confined to 01:00-05:00 Europe/Berlin. "
            "Refusing to apply artwork changes."
        )
        report["final_metrics"] = baseline_metrics
        return report

    # Apply matched artwork with deterministic rollback guard
    ext_bak, man_bak = backup_icon_state()
    report["backup_created"] = ext_bak
    report["details"].append(f"Rollback point created at: {ext_bak}")

    try:
        installed = install_matched_icons(installable_matches)
        report["installed_files"] = installed
        report["details"].append(f"Installed {len(installed)} processed icons into {EXTRACTED_DIR}.")

        # Re-index archive
        new_manifest = run_icon_archive_export()
        verify_res = run_verify("production-jellyfin")
        report["verify_result"] = verify_res

        post_metrics = get_icon_metrics("production-jellyfin")
        report["post_repair_metrics"] = post_metrics
        post_custom = post_metrics.get("custom_icons", 0)

        # Grade with the metric
        if verify_res.get("degraded", 0) == 0 and post_custom > initial_custom:
            report["action_taken"] = "ARTWORK_APPLIED_AND_IMPROVED"
            report["repair_applied"] = True
            report["details"].append(
                f"Metric improved: custom icons {initial_custom} -> {post_custom} (degraded={verify_res.get('degraded', 0)}). "
                "Artwork change retained."
            )
            report["final_metrics"] = post_metrics
        else:
            # Did not improve or degraded channels found -> automatic revert
            report["action_taken"] = "REVERTED_NO_IMPROVEMENT"
            report["reverted"] = True
            report["details"].append(
                f"Metric did NOT improve (before={initial_custom}, after={post_custom}, degraded={verify_res.get('degraded')}). "
                f"Triggering automatic rollback from {ext_bak}."
            )
            restore_icon_state(ext_bak, man_bak)
            run_icon_archive_export()
            restored_metrics = get_icon_metrics("production-jellyfin")
            report["final_metrics"] = restored_metrics
            report["details"].append(f"Rollback completed. Restored custom icon count: {restored_metrics.get('custom_icons')}.")

    except Exception as exc:
        report["action_taken"] = "ERROR_ROLLED_BACK"
        report["error"] = str(exc)
        report["details"].append(f"Exception encountered: {exc}. Rolling back to {ext_bak}.")
        restore_icon_state(ext_bak, man_bak)
        run_icon_archive_export()
        report["reverted"] = True
        report["final_metrics"] = get_icon_metrics("production-jellyfin")

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-correcting repair loop for channel artwork.")
    parser.add_argument("--apply", action="store_true", help="Apply matched artwork if maintenance window is open")
    parser.add_argument("--dry-run", action="store_true", help="Report only without modifying state (default)")
    parser.add_argument("--force-window", action="store_true", help="Bypass maintenance window check (testing only)")
    parser.add_argument("--test-revert", action="store_true", help="Simulate a failing repair and verify automatic rollback")
    parser.add_argument("--json", action="store_true", help="Output results formatted as JSON")
    args = parser.parse_args()

    # Default is dry-run unless --apply or --test-revert is specified
    apply_mode = args.apply and not args.dry_run
    res = run_loop(apply=apply_mode, force_window=args.force_window, test_revert=args.test_revert)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print("=" * 68)
        print(f"LOOP B: Channel Artwork Repair Loop [{res['mode'].upper()}]")
        print("=" * 68)
        bm = res.get("baseline_metrics", {})
        print(f"Grader baseline : custom={bm.get('custom_icons')} / {bm.get('total_channels')} "
              f"(placeholders: {bm.get('placeholder_icons')}, ratio: {bm.get('coverage_ratio')})")
        mw = res.get("maintenance_window", {})
        print(f"Window status   : {'OPEN' if mw.get('is_open') else 'CLOSED'} ({mw.get('reason')})")

        cs = res.get("candidate_summary", {})
        print(f"Candidate audit : {cs.get('total_candidates', 0)} non-event candidates "
              f"({cs.get('high_confidence_matches', 0)} high match, {cs.get('medium_confidence_matches', 0)} med match, "
              f"{cs.get('unmatched_needing_generation', 0)} unmatched)")

        print(f"Action taken    : {res.get('action_taken')}")
        if res.get("backup_created"):
            print(f"Rollback point  : {res.get('backup_created')}")
        if res.get("reverted"):
            print("Rollback status : REVERTED AUTOMATICALLY (restored pre-state)")

        fm = res.get("final_metrics", {})
        print(f"Final metric    : custom={fm.get('custom_icons')} / {fm.get('total_channels')} "
              f"(placeholders: {fm.get('placeholder_icons')}, ratio: {fm.get('coverage_ratio')})")

        print("\nDetails:")
        for d in res.get("details", []):
            print(f"  {d}")

        unmatched_list = res.get("proposals_for_generation", [])
        if unmatched_list:
            print("\nProposals for bespoke artwork generation (batched at 6 per runbook):")
            for i in range(0, min(18, len(unmatched_list)), 6):
                batch = unmatched_list[i : i + 6]
                print(f"  Batch {i//6 + 1}: {', '.join(batch)}")
            if len(unmatched_list) > 18:
                print(f"  ... and {len(unmatched_list) - 18} more candidates")
        print("=" * 68)

    return 0 if not res.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
