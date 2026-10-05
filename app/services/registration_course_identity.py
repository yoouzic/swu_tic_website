"""Pin reservation identity without guessing a term from current settings.

Unresolved legacy rows remain readable and deletable. Only a unique candidate
or consistent exact evidence from bound forms may pin a legacy Course ID.
The persisted evidence is immutable once decided; imports cannot reinterpret
an ambiguous historical row using a newer course catalog.
"""
from collections import defaultdict
import json
from pathlib import Path
import re

from sqlalchemy import MetaData, Table, inspect
from sqlalchemy.schema import CreateTable, ForeignKeyConstraint

from app.models import Course, CourseRegistration, db
from app.services.current_courses import current_course_query, is_current_course


def _text(value):
    return str(value or '').strip()


def _choose_legacy_course(candidates, forms):
    evidence = {'candidate_ids': [course['id'] for course in candidates]}
    if forms:
        # Every bound version must support the same exact course. A corrected
        # or conflicting historical title does not license a current guess.
        matches = set(evidence['candidate_ids'])
        for form in forms:
            exact = {course['id'] for course in candidates
                     if _text(form['course_title']) == _text(course['course_name'])
                     and (not _text(course.get('teacher_name'))
                          or _text(form['teacher_name']) == _text(course['teacher_name']))}
            matches &= exact
        evidence.update(source='bound_form', form_ids=[form['id'] for form in forms])
        if len(matches) == 1:
            return next(course for course in candidates if course['id'] in matches), 'resolved', evidence
        return None, 'ambiguous' if candidates else 'missing', evidence
    evidence['source'] = 'unique_course_pair'
    if len(candidates) == 1:
        return candidates[0], 'resolved', evidence
    return None, 'ambiguous' if candidates else 'missing', evidence


def stamp_registration_course(registration, course):
    """Assign the selected server-validated Course; caller owns the transaction."""
    registration.course_id = course.id
    registration.semester = course.semester
    registration.academic_year = course.academic_year
    registration.identity_status = 'resolved'
    registration.identity_evidence_json = json.dumps(
        {'source': 'selected_current_course', 'course_id': course.id,
         'semester': course.semester, 'academic_year': course.academic_year},
        ensure_ascii=False, sort_keys=True)


def resolve_current_course_input(course_code, selection_code, course_id=None):
    """Accept an exact selected ID, or the old two-code API only when unique."""
    query = current_course_query().filter_by(course_code=course_code, selection_code=selection_code)
    if course_id is not None:
        if isinstance(course_id, bool) or not isinstance(course_id, (int, str)):
            return None
        if not re.fullmatch(r'[1-9][0-9]*', str(course_id)):
            return None
        parsed = int(course_id)
        return query.filter(Course.id == parsed).first()
    matches = query.limit(2).all()
    return matches[0] if len(matches) == 1 else None


def resolve_registration_course(registration, *, current_only=False):
    """Read an exact reservation identity, with conservative unmigrated support."""
    if registration.course_id is not None:
        course = db.session.get(Course, registration.course_id)
        if course is None or (course.course_code, course.selection_code) != (registration.course_code, registration.selection_code):
            return None
        if (registration.semester is not None and registration.semester != course.semester
                or registration.academic_year is not None and registration.academic_year != course.academic_year):
            return None
    elif registration.identity_status not in {None, 'pending'}:
        return None
    else:
        # Supports caller-owned legacy fixture/import transactions without a
        # read endpoint committing migrations or using current-term authority.
        candidates = Course.query.filter_by(course_code=registration.course_code, selection_code=registration.selection_code).all()
        from app.models import LectureForm
        forms = LectureForm.query.filter_by(registration_id=registration.id).all()
        course_data = [dict(id=c.id, course_name=c.course_name, teacher_name=c.teacher.name if c.teacher else '') for c in candidates]
        form_data = [dict(id=f.id, course_title=f.course_title, teacher_name=f.teacher_name) for f in forms]
        chosen, _, _ = _choose_legacy_course(course_data, form_data)
        course = next((c for c in candidates if chosen and c.id == chosen['id']), None)
    return course if course and (not current_only or is_current_course(course.id)) else None


def registrations_for_course(course_id, *, user_id=None):
    """Identity-filtered rows, including unique not-yet-migrated legacy fixtures."""
    course = db.session.get(Course, course_id)
    if course is None:
        return []
    query = CourseRegistration.query.filter_by(course_code=course.course_code, selection_code=course.selection_code)
    if user_id is not None:
        query = query.filter_by(user_id=user_id)
    return [registration for registration in query.order_by(CourseRegistration.created_at.desc(), CourseRegistration.id.desc()).all()
            if (resolved := resolve_registration_course(registration)) is not None and resolved.id == course_id]


def ensure_registration_course_identity_schema(engine=None):
    """Upgrade existing tables atomically; a fresh empty DB is left untouched."""
    engine = engine if engine is not None else db.engine
    if engine.dialect.name == 'sqlite':
        filename = engine.url.database
        if not filename or filename == ':memory:' or not Path(filename).exists() or Path(filename).stat().st_size == 0:
            return
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        if not ({'course_registrations', 'lecture_bans'} & tables):
            return
        connection.commit()
        if connection.dialect.name == 'sqlite':
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        else:
            connection.begin()
        try:
            if 'lecture_bans' in tables:
                invalid = [fk for fk in inspect(connection).get_foreign_keys('lecture_bans')
                           if fk['referred_table'] == 'courses' and fk['constrained_columns'] == ['course_id']
                           and fk['referred_columns'] == ['course_code']]
                if invalid:
                    if connection.dialect.name != 'sqlite':
                        raise RuntimeError('Legacy lecture_bans foreign key requires an explicit database migration')
                    _rebuild_legacy_bans(connection)
            if 'course_registrations' in tables:
                columns = {column['name'] for column in inspect(connection).get_columns('course_registrations')}
                additions = {'course_id': 'INTEGER REFERENCES courses(id)', 'semester': 'VARCHAR(20)',
                             'academic_year': 'VARCHAR(20)', 'identity_status': 'VARCHAR(32)', 'identity_evidence_json': 'TEXT'}
                for name, definition in additions.items():
                    if name not in columns:
                        connection.exec_driver_sql(f'ALTER TABLE course_registrations ADD COLUMN {name} {definition}')
                for index in CourseRegistration.__table__.indexes:
                    index.create(connection, checkfirst=True)
                if 'courses' in tables:
                    _migrate_legacy_registrations(connection, tables)
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def _migrate_legacy_registrations(connection, tables):
    registrations = connection.exec_driver_sql(
        "SELECT id, course_code, selection_code FROM course_registrations WHERE course_id IS NULL AND (identity_status IS NULL OR identity_status='pending')").mappings().all()
    if not registrations:
        return
    by_pair = defaultdict(list)
    teacher_join = ' LEFT JOIN teachers t ON t.teacher_id=c.teacher_id' if 'teachers' in tables else ''
    teacher_select = ', t.name AS teacher_name' if teacher_join else ", '' AS teacher_name"
    for row in connection.exec_driver_sql('SELECT c.id, c.course_code, c.selection_code, c.course_name, c.semester, c.academic_year' + teacher_select + ' FROM courses c' + teacher_join).mappings():
        by_pair[(row['course_code'], row['selection_code'])].append(dict(row))
    by_registration = defaultdict(list)
    if 'lecture_forms' in tables:
        for row in connection.exec_driver_sql('SELECT id, registration_id, course_title, teacher_name FROM lecture_forms WHERE registration_id IS NOT NULL').mappings():
            by_registration[row['registration_id']].append(dict(row))
    for registration in registrations:
        chosen, status, evidence = _choose_legacy_course(by_pair[(registration['course_code'], registration['selection_code'])], by_registration[registration['id']])
        evidence['migration'] = 'registration_course_identity_v1'
        connection.exec_driver_sql(
            'UPDATE course_registrations SET course_id=?,semester=?,academic_year=?,identity_status=?,identity_evidence_json=? WHERE id=?',
            (chosen['id'] if chosen else None, chosen['semester'] if chosen else None,
             chosen['academic_year'] if chosen else None, status,
             json.dumps(evidence, ensure_ascii=False, sort_keys=True), registration['id']))


def _rebuild_legacy_bans(connection):
    """Reflect constraints rather than editing SQL text; verify all original cells."""
    inspector = inspect(connection)
    for table in inspector.get_table_names():
        if any(fk['referred_table'] == 'lecture_bans' for fk in inspector.get_foreign_keys(table)):
            raise RuntimeError('Inbound lecture_bans foreign keys require a reviewed migration')
    temporary = 'lecture_bans__course_code_migration'
    if inspector.has_table(temporary):
        raise RuntimeError('A lecture_bans migration staging table already exists')
    schema_sql = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name='lecture_bans'").scalar()
    metadata = MetaData()
    Table('users', metadata, autoload_with=connection, resolve_fks=False)
    source = Table('lecture_bans', metadata, autoload_with=connection, resolve_fks=False)
    target = source.to_metadata(metadata, name=temporary)
    for constraint in list(target.constraints):
        if isinstance(constraint, ForeignKeyConstraint) and [element.target_fullname for element in constraint.elements] == ['courses.course_code']:
            target.constraints.remove(constraint)
    if 'AUTOINCREMENT' in schema_sql.upper():
        target.dialect_options['sqlite']['autoincrement'] = True
    if 'WITHOUT ROWID' in schema_sql.upper():
        target.dialect_options['sqlite']['with_rowid'] = False
    explicit_schema = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE tbl_name='lecture_bans' AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name").scalars().all()
    sequence = connection.exec_driver_sql("SELECT seq FROM sqlite_sequence WHERE name='lecture_bans'").scalar() if target.dialect_options['sqlite']['autoincrement'] else None
    connection.execute(CreateTable(target))
    quote = connection.dialect.identifier_preparer.quote
    column_sql = ','.join(quote(column.name) for column in source.columns if column.computed is None)
    connection.exec_driver_sql(f'INSERT INTO {quote(temporary)} ({column_sql}) SELECT {column_sql} FROM lecture_bans')
    for left, right in [('lecture_bans', temporary), (temporary, 'lecture_bans')]:
        if connection.exec_driver_sql(f'SELECT * FROM {quote(left)} EXCEPT SELECT * FROM {quote(right)}').first() is not None:
            raise RuntimeError('lecture_bans migration preservation verification failed')
    connection.exec_driver_sql('DROP TABLE lecture_bans')
    connection.exec_driver_sql(f'ALTER TABLE {quote(temporary)} RENAME TO lecture_bans')
    for statement in explicit_schema:
        connection.exec_driver_sql(statement)
    if sequence is not None:
        connection.exec_driver_sql("UPDATE sqlite_sequence SET seq=? WHERE name='lecture_bans'", (sequence,))
