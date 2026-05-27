# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | ✅        |

## Reporting a Vulnerability

If you discover a security vulnerability in Origin, **do not open a public GitHub issue**.

Contact us privately at: pvadim030@gmail.com  
Subject line: `[SECURITY] Origin vulnerability report`

Include:
- A description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix (optional)

You will receive an acknowledgement within 48 hours and a resolution timeline within 7 days.

## Security Hardening Checklist

Before deploying Origin, ensure:

- [ ] **Rotate all API keys**: Never use keys from `.env.example` verbatim.
- [ ] **Set `ORIGIN_JWT_SECRET`**: Run `python -c "import secrets; print(secrets.token_hex(32))"` and set in `.env`.
- [ ] **Use strong admin password**: Minimum 12 characters; `admin/origin` default is dev-only.
- [ ] **Set `ORIGIN_ALLOW_DEFAULT_ADMIN=0`** (default) in production.
- [ ] **Run behind a reverse proxy** (nginx/Caddy) with HTTPS in production.
- [ ] **Restrict CORS**: Update `ALLOWED_ORIGINS` in `api/main.py` to your actual domain.
- [ ] **Set `LOG_FORMAT=json`** for structured log aggregation in production.
- [ ] **Audit dependencies**: Run `pip-audit -r requirements.txt` before each release.
- [ ] **Use secrets manager** (AWS Secrets Manager, HashiCorp Vault) instead of `.env` in production.

## Known Security Controls

- HMAC-SHA256 token authentication with configurable expiry
- bcrypt password hashing (PBKDF2 fallback)
- Rate limiting per IP (60 req/min default)
- Prompt injection detection and blocking (`core/injection_guard.py`)
- Security headers: CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- Input length caps (10,000 chars per message)
- Path traversal prevention on context file endpoints
- Auth data excluded from git (`data/auth/` in `.gitignore`)
