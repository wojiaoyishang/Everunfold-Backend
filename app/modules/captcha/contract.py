"""Single authoritative transport graph, including admission and private page reads."""

from app.core.schemas import PublicResponse
from app.modules.captcha.comments import CommentReceipt, SubmitComment
from app.modules.captcha.page_access import UnlockedManagedPage
from app.modules.captcha.schemas import CaptchaContract
from app.modules.content.access_router import ContentAccessInput


class CaptchaAPIContract(CaptchaContract):
    page_request: ContentAccessInput
    page_response: PublicResponse[UnlockedManagedPage]
    comment_request: SubmitComment
    comment_response: PublicResponse[CommentReceipt]
