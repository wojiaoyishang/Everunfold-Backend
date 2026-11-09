"""Single authoritative transport graph, including admission and private page reads."""

from app.core.schemas import PublicResponse
from app.modules.captcha.comments import CommentReceipt, SubmitComment
from app.modules.captcha.schemas import CaptchaContract
from app.modules.content.access_router import ContentAccessInput
from app.modules.pages.schemas import PublicManagedPage


class CaptchaAPIContract(CaptchaContract):
    page_request: ContentAccessInput
    page_response: PublicResponse[PublicManagedPage]
    comment_request: SubmitComment
    comment_response: PublicResponse[CommentReceipt]
