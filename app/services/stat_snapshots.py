# -*- coding: utf-8 -*-
"""Statistics snapshot persistence/ownership domain service.

This module deliberately returns domain results/error codes, not Flask
responses.  HTTP adapters in the admin blueprint translate them into the same
Chinese JSON/messages that existed before this refactor.
"""
import json

from app.models import StatisticsSnapshot, User, db
from app.utils.manage_permissions import get_user_manage_permission


def snapshot_base_query(snapshot_type, current_user_id):
    query = StatisticsSnapshot.query.filter_by(snapshot_type=snapshot_type)
    if get_user_manage_permission(current_user_id) != '超级管理员':
        query = query.filter_by(created_by=current_user_id)
    return query.order_by(StatisticsSnapshot.created_at.desc(), StatisticsSnapshot.id.desc())


def build_snapshot_list_items(snapshot_type, current_user_id):
    records = snapshot_base_query(snapshot_type, current_user_id).all()
    creator_ids = {record.created_by for record in records if record.created_by}
    creators = {
        user.id: user
        for user in User.query.filter(User.id.in_(list(creator_ids))).all()
    } if creator_ids else {}
    items = []
    for record in records:
        try:
            filters_data = json.loads(record.filters_json or '{}')
        except Exception:
            filters_data = {}
        creator = creators.get(record.created_by)
        items.append({
            'id': record.id,
            'title': record.title,
            'created_at': record.created_at.strftime('%Y-%m-%d %H:%M:%S') if record.created_at else '',
            'creator_name': creator.name if creator else '',
            'range_start': filters_data.get('start_date', ''),
            'range_end': filters_data.get('end_date', ''),
            'period_label': filters_data.get('period_label', ''),
            'scope_label': filters_data.get('scope_label', ''),
        })
    return items


def create_statistics_snapshot(snapshot_type, title, filters_data, payload_data, current_user_id):
    record = StatisticsSnapshot(
        snapshot_type=snapshot_type,
        title=title,
        filters_json=json.dumps(filters_data, ensure_ascii=False),
        payload_json=json.dumps(payload_data, ensure_ascii=False),
        created_by=current_user_id
    )
    db.session.add(record)
    db.session.commit()
    return record


def load_snapshot_payload(record):
    try:
        filters_data = json.loads(record.filters_json or '{}')
    except Exception:
        filters_data = {}
    try:
        payload_data = json.loads(record.payload_json or '{}')
    except Exception:
        payload_data = {}
    return filters_data, payload_data


def find_snapshot_record(snapshot_id, snapshot_type):
    return StatisticsSnapshot.query.filter_by(id=snapshot_id, snapshot_type=snapshot_type).first()


def can_access_snapshot(record, current_user_id):
    if get_user_manage_permission(current_user_id) == '超级管理员':
        return True
    return record.created_by == current_user_id
