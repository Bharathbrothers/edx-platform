"""
Methods associated with accessing models related to email list widget on instructor dashboard
"""
from courseware.models import StudentModule
from courseware.models import GroupedQueries, GroupedQueriesSubqueries, GroupedTempQueriesSubqueries
from courseware.models import QueriesSaved, QueriesStudents, QueriesTemporary
from bulk_email.models import Optout
from student.models import CourseEnrollment
from django.contrib.auth.models import User
from instructor.views.data_access_constants import INCLUSION_MAP, QueryType, ProblemFilters, SectionFilters
from instructor.views.data_access_constants import Inclusion, QueryStatus
from instructor.views.data_access_constants import REVERSE_INCLUSION_MAP, Query
from instructor.views.data_access_constants import DatabaseFields
from django.db.models import Q
from collections import defaultdict
import random
import datetime
from celery import task
from instructor_task.tasks_helper import EmailWidgetTask


def delete_saved_query(query_to_delete):
    """
    Deletes a specified grouped query along with its saved queries
    """
    grouped_query = GroupedQueries.objects.filter(id=query_to_delete)
    subqueries_to_delete = GroupedQueriesSubqueries.objects.filter(grouped_id=query_to_delete)
    queries_saved = QueriesSaved.objects.filter(id__in=subqueries_to_delete.values_list(DatabaseFields.QUERY_ID))
    #Needs to be in this specific order for deletion
    queries_saved.delete()
    subqueries_to_delete.delete()
    grouped_query.delete()


def delete_temporary_query(query_to_delete):
    """
    Removes a single query from the temporary queries
    """
    queries_to_delete = QueriesTemporary.objects.filter(id=query_to_delete)
    saved_students = QueriesStudents.objects.filter(query_id=query_to_delete)
    saved_students.delete()
    queries_to_delete.delete()


def delete_bulk_temporary_query(query_to_delete):
    """
    Removes many queries from the temporary query table
    """
    if len(query_to_delete) == 0 or query_to_delete[0] == u'':
        return
    query_set = set(query_to_delete)
    temp_query = QueriesTemporary.objects.filter(id__in=query_set)
    saved_students = QueriesStudents.objects.filter(query_id__in=query_set)
    saved_students.delete()
    temp_query.delete()


def save_query(course_id, queries):
    """
    Makes a new grouped query by saving the individual subqueries and then associating them to a grouped query
    """
    temp_queries = QueriesTemporary.objects.filter(id__in=queries)
    group = GroupedQueries(course_id=course_id, title="")
    group.save()
    for temp_query in temp_queries:
        perm_query = QueriesSaved(
            inclusion=temp_query.inclusion,
            course_id=course_id,
            module_state_key=temp_query.module_state_key,
            filter_on=temp_query.filter_on,
            entity_name=temp_query.entity_name,
            type=temp_query.type,
        )
        perm_query.save()
        relation = GroupedQueriesSubqueries(grouped=group, query=perm_query)
        relation.save()
    return True


@task(base=EmailWidgetTask)  # pylint: disable=not-callable
def get_group_query_students(course_id, group_id):
    """
    Asynchronously makes the subqueries for a group and then aggregates them once the subqueries are finished.
    """
    _group, queries, _relation = get_saved_queries(course_id, group_id)
    chained_students = (make_subqueries.si(course_id, group_id, queries) |
                        retrieve_grouped_query.si(course_id, group_id))().get()
    return chained_students


@task(base=EmailWidgetTask)  # pylint: disable=not-callable
def retrieve_grouped_query(_course_id, group_id):
    """
    For a grouped query where its subqueries have already been executed, return the students associated
    """
    subqueries = GroupedTempQueriesSubqueries.objects.filter(grouped_id=group_id).distinct()
    existing = []
    for sub in subqueries:
        existing.append(sub.query_id)
    student_info = make_existing_query(existing)
    projected_student_info = student_info.values_list(
        DatabaseFields.ID,
        DatabaseFields.EMAIL,
        DatabaseFields.PROFILE_NAME,
    )
    distinct_students = projected_student_info.distinct()
    students = [
        {"id": triple[0], "email": triple[1], "profileName": triple[2]}
        for triple in distinct_students
    ]
    subqueries.delete()
    return students


@task(base=EmailWidgetTask)  # pylint: disable=not-callable
def make_subqueries(course_id, group_id, queries):
    """
    Issues the subqueries associated with a group query
    """
    for query in queries:
        query = Query(
            query.type,
            REVERSE_INCLUSION_MAP[query.inclusion],
            course_id.make_usage_key(query.module_state_key.block_type, query.module_state_key.block_id),
            query.filter_on,
            query.entity_name,
        )
        make_single_query.apply_async(args=(course_id, query, group_id))


def get_saved_queries(course_id, specific_group=None):
    """
    Get existing saved queries associated with a given course
    """
    if specific_group:
        group = GroupedQueries.objects.filter(course_id=course_id, id=specific_group)
    else:
        group = GroupedQueries.objects.filter(course_id=course_id)
    if len(group) == 0:
        return ([], [], [])
    relation = GroupedQueriesSubqueries.objects.filter(grouped__in=group)
    queries = QueriesSaved.objects.filter(id__in=relation.values_list(DatabaseFields.QUERY))
    return (group, queries, relation)


def get_temp_queries(course_id):
    """
    Get temporary queries associated with a course
    """
    queries_temp = QueriesTemporary.objects.filter(course_id=course_id)
    return queries_temp


@task(base=EmailWidgetTask)  # pylint: disable=not-callable
def make_single_query(course_id, query, associate_group=None):
    """
    Make a single query for student information
    """
    temp_query = QueriesTemporary(
        inclusion=INCLUSION_MAP.get(query.inclusion),
        course_id=course_id,
        module_state_key=query.entity_id,
        filter_on=query.filter,
        entity_name=query.entity_name,
        type=query.type,
        done=False,
    )
    temp_query.save()
    try:
        if query.type == QueryType.SECTION:
            students = get_section_users(course_id, query)
        else:
            students = get_problem_users(course_id, query)
        bulk_queries = []
        for student_id, dummy0 in students:
            row = QueriesStudents(query=temp_query,
                                  inclusion=INCLUSION_MAP[query.inclusion],
                                  student=User.objects.filter(id=student_id)[0],
                                  )
            bulk_queries.append(row)
        QueriesStudents.objects.bulk_create(bulk_queries)
        QueriesTemporary.objects.filter(id=temp_query.id).update(done=True)  # pylint: disable=no-member
        if associate_group is not None:
            grouped_temp = GroupedTempQueriesSubqueries(
                grouped_id=associate_group,
                query_id=temp_query.id,   # pylint: disable=no-member
            )
            grouped_temp.save()

    except Exception as error:
        QueriesTemporary.objects.filter(id=temp_query.id).update(done=None)  # pylint: disable=no-member
        raise(error)

    #on roughly every 10th query, purge the temporary queries
    rand = random.random()
    if rand > .9:
        purge_temporary_queries()
    return {temp_query.id: students}  # pylint: disable=no-member


def purge_temporary_queries():
    """
    Delete queries made more than 30 minutes ago along with the saved students from those queries
    """
    minutes30ago = datetime.datetime.now() - datetime.timedelta(minutes=30)
    old_queries = QueriesTemporary.objects.filter(created__lt=minutes30ago)
    saved_students = QueriesStudents.objects.filter(query_id__in=old_queries.values_list(DatabaseFields.ID))
    saved_students.delete()
    old_queries.delete()


@task(base=EmailWidgetTask)  # pylint: disable=not-callable
def make_total_query(existing_queries):
    """
    Given individual queries that have already been made , aggregate students associated with those queries
    """
    aggregate_existing = set()
    if len(existing_queries) != 0:
        queryset = make_existing_query(existing_queries).values_list(
            DatabaseFields.ID,
            DatabaseFields.EMAIL,
            DatabaseFields.PROFILE_NAME,
        ).distinct()
        for row in queryset:
            aggregate_existing.add((row[0], row[1], row[2]))
    return aggregate_existing


def get_problem_users(course_id, query):
    """
    Gets Students filtered on specified problem-specific query criteria
    """
    results = []
    if query.filter == ProblemFilters.OPENED:
        results = open_query(course_id, query)
    elif query.filter == ProblemFilters.COMPLETED:
        results = completed_query(course_id, query)
    elif query.filter == ProblemFilters.NOT_OPENED:
        results = not_open_query(course_id, query)
    elif query.filter == ProblemFilters.NOT_COMPLETED:
        results = not_completed_query(course_id, query)
    return results


def get_section_users(course_id, query):
    """
    Gets Students filtered on specified section-specific query criteria
    """
    if query.filter == SectionFilters.OPENED:
        results = open_query(course_id, query)
    elif query.filter == SectionFilters.NOT_OPENED:
        results = not_open_query(course_id, query)
    return results


def make_existing_query(existing_queries):
    """
    Aggregates single queries in a group into one unified set of students
    """
    query = User.objects
    if existing_queries is None:
        return None

    query_dct = defaultdict(list)

    for existing_query in existing_queries:
        if existing_query == "" or existing_query == QueryStatus.WORKING:
            continue
        inclusion_type = QueriesTemporary.objects.filter(id=existing_query)
        result = QueriesStudents.objects.filter(query_id=existing_query).values_list(DatabaseFields.STUDENT_ID,
                                                                                     flat=True,
                                                                                     )
        if inclusion_type.exists():
            query_dct[inclusion_type[0].inclusion].append(result)

    for not_query in query_dct[INCLUSION_MAP.get(Inclusion.NOT)]:
        query = query.exclude(id__in=not_query)

    for and_query in query_dct[INCLUSION_MAP.get(Inclusion.AND)]:
        query = query.filter(id__in=and_query)

    or_query = User.objects
    qobjs = Q()
    for orq in query_dct[INCLUSION_MAP.get(Inclusion.OR)]:
        qobjs = qobjs | (Q(id__in=orq))
    if len(query_dct[INCLUSION_MAP.get(Inclusion.NOT)]) == 0 and len(query_dct[INCLUSION_MAP.get(Inclusion.AND)]) == 0:
        return or_query.filter(qobjs)
    else:
        return query | or_query.filter(qobjs)


def not_open_query(course_id, query):
    """
    Specific db query for sections or problems that have not been opened
    """
    ids_in_course = CourseEnrollment.objects.filter(course_id=course_id,
                                                    is_active=1,
                                                    ).values_list(DatabaseFields.USER_ID)
    total_students = User.objects.filter(id__in=ids_in_course)
    without_open = total_students.exclude(id__in=
    StudentModule.objects.filter(
        module_state_key=query.entity_id,
        course_id=course_id).values_list(DatabaseFields.STUDENT_ID),
    )
    return process_results(course_id, without_open, DatabaseFields.ID, DatabaseFields.EMAIL)


def not_completed_query(course_id, query):
    """
    Specific db query for sections or problems that are not completed
    """
    ids_in_course = CourseEnrollment.objects.filter(course_id=course_id,
                                                    is_active=1,
                                                    ).values_list(DatabaseFields.USER_ID)
    total_students = User.objects.filter(id__in=ids_in_course)
    without_completed = total_students.exclude(id__in=
    StudentModule.objects.filter(
        module_state_key=query.entity_id,
        course_id=course_id).filter(~Q(grade=None)).values_list(DatabaseFields.STUDENT_ID),
    )
    return process_results(course_id, without_completed, DatabaseFields.ID, DatabaseFields.EMAIL)


def completed_query(course_id, query):
    """
    Specific db query for sections or problems that are completed
    """
    queryset = StudentModule.objects.filter(module_state_key=query.entity_id,
                                            course_id=course_id,
                                            ).filter(~Q(grade=None))
    return process_results(course_id, queryset, DatabaseFields.STUDENT_ID, DatabaseFields.STUDENT_EMAIL)


def open_query(course_id, query):
    """
    Specific db query for sections or problems that are opened
    """
    queryset = StudentModule.objects.filter(module_state_key=query.entity_id, course_id=course_id)
    return process_results(course_id, queryset, DatabaseFields.STUDENT_ID, DatabaseFields.STUDENT_EMAIL)


def process_results(course_id, queryset, id_field, email_field):
    """
    Handles any intermediate filtering between specific query criteria and returning to the user
    """
    filtered_query = filter_out_students_negative(course_id, queryset)
    values = filtered_query.values_list(id_field, email_field).distinct()
    return values


def filter_out_students_negative(course_id, queryset):
    """
    Exclude students who have opted out of emails and exclude students who are not active in the class
    Used specifically for positive queries i.e. have completed/opened because of query structure
    """
    without_opt_out = queryset.exclude(id__in=Optout.objects.all().values_list(DatabaseFields.USER_ID))
    without_not_enrolled = without_opt_out.exclude(
        id__in=CourseEnrollment.objects.filter(course_id=course_id, is_active=0).values_list(DatabaseFields.USER_ID))
    return without_not_enrolled


def filter_out_students_positive(course_id, queryset):
    """
    Exclude students who have opted out of emails and exclude students who are not active in the class
    Used specifically for negative queries i.e. not completed/not opened because of query structure
    """
    without_opt_out = queryset.exclude(student_id__in=Optout.objects.all().values_list(DatabaseFields.USER_ID))
    without_not_enrolled = without_opt_out.exclude(
        student_id__in=CourseEnrollment.objects.filter(course_id=course_id, is_active=0).
        values_list(DatabaseFields.USER_ID))
    return without_not_enrolled
