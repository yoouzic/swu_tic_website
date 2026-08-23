from pathlib import Path
import unittest


COURSE_TEMPLATE = Path("app/templates/admin/course_feedback_management.html")
REVIEW_TEMPLATE = Path("app/templates/admin/review_forms.html")
STATISTICS_TEMPLATE = Path("app/templates/admin/statistics.html")
PEOPLE_TEMPLATE = Path("app/templates/admin/manage_departments.html")
REVIEW_BLUEPRINT = Path("app/blueprints/admin/review.py")
STATISTICS_BLUEPRINT = Path("app/blueprints/admin/statistics.py")
STYLE = Path("app/static/css/style.css")
AUTH_BLUEPRINT = Path("app/blueprints/auth.py")
REVIEW_QUEUE_SCRIPT = Path("app/static/js/review-queue.js")


class ThirdPassUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.course = COURSE_TEMPLATE.read_text(encoding="utf-8")
        cls.review = REVIEW_TEMPLATE.read_text(encoding="utf-8")
        cls.statistics = STATISTICS_TEMPLATE.read_text(encoding="utf-8")
        cls.people = PEOPLE_TEMPLATE.read_text(encoding="utf-8")
        cls.review_source = REVIEW_BLUEPRINT.read_text(encoding="utf-8")
        cls.statistics_source = STATISTICS_BLUEPRINT.read_text(encoding="utf-8")
        cls.style = STYLE.read_text(encoding="utf-8")
        cls.auth = AUTH_BLUEPRINT.read_text(encoding="utf-8")
        cls.review_queue_script = REVIEW_QUEUE_SCRIPT.read_text(encoding="utf-8")

    def test_course_has_one_filter_source_and_two_responsive_presentations(self):
        self.assertEqual(self.course.count('id="searchInput"'), 1)
        self.assertIn('id="courseAdvancedFilters"', self.course)
        self.assertIn('id="courseFilterSummary"', self.course)
        self.assertIn('id="courseDesktopTable"', self.course)
        self.assertIn('id="courseSummaryList"', self.course)
        self.assertIn('function renderCoursePresentations(', self.course)
        self.assertIn('@media (min-width: 1200px)', self.style)
        self.assertIn('.course-summary-list', self.style)

    def test_course_selection_is_shared_across_both_presentations(self):
        self.assertIn("toggleGroupSelection('${groupKey}', this.checked)", self.course)
        self.assertIn('function syncCourseSelectionControls()', self.course)
        self.assertIn('querySelectorAll(`input[data-group-key="${groupKey}"]`)', self.course)
        self.assertIn('courseGroup._domToken = `course-group-${groupIndex}`', self.course)
        self.assertIn('currentCoursesData.find(course => course._domToken === groupKey)', self.course)
        self.assertEqual(self.course.count('function applyFiltersAndSort()'), 1)
        self.assertIn('let cellContent = escapeHtml(courseGroup[columnKey] || \'-\')', self.course)
        self.assertIn('data-course-detail-type="teacher"', self.course)
        self.assertIn('data-course-detail-type="venue"', self.course)
        self.assertEqual(self.course.count("document.addEventListener('click', handleCourseDetailLinkClick)"), 1)
        self.assertIn('function handleCourseDetailLinkClick(event)', self.course)
        self.assertNotIn("onclick=\"showTeacherDetail('${record.teacher_id}')\"", self.course)
        self.assertNotIn("onclick=\"showVenueDetail('${record.venue_id}')\"", self.course)
        self.assertIn("${escapeHtml(teacher.name || '-')}", self.course)
        self.assertIn("${escapeHtml(venue.name || '-')}", self.course)
        self.assertIn("${escapeHtml(course.teacher_name || '-')}", self.course)
        self.assertIn('id="courseBatchToolbar" hidden', self.course)

    def test_review_uses_server_authoritative_reviewability_and_compact_actions(self):
        self.assertIn('form.id == latest_form.id', self.review_source)
        self.assertIn('and can_review_status(session[\'user_id\'], form.status, permission=permission)', self.review_source)
        self.assertIn('latestForm.can_review === true', self.review)
        self.assertIn('version.can_review === true', self.review)
        self.assertNotIn('function canReviewForm(status)', self.review)
        self.assertIn('class="review-item__primary-action', self.review)
        self.assertIn('class="dropdown review-item__menu"', self.review)
        self.assertEqual(self.review.count('class="review-item__meta--secondary"'), 2)
        self.assertIn('@media (max-width: 959.98px) { .review-item__meta--secondary { display: none; } }', self.style)
        self.assertIn("activeElement === document.body", self.review_queue_script)
        self.assertIn("activeElement?.closest?.('.modal.show, .offcanvas.show')", self.review_queue_script)
        self.assertIn('window.requestAnimationFrame(() => toggle.focus())', self.review_queue_script)

    def test_review_queue_load_success_is_accessible_without_persistent_layout(self):
        self.assertIn("setReviewQueueFeedback(summaryMessage, 'success', true)", self.review)
        self.assertIn('.activity-feedback--compact', self.style)
        self.assertIn('aria-live="polite"', self.review)

    def test_statistics_has_complete_subnav_metric_layout_and_data_states(self):
        subnav = Path('app/templates/partials/_statistics_nav.html').read_text(encoding='utf-8')
        self.assertIn('aria-current="page"', subnav)
        self.assertIn('statistics-metrics', self.statistics)
        self.assertIn('visualization_state', self.statistics)
        self.assertIn("visualization_state='NO_DATA'", self.statistics_source)
        self.assertIn("visualization_state='INSUFFICIENT_DATA'", self.statistics_source)
        self.assertIn("visualization_state='DATA_READY'", self.statistics_source)
        self.assertIn('.statistics-metrics', self.style)

    def test_statistics_filters_details_and_export_share_registered_contracts(self):
        self.assertIn('def _statistics_query(', self.statistics_source)
        self.assertIn("@admin_bp.route('/export_statistics')", self.statistics_source)
        self.assertIn("fetch(`/admin/api/statistics_detail?${params.toString()}`)", self.statistics)
        self.assertIn("link.href = `/admin/export_statistics?${params.toString()}`", self.statistics)
        self.assertNotIn("innerHTML = data.html", self.statistics)
        self.assertIn('data-statistics-group=', self.statistics)
        self.assertIn('statistics-detail-pagination', self.statistics)
        self.assertIn('loadStatisticsDetails(pagination.page + 1)', self.statistics)
        self.assertIn('def _safe_statistics_excel_text(', self.statistics_source)

    def test_people_empty_department_actions_and_contact_tools_are_disclosed(self):
        self.assertIn('department-card__menu', self.people)
        self.assertIn('data-department-action="edit"', self.people)
        self.assertIn('data-department-action="disband"', self.people)
        self.assertIn('${escapePeopleHtml(dept.name)}', self.people)
        self.assertNotIn("onclick=\"disbandDepartment(${dept.id}, '${dept.name}')\"", self.people)
        self.assertIn("event.target.closest?.('.department-card__menu')", self.people)
        self.assertIn("!document.activeElement?.closest?.('.modal.show, .offcanvas.show')", self.people)
        self.assertIn('id="contactsTools"', self.people)
        self.assertIn('class="people-contact-tools ', self.people)
        self.assertIn("window.location.hash === '#contacts-import'", self.people)
        self.assertIn("window.location.hash === '#contacts-export'", self.people)

    def test_login_success_does_not_duplicate_dashboard_greeting(self):
        self.assertNotIn("flash(f'欢迎回来，{user.name}！', 'success')", self.auth)

    def test_touched_pages_use_bootstrap_five_badge_tokens(self):
        for template in (self.review, self.statistics):
            for legacy in ('badge-warning', 'badge-success', 'badge-danger', 'badge-light'):
                self.assertNotIn(legacy, template)


if __name__ == "__main__":
    unittest.main()
