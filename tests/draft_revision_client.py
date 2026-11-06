"""Browser-like draft revision tracking for legacy workflow fixtures.

Only an actual successful response supplies the next revision. Tests can copy
an already observed token to another tab; writes never fetch a fresh one.
Protocol rejection tests should keep using Flask's unwrapped test client.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from flask.testing import FlaskClient
from werkzeug.datastructures import Headers


class _DraftRevisionInputParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.revision = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('name') == 'expected_draft_revision':
            self.revision = attrs.get('value') or None


class DraftRevisionClient(FlaskClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._draft_revisions = {}

    def _draft_actor(self):
        with self.session_transaction() as session:
            return session.get('user_id')

    @property
    def draft_revision(self):
        return self._draft_revisions.get(self._draft_actor())

    def set_draft_revision(self, revision):
        """Model a new tab sharing a revision it has already observed."""
        self._draft_revisions[self._draft_actor()] = revision

    def observe_draft_revision(self):
        response = self.get('/user/api/lecture_form_draft')
        assert response.status_code == 200, response.get_data(as_text=True)
        assert self.draft_revision, response.get_json()
        return self.draft_revision

    def open(self, *args, **kwargs):
        path = args[0] if args and isinstance(args[0], str) else kwargs.get('path', '')
        path = urlsplit(path).path
        method = kwargs.get('method', 'GET').upper()
        headers = Headers(kwargs.get('headers'))
        json_body = kwargs.get('json') or {}
        data = kwargs.get('data') or {}
        explicit = ('X-Lecture-Draft-Revision' in headers or
                    isinstance(json_body, dict) and 'expected_draft_revision' in json_body or
                    hasattr(data, '__contains__') and 'expected_draft_revision' in data)
        changes_draft = (
            path == '/user/api/lecture_form_draft' and method in {'PUT', 'DELETE'} or
            method == 'POST' and re.fullmatch(r'/user/api/site-capture(?:/\d+/(?:confirm|resume))?', path) or
            method == 'PATCH' and re.fullmatch(r'/user/api/site-capture/\d+', path)
        )
        is_submission = path == '/user/submit_form' and method == 'POST'
        actor = self._draft_actor()
        revision = self._draft_revisions.get(actor)
        if not explicit and revision is not None:
            if changes_draft:
                headers['X-Lecture-Draft-Revision'] = revision
                kwargs['headers'] = headers
            elif is_submission:
                copied = data.copy()
                copied['expected_draft_revision'] = revision
                kwargs['data'] = copied
        response = super().open(*args, **kwargs)
        body = response.get_json(silent=True)
        if 200 <= response.status_code < 300 and isinstance(body, dict) and body.get('success'):
            observed = body.get('draft_revision')
            if isinstance(observed, str) and observed:
                self._draft_revisions[actor] = observed
        # A successful new-form navigation renders its baseline in the hidden
        # input. Error POSTs keep the submitted token, and edit-page `none` is
        # unrelated to the ordinary draft, so neither may advance this cache.
        if method == 'GET' and path == '/user/submit_form' and response.status_code == 200:
            parser = _DraftRevisionInputParser()
            parser.feed(response.get_data(as_text=True))
            if parser.revision:
                self._draft_revisions[actor] = parser.revision
        return response
