"""Code-owned notification events and safe built-in template fallbacks."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EventDefinition:
    id: str
    title: str
    description: str
    variables: tuple[str, ...]


@dataclass(frozen=True)
class DefaultTemplate:
    subject: str
    text: str
    href: str = ""


EVENTS = (
    EventDefinition(
        id="comment.pending_review",
        title="评论待审核",
        description="读者提交评论并进入审核队列。",
        variables=(
            "actor.name",
            "content.excerpt",
            "resource.title",
            "resource.url",
            "resource.review_url",
        ),
    ),
)

EVENT_BY_ID = {item.id: item for item in EVENTS}

DEFAULT_TEMPLATES = {
    "comment.pending_review": {
        "in-app": {
            "zh-CN": DefaultTemplate(
                subject="评论待审核",
                text="{{actor.name}} 在「{{resource.title}}」提交了评论：{{content.excerpt}}",
                href="{{resource.review_url}}",
            ),
            "en": DefaultTemplate(
                subject="Comment awaiting review",
                text='{{actor.name}} commented on "{{resource.title}}": {{content.excerpt}}',
                href="{{resource.review_url}}",
            ),
        },
        "email": {
            "zh-CN": DefaultTemplate(
                subject="评论待审核 · {{resource.title}}",
                text=(
                    "{{actor.name}} 在「{{resource.title}}」提交了一条待审核评论。\n\n"
                    "{{content.excerpt}}\n\n审核：{{resource.review_url}}"
                ),
                href="{{resource.review_url}}",
            ),
            "en": DefaultTemplate(
                subject="Comment awaiting review · {{resource.title}}",
                text=(
                    '{{actor.name}} submitted a comment on "{{resource.title}}".\n\n'
                    "{{content.excerpt}}\n\nReview: {{resource.review_url}}"
                ),
                href="{{resource.review_url}}",
            ),
        },
    },
}


def default_template(event_type: str, platform_id: str, locale: str) -> DefaultTemplate:
    variants = DEFAULT_TEMPLATES.get(event_type, {}).get(platform_id, {})
    return (
        variants.get(locale)
        or variants.get(locale.split("-", 1)[0])
        or variants.get("zh-CN")
        or DefaultTemplate(subject=event_type, text=event_type)
    )
