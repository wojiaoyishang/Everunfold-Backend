from app.modules.captcha.providers import CaptchaProvider, verify_site_token
from app.modules.captcha.schemas import StoredProvider


async def verify(answer: str, settings: StoredProvider) -> bool:
    return await verify_site_token(
        "https://www.google.com/recaptcha/api/siteverify", answer, settings
    )


provider = CaptchaProvider(
    "google.recaptcha-v2", "Google reCAPTCHA v2", "core.captcha.google", verify
)
