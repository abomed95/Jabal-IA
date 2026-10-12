"""Appareils : identifiant local de l'appareil et limite recalculée aussi à la réactivation.

Constaté à l'implémentation : sans identifiant stable côté client, une reconnexion depuis le même
téléphone était comptée comme un 3e appareil. Le déclencheur ne couvrait pas non plus la
réactivation d'un appareil révoqué.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE devices ADD COLUMN client_device_id uuid NOT NULL")
    op.execute("CREATE UNIQUE INDEX devices_user_client_uq ON devices (user_id, client_device_id)")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_device_limit() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.revoked_at IS NULL AND
             (SELECT count(*) FROM devices
               WHERE user_id = NEW.user_id AND revoked_at IS NULL AND id <> NEW.id) >= 2 THEN
            RAISE EXCEPTION 'device_limit_reached' USING ERRCODE = 'P0001';
          END IF;
          RETURN NEW;
        END $$
        """
    )
    op.execute("DROP TRIGGER devices_limit ON devices")
    op.execute(
        "CREATE TRIGGER devices_limit BEFORE INSERT OR UPDATE OF revoked_at ON devices "
        "FOR EACH ROW EXECUTE FUNCTION enforce_device_limit()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER devices_limit ON devices")
    op.execute("DROP INDEX devices_user_client_uq")
    op.execute("ALTER TABLE devices DROP COLUMN client_device_id")
    op.execute(
        "CREATE TRIGGER devices_limit BEFORE INSERT ON devices "
        "FOR EACH ROW EXECUTE FUNCTION enforce_device_limit()"
    )
