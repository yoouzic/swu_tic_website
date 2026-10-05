"""Small real authority fixture for tests focused on downstream contracts."""
import pandas as pd
from app.models import Course, SystemSetting, db
from app.services.current_courses import persist_course_mapping
from app.services.listening_assistant_schedule import persist_listening_assistant_entries
from app.services.schedule_snapshots import persist_import_snapshot


def seed_current_schedule(semester='2026-2027-1'):
    df = pd.DataFrame([{'姓名':'课表测试教师', '教师所属学院':'测试学院', '课程名称':'合成课表课程',
                        '星期几':1, '上课节次':'第1-2节', '场地名称':'8-309',
                        '教学班组成':'2026级测试1班', '起始周':'1-16',
                        '学期':semester, '学年':'2026', '课程号':'FIXTURE', '选课号':'1'}])
    batches = persist_import_snapshot(df, 'synthetic-fixture.xlsx', 'f' * 64)
    persist_listening_assistant_entries(df, batches)
    course = Course(course_code='FIXTURE', selection_code='1', course_name='合成课表课程',
                    semester=semester, class_time='星期一 第1-2节', class_location='8-309')
    db.session.add(course)
    db.session.flush()
    persist_course_mapping(batches[0], [course.id])
    db.session.commit()
    SystemSetting.set('teaching_current_semester', semester)
    return batches[0]
