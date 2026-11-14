"""Keep the existing AI model and encrypted key as the default named provider."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0065_ai_writing_providers"
down_revision = "0064_ai_writing"
branch_labels = None
depends_on = None

LEGACY_PROVIDER_ID = "00000000-0000-4000-8000-000000000001"
LEGACY_PROVIDER_NAME = "默认服务商"


def upgrade() -> None:
    op.add_column(
        "ai_writing_settings",
        sa.Column("providers", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("ai_writing_settings", sa.Column("default_provider_id", sa.Uuid()))
    op.add_column("ai_writing_requests", sa.Column("provider_id", sa.Uuid()))
    op.execute(
        sa.text(
            """
            UPDATE ai_writing_settings
            SET providers = jsonb_build_array(jsonb_build_object(
                'id', :provider_id, 'name', :provider_name, 'base_url', base_url,
                'model', model, 'key_ciphertext', key_ciphertext
            )), default_provider_id = CAST(:provider_id AS uuid)
            WHERE base_url <> '' OR model <> '' OR key_ciphertext IS NOT NULL
            """
        ).bindparams(provider_id=LEGACY_PROVIDER_ID, provider_name=LEGACY_PROVIDER_NAME)
    )
    op.execute(
        """
        UPDATE ai_writing_requests
        SET provider_id = (SELECT default_provider_id FROM ai_writing_settings WHERE id = 1)
        WHERE status IN ('queued', 'running')
        """
    )
    op.alter_column("ai_writing_settings", "providers", server_default=None)
    for column in ("base_url", "model", "key_ciphertext"):
        op.drop_column("ai_writing_settings", column)


def downgrade() -> None:
    bind = op.get_bind()
    op.execute("LOCK TABLE ai_writing_settings, ai_writing_requests IN ACCESS EXCLUSIVE MODE")
    settings_allow_downgrade = bind.scalar(
        sa.text(
            """
            SELECT jsonb_array_length(providers) = 0 OR (
                jsonb_array_length(providers) = 1
                AND default_provider_id = CAST(:provider_id AS uuid)
                AND providers->0->>'id' = :provider_id
                AND providers->0->>'name' = :provider_name
            )
            FROM ai_writing_settings WHERE id = 1
            """
        ).bindparams(provider_id=LEGACY_PROVIDER_ID, provider_name=LEGACY_PROVIDER_NAME)
    )
    # New queued payloads include provider fields that the older worker cannot decode.
    active_requests = bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM ai_writing_requests WHERE status IN ('queued','running'))"
        )
    )
    if not settings_allow_downgrade or active_requests:
        raise RuntimeError("Downgrade would lose configured AI providers or active requests")
    op.add_column(
        "ai_writing_settings",
        sa.Column("base_url", sa.String(2048), nullable=False, server_default=""),
    )
    op.add_column(
        "ai_writing_settings", sa.Column("model", sa.String(160), nullable=False, server_default="")
    )
    op.add_column("ai_writing_settings", sa.Column("key_ciphertext", sa.Text()))
    op.execute(
        """
        UPDATE ai_writing_settings
        SET base_url = providers->0->>'base_url',
            model = providers->0->>'model',
            key_ciphertext = providers->0->>'key_ciphertext'
        WHERE jsonb_array_length(providers) = 1
        """
    )
    for column in ("base_url", "model"):
        op.alter_column("ai_writing_settings", column, server_default=None)
    op.drop_column("ai_writing_requests", "provider_id")
    op.drop_column("ai_writing_settings", "default_provider_id")
    op.drop_column("ai_writing_settings", "providers")
