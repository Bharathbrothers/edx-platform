"""
Helpers for instructor app.
"""


from xmodule.modulestore.django import modulestore

from courseware.model_data import FieldDataCache
from courseware.module_render import get_module
from pymongo.errors import PyMongoError
from pymongo import MongoClient
from django.conf import settings
from django_comment_client.management_utils import get_mongo_connection_string

FORUMS_MONGO_PARAMS = settings.FORUM_MONGO_PARAMS


class DummyRequest(object):
    """Dummy request"""

    META = {}

    def __init__(self):
        self.session = {}
        self.user = None
        return

    def get_host(self):
        """Return a default host."""
        return 'edx.mit.edu'

    def is_secure(self):
        """Always insecure."""
        return False


def get_module_for_student(student, usage_key, request=None):
    """Return the module for the (student, location) using a DummyRequest."""
    if request is None:
        request = DummyRequest()
        request.user = student

    descriptor = modulestore().get_item(usage_key, depth=0)
    field_data_cache = FieldDataCache([descriptor], usage_key.course_key, student)
    return get_module(student, request, usage_key, field_data_cache)


def collect_course_forums_data(course_id):
    """
    Given a SlashSeparatedCourseKey course_id, return headers and information
    related to course forums usage such as upvotes, downvotes, and number of posts
    """
    try:
        client = MongoClient(get_mongo_connection_string())
        mongodb = client[FORUMS_MONGO_PARAMS['database']]
        new_threads_query = generate_course_forums_query(course_id, "CommentThread")
        new_responses_query = generate_course_forums_query(course_id, "Comment", False)
        new_comments_query = generate_course_forums_query(course_id, "Comment", True)

        new_threads = mongodb.contents.aggregate(new_threads_query)['result']
        new_responses = mongodb.contents.aggregate(new_responses_query)['result']
        new_comments = mongodb.contents.aggregate(new_comments_query)['result']
    except PyMongoError:
        raise

    for entry in new_responses:
        entry['_id']['type'] = "Response"
    results = merge_join_course_forums(new_threads, new_responses, new_comments)
    parsed_results = [
        [
            "{0}-{1}-{2}".format(result['_id']['year'], result['_id']['month'], result['_id']['day']),
            result['_id']['type'],
            result['posts'],
            result['up_votes'],
            result['down_votes'],
            result['net_points'],
        ]
        for result in results
    ]
    header = ['Date', 'Type', 'Number', 'Up Votes', 'Down Votes', 'Net Points']
    return header, parsed_results


from openedx.contrib.stanford.data_forums import merge_join_course_forums
from openedx.contrib.stanford.data_forums import generate_course_forums_query
from openedx.contrib.stanford.data_ora2 import collect_anonymous_ora2_data
from openedx.contrib.stanford.data_ora2 import collect_email_ora2_data
from openedx.contrib.stanford.data_ora2 import collect_ora2_data
from openedx.contrib.stanford.data_ora2 import ora2_data_queries
from openedx.contrib.stanford.data_forums import collect_student_forums_data
from openedx.contrib.stanford.data_forums import generate_student_forums_query
