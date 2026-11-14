from __future__ import annotations

import shutil

# Common CLI pentest / recon tools. Detection is by presence on PATH, so the count reflects
# what is ACTUALLY installed on the host (Kali ships many of these). The agent invokes them
# through run_command when the workspace shell is enabled.
KNOWN_TOOLS = [
    # discovery / recon
    "subfinder", "amass", "assetfinder", "findomain", "dnsx", "naabu", "nmap", "masscan",
    "httpx", "httprobe", "katana", "hakrawler", "gau", "waybackurls", "gospider", "gauplus",
    # content / fuzzing
    "ffuf", "feroxbuster", "gobuster", "dirb", "dirsearch", "wfuzz", "arjun",
    # scanners
    "nuclei", "nikto", "wpscan", "whatweb", "wafw00f", "testssl.sh", "sslscan",
    # injection / exploitation
    "sqlmap", "dalfox", "commix", "tplmap", "xsstrike", "nosqlmap",
    # web / proxies / misc
    "curl", "jq", "python3", "openssl", "git", "wget", "nc", "socat",
    # cloud / secrets
    "trufflehog", "gitleaks", "s3scanner", "cloud_enum",
    # jwt / auth
    "jwt_tool", "hashcat", "john",
]


def detect() -> dict:
    installed = [t for t in KNOWN_TOOLS if shutil.which(t)]
    missing = [t for t in KNOWN_TOOLS if t not in installed]
    return {
        "installed": installed,
        "installed_count": len(installed),
        "missing": missing,
        "total_checked": len(KNOWN_TOOLS),
    }


def installed_names() -> list[str]:
    return [t for t in KNOWN_TOOLS if shutil.which(t)]
