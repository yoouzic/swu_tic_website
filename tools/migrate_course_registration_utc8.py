"""One-time migration for legacy SQLite UTC CourseRegistration.created_at.

IMPORTANT:
- Run in dry-run mode first; this is the default.
- The migration assumes existing CourseRegistration.created_at values were stored as SQLite
  UTC by `CURRENT_TIMESTAMP`, and shifts them to the same local/UTC+8 semantics used by
  `datetime.now()` elsewhere in the project.
- Back up the database before applying in production.
- The migration is idempotent via a SystemSetting marker; only a fresh database or `--force`
  should reprocess rows.
"""

import argparse
import sys
from datetime import datetime, timedelta

MIGRATION_MARKER = 'course_registration_utc8_migrated'


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Actually persist the UTC -> UTC+8 shift. Omit for dry-run only.',
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Ignore the migration marker and process rows again (for verified restores only).',
    )
    return parser.parse_args()


def run():
    args = parse_args()

    # Import after argument parsing so --help does not require full app.
    from app.app import app
    from app.models import CourseRegistration, SystemSetting, db

    with app.app_context():
        marker = SystemSetting.get(MIGRATION_MARKER)
        if marker and not args.force:
            print(f'Migration marker present ({MIGRATION_MARKER}); nothing to do.')
            return 0

        rows = CourseRegistration.query.all()
        if not rows:
            print('No CourseRegistration rows to migrate.')
            if args.apply:
                SystemSetting.set(MIGRATION_MARKER, '1')
                print('Migration marker set.')
            return 0

        print(f'Dry-run{" apply" if args.apply else ""}: {len(rows)} rows would be shifted UTC -> UTC+8.')
        for row in rows:
            original = row.created_at
            shifted = original + timedelta(hours=8) if original is not None else None
            print(f'  id={row.id} original={original} shifted={shifted}')

        if args.apply:
            for row in rows:
                if row.created_at is not None:
                    row.created_at = row.created_at + timedelta(hours=8)
            SystemSetting.set(MIGRATION_MARKER, '1')
            db.session.commit()
            print('Migration applied and marker set.')
        else:
            print('Use --apply to persist. Backup required before applying.')
    return 0


if __name__ == '__main__':
    sys.exit(run())
