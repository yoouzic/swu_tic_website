"""Explicitly model opening a review before sending a review decision.

These helpers never patch FlaskClient and never invent revisions from database
state. Concurrency tests can retain the returned revision for their stale page.
"""
import re
from html.parser import HTMLParser


def opened_listener_revision(client, form_id):
    """Read the exact hidden revision from an owner's editable form page."""
    response = client.get(f'/user/form/edit/{form_id}')
    assert response.status_code == 200, response.get_data(as_text=True)
    revisions = {}

    class RevisionInputs(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            name = attributes.get('name')
            if tag == 'input' and name in ('expected_form_id', 'expected_form_updated_at'):
                revisions[name] = attributes.get('value', '')

    RevisionInputs().feed(response.get_data(as_text=True))
    assert set(revisions) == {'expected_form_id', 'expected_form_updated_at'}, revisions
    return revisions


def opened_review_revision(client, form_id):
    response = client.get(f'/admin/api/review/form/{form_id}')
    body = response.get_json(silent=True)
    if response.status_code in (401, 403, 404):
        # Negative authorization/ID cases have no page to open; their write
        # request must still exercise the original permission/ID boundary.
        return {}
    if response.status_code == 500 and isinstance(body, dict) and body.get('message', '').startswith('404 Not Found:'):
        # The legacy GET adapter catches NotFound as JSON 500. There still
        # is no opened page or token for this negative missing-ID fixture.
        return {}
    assert response.status_code == 200, response.get_json()
    assert isinstance(body, dict) and body.get('success'), body
    for candidate in (body, body.get('form'), body.get('data')):
        if isinstance(candidate, dict) and all(
            name in candidate for name in ('expected_form_id', 'expected_form_updated_at')
        ):
            return {name: candidate[name] for name in ('expected_form_id', 'expected_form_updated_at')}
    raise AssertionError('review GET did not supply its exact opened revision')


def with_opened_review_revision(client, form_id, payload):
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    nested = result.get('form_data')
    revision_keys = ('expected_form_id', 'expected_form_updated_at')
    if any(key in result or (isinstance(nested, dict) and key in nested) for key in revision_keys):
        # Explicit old, malformed, or incomplete revision fixtures must never
        # be upgraded by a fresh GET immediately before their decision.
        return result
    for key, value in opened_review_revision(client, form_id).items():
        result.setdefault(key, value)
    return result


def post_opened_review(client, url, *, json=None, data=None, **kwargs):
    """Named fixture action for a reviewer who opens this exact form first."""
    match = re.fullmatch(r'/admin/api/review/(?:submit|form|reject)/(\d+)', url)
    assert match, 'Only review decision routes belong in this fixture helper'
    form_id = int(match.group(1))
    if json is not None:
        return client.post(url, json=with_opened_review_revision(client, form_id, json), **kwargs)
    if isinstance(data, dict):
        return client.post(url, data=with_opened_review_revision(client, form_id, data), **kwargs)
    # Malformed raw request envelopes remain unchanged.
    return client.post(url, data=data, **kwargs)
