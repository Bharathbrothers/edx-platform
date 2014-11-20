from courseware.models import StudentModule
from courseware.models import GroupedQueries, GroupedQueriesStudents, GroupedQueriesSubqueries
from courseware.models import QueriesSaved, QueriesStudents, QueriesTemporary
from bulk_email.models import Optout
from student.models import CourseEnrollment
#todo: specific imports
from django.contrib.auth.models import User
from data_access_constants import *
from django.db.models import Q
from collections import defaultdict
import time
import random
import datetime

def deleteSavedQuery(queryToDelete):
    groupedQ = GroupedQueries.objects.filter(id=queryToDelete)
    subqueriesToDelete = GroupedQueriesSubqueries.objects.filter(grouped_id=queryToDelete)
    queriesSaved = QueriesSaved.objects.filter(id__in=subqueriesToDelete.values_list("query_id"))
    #needs to be in this specific order
    queriesSaved.delete()
    subqueriesToDelete.delete()
    groupedQ.delete()



def saveQuery(course_id, queries):
    tempQueries = QueriesTemporary.objects.filter(id__in=queries)
    group = GroupedQueries(course_id = course_id, title = "")
    group.save()
    for tempQuery in tempQueries:
        permQuery = QueriesSaved(inclusion=tempQuery.inclusion,
                                 course_id=course_id,
                                 module_state_key=tempQuery.module_state_key,
                                 filter_on=tempQuery.filter_on,
                                 entity_name=tempQuery.entity_name,
                                 type=tempQuery.type)
        permQuery.save()
        relation = GroupedQueriesSubqueries(grouped=group,
                                            query=permQuery)
        relation.save()

    return True


def retrieveSavedQueries(course_id):
    group = GroupedQueries.objects.filter(course_id = course_id)
    relation = GroupedQueriesSubqueries.objects.filter(grouped__in=group)
    queries = QueriesSaved.objects.filter(id__in=relation.values_list('query'))

    if len(group)>0:
        return (group, queries, relation)
    else:
        return ([], [], [])





def make_single_query(course_id, query):
    #store query into QueriesTemporary
    q = QueriesTemporary(inclusion=INCLUSION_MAP.get(query.inclusion),
                         course_id = course_id,
                         module_state_key=query.id,
                         filter_on=query.filter,
                         entity_name=query.entityName,
                         type=query.type)
    q.save()

    if query.type==QUERY_TYPE.SECTION:
        students = get_section_users_s(course_id, query)
    else:
        students = get_problem_users_s(course_id, query)

    for studentid, studentemail in students:
        row = QueriesStudents(query=q, inclusion=INCLUSION_MAP[query.inclusion], student=User.objects.filter(id=studentid)[0])
        row.save()

    #on every 10th query, purge the temporary database
    rand = random.random()
    if rand>.9:
        purgeTemporaryQueries()
    return {q.id:students}

def purgeTemporaryQueries():
    minutes15ago = datetime.datetime.now()-datetime.timedelta(minutes=15)
    oldQueries = QueriesTemporary.objects.filter(created__lt=minutes15ago)
    savedStudents = QueriesStudents.objects.filter(query_id__in=oldQueries.values_list("id"))
    savedStudents.delete()
    oldQueries.delete()



def make_total_query(course_id, existing_queries):
    querySpecific = set()
    if len(existing_queries) !=0:
        queryset= make_existing(existing_queries).values_list('id','email').distinct()
        for row in queryset:
            querySpecific.add((row[0], row[1]))

    return {0:querySpecific}



def get_problem_users_s(course_id, query):
    if query.filter==PROBLEM_FILTERS.OPENED:
        results = open_query(course_id, query)
    elif query.filter==PROBLEM_FILTERS.COMPLETED:
        results = completed_query(course_id, query)
    return results

def get_section_users_s(course_id, query):
    if query.filter == SECTION_FILTERS.OPENED:
        results =  open_query(course_id, query)
    elif query.filter == SECTION_FILTERS.NOT_OPENED:
        results = not_open_query(course_id, query)
    return results


def make_existing(existing_queries):
    query = User.objects
    if existing_queries==None:
        return query

    queryDct = defaultdict(list)
    for q in existing_queries:
        if q=="" or q=="working":
            continue
        inclusionType = QueriesTemporary.objects.filter(id=q)
        result =  QueriesStudents.objects.filter(query_id=q).values_list("student_id",flat=True)
        if inclusionType.exists():
            queryDct[inclusionType[0].inclusion].append(result)

    for notquery in queryDct[INCLUSION_MAP.get(INCLUSION.NOT)]:
        query = query.exclude(id__in=notquery)

    for andquery in queryDct[INCLUSION_MAP.get(INCLUSION.AND)]:
        query = query.filter(id__in=andquery)



    orQuery = User.objects
    qobjs = Q()
    for orq in queryDct[INCLUSION_MAP.get(INCLUSION.OR)]:
        qobjs = qobjs |(Q(id__in=orq))


    return query | orQuery.filter(qobjs)



def not_open_query(course_id, query):
    #starting = make_existing(existing_queries)
    idsInCourse = CourseEnrollment.objects.filter(course_id=course_id, is_active=1).values_list('user_id')
    totalStudents = User.objects.filter(id__in=idsInCourse)
    withoutOpen = totalStudents.exclude(id__in=StudentModule.objects.filter(module_state_key=query.id,
                                                                            course_id = course_id).values_list("student_id"))

    return processResults_negative(course_id, query, withoutOpen)

def not_completed_query(course_id, query):
    idsInCourse = CourseEnrollment.objects.filter(course_id=course_id, is_active=1).values_list('user_id')
    totalStudents = User.objects.filter(id__in=idsInCourse)

    withoutCompleted = totalStudents.exclude(id__in= StudentModule.objects.filter(module_state_key=query.id, course_id = course_id).filter(~Q(grade=None)).values_list("student_id"))

    return processResults_negative(course_id, query, withoutCompleted)

def completed_query(course_id, query):
    #starting = make_existing(existing_queries)
    querySet = StudentModule.objects.filter(module_state_key=query.id, course_id = course_id).filter(~Q(grade=None))
    return processResults_positive(course_id, query, querySet)

def open_query(course_id, query):
    #starting = make_existing(existing_queries)
    queryset = StudentModule.objects.filter(module_state_key=query.id, course_id = course_id)
    return processResults_positive(course_id, query, queryset)

def filter_out_students_negative(course_id,  queryset):
    #first exclude students who have opted out of emails
    withoutOptOut = queryset.exclude(id__in = Optout.objects.all().values_list('user_id'))
    withoutNotEnrolled = withoutOptOut.exclude(id__in=
                              CourseEnrollment.objects.filter(course_id=course_id, is_active=0).values_list('user_id'))
    return withoutNotEnrolled
    """
    filterOut = Optout.objects.filter(course_id=course_id)
    filterout_ids = set([result.user.id for result in filterOut])
    return filterout_ids
    """


def processResults_negative(course_id, query, queryset):
    filteredQuery = filter_out_students_negative(course_id, queryset)
    values = filteredQuery.values_list('id','email').distinct()
    return values

def filter_out_students_positive(course_id,  queryset):
    #first exclude students who have opted out of emails
    withoutOptOut = queryset.exclude(student_id__in = Optout.objects.all().values_list('user_id'))
    withoutNotEnrolled = withoutOptOut.exclude(student_id__in=
                              CourseEnrollment.objects.filter(course_id=course_id, is_active=0).values_list('user_id'))
    return withoutNotEnrolled
    """
    filterOut = Optout.objects.filter(course_id=course_id)
    filterout_ids = set([result.user.id for result in filterOut])
    return filterout_ids
    """


def processResults_positive(course_id, query, queryset):
    filteredQuery = filter_out_students_positive(course_id, queryset)
    values = filteredQuery.values_list('student_id','student__email').distinct()
    return values
