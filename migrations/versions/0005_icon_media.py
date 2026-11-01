"""Icon media storage and resumable upload identity."""

from alembic import op

from app.modules.media.models import MediaAsset

revision = "0005_icon_media"
down_revision = "0004_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    MediaAsset.__table__.create(op.get_bind())


def downgrade() -> None:
    raise RuntimeError("Preserve uploaded media records; destructive downgrade is not supported")
