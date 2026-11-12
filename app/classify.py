"""Job apply-type classification, derived from the job's URL/domain.

apply_type:
  ats_form       — ATS/company application form, NO account needed
                   (Greenhouse, Lever, Ashby, Workable, SmartRecruiters,
                   Breezy, Recruitee, plain company sites).
  network_login  — job network that applies through YOUR account there
                   (LinkedIn, Indeed) — login required.
  ats_account    — ATS portal that forces you to create an account on that
                   employer's instance (Workday) — login required.
  external       — anything else / no URL: treated as no-login preparation.

requires_login is 1 only for network_login and ats_account. Login-required
jobs are the ONLY ones the browser extension add-on is ever involved in;
ats_form/external jobs never need it.
"""
from urllib.parse import urlparse

NETWORK_HOSTS = ("linkedin.com",)
ATS_FORM_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "smartrecruiters.com", "breezy.hr", "recruitee.com",
)

def _host(url):
    if not url:
        return ""
    u = url.strip()
    if "://" not in u:
        u = "https://" + u
    try:
        return (urlparse(u).hostname or "").lower()
    except Exception:
        return ""

def classify_job(url):
    """Returns (apply_type, requires_login)."""
    host = _host(url)
    if not host:
        return "external", 0
    if any(host == h or host.endswith("." + h) for h in NETWORK_HOSTS) or \
       host.startswith("indeed.") or ".indeed." in host:
        return "network_login", 1
    if "myworkdayjobs" in host or "workday" in host:
        return "ats_account", 1
    if any(host == h or host.endswith("." + h) for h in ATS_FORM_HOSTS):
        return "ats_form", 0
    return "external", 0
