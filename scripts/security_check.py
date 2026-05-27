#!/usr/bin/env python3
"""
Origin Security Checker — Run before deploys or periodically.

Checks:
  1. .env not committed to git
  2. .env.example has no real secrets
  3. All Python deps are pinned (==)
  4. npm lockfile integrity
  5. Known vulnerable patterns in code
  6. CORS not set to wildcard
  7. No hardcoded secrets in source

Usage:
    python scripts/security_check.py
"""

import re
import sys
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
WARN = "\033[93m[WARN]\033[0m"

issues = []


def check(name: str, passed: bool, detail: str = ""):
    tag = PASS if passed else FAIL
    print(f"  {tag} {name}")
    if detail and not passed:
        print(f"        {detail}")
    if not passed:
        issues.append(name)


def main():
    print("\n=== Origin Security Audit ===\n")

    # 1. Check .env is gitignored
    print("[1/7] Git & Secrets")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8", errors="ignore") if (ROOT / ".gitignore").exists() else ""  # noqa: E501
    check(".env in .gitignore", ".env" in gitignore)

    # Check .env.example for real keys
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8", errors="ignore") if (ROOT / ".env.example").exists() else ""  # noqa: E501
    real_key_patterns = [
        r"sk-[A-Za-z0-9]{20,}",        # OpenAI/similar API keys
        r"nvapi-[A-Za-z0-9]{20,}",      # NVIDIA keys
        r"AIza[A-Za-z0-9_-]{35}",       # Google API keys
        r"gsk_[A-Za-z0-9]{20,}",        # Groq keys
    ]
    has_real_keys = any(re.search(p, env_example) for p in real_key_patterns)
    check(".env.example has no real secrets", not has_real_keys,
          "Found patterns matching real API keys in .env.example!")

    # 2. Python dependency pinning
    print("\n[2/7] Dependency Pinning (Supply Chain)")
    req_file = ROOT / "requirements.txt"
    if req_file.exists():
        req_text = req_file.read_text(encoding="utf-8", errors="ignore")
        unpinned = []
        for line in req_text.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "==" not in line and ">=" not in line:
                unpinned.append(line)
            elif ">=" in line and "==" not in line:
                unpinned.append(line)
        check("All Python deps pinned (==)", len(unpinned) == 0,
              f"Unpinned: {', '.join(unpinned)}")
    else:
        check("requirements.txt exists", False)

    # npm lockfile
    lockfile = ROOT / "frontend" / "package-lock.json"
    check("npm lockfile exists (package-lock.json)", lockfile.exists(),
          "Run 'npm install' to generate lockfile for reproducible builds")

    # 3. Hardcoded secrets scan
    print("\n[3/7] Hardcoded Secrets Scan")
    secret_patterns = [
        (r"sk-[A-Za-z0-9]{20,}", "API key (sk-...)"),
        (r"nvapi-[A-Za-z0-9]{20,}", "NVIDIA API key"),
        (r"AIza[A-Za-z0-9_-]{35}", "Google API key"),
        (r"ghp_[A-Za-z0-9]{36}", "GitHub token"),
        (r"password\s*=\s*['\"][^'\"]{8,}['\"]", "Hardcoded password"),
    ]
    source_files = list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.ts")) + list(ROOT.rglob("*.tsx"))
    # Exclude venv, node_modules, __pycache__
    source_files = [f for f in source_files if not any(
        skip in str(f) for skip in ("venv", "node_modules", "__pycache__", ".git", "security_check", "setup.py")
    )]

    secrets_found = []
    for f in source_files:
        try:
            content = f.read_text(errors="ignore")
            for pattern, desc in secret_patterns:
                if re.search(pattern, content):
                    secrets_found.append(f"{f.relative_to(ROOT)}: {desc}")
        except Exception:
            pass

    check("No hardcoded secrets in source", len(secrets_found) == 0,
          "\n        ".join(secrets_found[:5]))

    # 4. CORS check
    print("\n[4/7] API Security")
    api_main = ROOT / "api" / "main.py"
    if api_main.exists():
        api_text = api_main.read_text(encoding="utf-8", errors="ignore")
        check("CORS not wildcard (*)", 'allow_origins=["*"]' not in api_text,
              "CORS allows all origins — restrict to localhost")
        check("Host not 0.0.0.0", '"0.0.0.0"' not in api_text,
              "API exposed to all network interfaces")
        check("Rate limiting present", "RateLimiter" in api_text or "rate_limit" in api_text.lower())
        check("Security headers present", "X-Content-Type-Options" in api_text or "SecurityHeaders" in api_text)

    # 5. Known vulnerable patterns
    print("\n[5/7] Code Security Patterns")
    # Check for eval() in Python files
    eval_files = []
    for f in source_files:
        if f.suffix == ".py":
            try:
                content = f.read_text(errors="ignore")
                # Match eval() but not safe_eval or ast-based
                if re.search(r"(?<!safe_|_)eval\s*\(", content) and "safe_eval" not in str(f):
                    eval_files.append(str(f.relative_to(ROOT)))
            except Exception:
                pass
    check("No unsafe eval() usage", len(eval_files) == 0,
          f"Files with eval(): {', '.join(eval_files)}")

    # Check for subprocess without sanitization (exclude build/setup scripts)
    subprocess_files = []
    build_scripts = {"setup.py", "install.py", "build.py"}
    for f in source_files:
        if f.suffix == ".py" and f.name not in build_scripts:
            try:
                content = f.read_text(errors="ignore")
                if "subprocess" in content and "shell=True" in content:
                    subprocess_files.append(str(f.relative_to(ROOT)))
            except Exception:
                pass
    check("No subprocess with shell=True", len(subprocess_files) == 0,
          f"Files: {', '.join(subprocess_files)}")

    # 6. File permissions
    print("\n[6/7] File Security")
    env_file = ROOT / ".env"
    if env_file.exists():
        check(".env file exists (expected)", True)
    else:
        check(".env file exists", False, "Create .env from .env.example")

    data_dir = ROOT / "data"
    check("data/ directory exists", data_dir.exists())

    # 7. Supply chain
    print("\n[7/7] Supply Chain Defense")
    check("No postinstall scripts in package.json",
          not _has_postinstall(ROOT / "frontend" / "package.json"))

    # Summary
    print(f"\n{'=' * 50}")
    if issues:
        print(f"{FAIL} {len(issues)} issue(s) found:")
        for issue in issues:
            print(f"  - {issue}")
        print("\nFix these before deploying.")
        return 1
    else:
        print(f"{PASS} All checks passed!")
        return 0


def _has_postinstall(package_json: Path) -> bool:
    """Check if package.json has suspicious lifecycle scripts."""
    if not package_json.exists():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8", errors="ignore"))
        scripts = data.get("scripts", {})
        dangerous = {"postinstall", "preinstall", "install", "prepare"}
        return bool(dangerous & set(scripts.keys()))
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
