"""
Urls for sysadmin dashboard feature
"""
# pylint: disable=E1120

from django.conf.urls import patterns, url

from dashboard import sysadmin

urlpatterns = patterns(
    '',
    url(r'^$', sysadmin.Users.as_view(), name="sysadmin"),
    url(r'^courses/?$', sysadmin.Courses.as_view(), name="sysadmin_courses"),
    url(r'^course_tabs/?$', sysadmin.CourseTabs.as_view(), name="sysadmin_course_tabs"),
    url(r'^staffing/?$', sysadmin.Staffing.as_view(), name="sysadmin_staffing"),
    url(r'^gitlogs/?$', sysadmin.GitLogs.as_view(), name="gitlogs"),
    url(r'^gitlogs/(?P<course_id>.+)$', sysadmin.GitLogs.as_view(),
        name="gitlogs_detail"),
    url(r'^task_queue/?$', sysadmin.TaskQueue.as_view(), name="sysadmin_task_queue"),
    url(r'^mgmt_commands/?$', sysadmin.MgmtCommands.as_view(), name="sysadmin_mgmt_commands"),
)
