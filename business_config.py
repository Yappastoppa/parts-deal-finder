import os


BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "CARBOTFINDER")
LEGAL_BUSINESS_NAME = os.environ.get("LEGAL_BUSINESS_NAME", "")
SUPPORT_EMAIL = os.environ.get("SUPPORT_EMAIL", "")
SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "")
BUSINESS_ADDRESS = os.environ.get("BUSINESS_ADDRESS", "")
RETURN_WINDOW_DAYS = os.environ.get("RETURN_WINDOW_DAYS", "")
RESTOCKING_FEE_PERCENT = os.environ.get("RESTOCKING_FEE_PERCENT", "")
TERMS_VERSION = os.environ.get("TERMS_VERSION", "2026-08-24")
PRIVACY_VERSION = os.environ.get("PRIVACY_VERSION", "2026-08-24")
SHIPPING_POLICY_VERSION = os.environ.get("SHIPPING_POLICY_VERSION", "2026-08-24")
DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "USD")
DEVELOPMENT_MODE = os.environ.get("ENVIRONMENT", "development").lower() != "production"
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0").lower() in {"1", "true", "yes"}
