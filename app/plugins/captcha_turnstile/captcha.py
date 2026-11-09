from app.modules.captcha.providers import CaptchaProvider, verify_site_token
from app.modules.captcha.schemas import StoredProvider


async def verify(answer: str, settings: StoredProvider) -> bool:
    return await verify_site_token(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify", answer, settings
    )


provider = CaptchaProvider(
    "cloudflare.turnstile", "Cloudflare Turnstile", "core.captcha.turnstile", verify
)
