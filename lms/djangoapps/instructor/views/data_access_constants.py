class Query:
    def __init__(self, type, inclusion, id, filter, entityName):
        self.type= type
        self.inclusion= inclusion
        self.id = id
        self.filter = filter
        self.entityName = entityName

class QueryResults:
    def __init__(self):
        #Need to distinguish between
        #  we have no AND queries
        #  AND query returns nothing
        self.mustInclude = None
        self.canInclude = set()
        self.dontInclude = set()
    def addMustInclude(self, mustInclude):
        if self.mustInclude==None:
            self.mustInclude = set()
            self.mustInclude.update(mustInclude)
        else:
            self.mustInclude.intersection_update(mustInclude)
    def addDontInclude(self, dontInclude):
        self.dontInclude.update(dontInclude)
    def addCanInclude(self, canInclude):
        self.canInclude.update(canInclude)
    #combine 2 queryresults
    def mergeIn(self, queryResults2):
        if self.mustInclude !=None and queryResults2.mustInclude !=None:
            self.mustInclude.intersection_update(queryResults2.mustInclude)
        elif self.mustInclude == None and queryResults2 == None:
            pass
        elif self.mustInclude == None:
            self.mustInclude = queryResults2.mustInclude

        self.canInclude.update(queryResults2.canInclude)
        self.dontInclude.update(queryResults2.dontInclude)
        return self
    def getResults(self):
        if self.mustInclude == None:
            return self.canInclude.difference(self.dontInclude)
        else:
            return self.mustInclude.union(self.canInclude).difference(self.dontInclude)

class INCLUSION:
    AND="AND"
    OR="OR"
    NOT="NOT"
    FILTER="FILTER"

INCLUSION_MAP = {INCLUSION.AND : 'A',
                 INCLUSION.OR : 'O',
                 INCLUSION.NOT : 'N',
                 INCLUSION.FILTER:'F'}

class SECTION_FILTERS:
    OPENED="OPENED"
    COMPLETED="COMPLETED"

class PROBLEM_FILTERS:
    OPENED= SECTION_FILTERS.OPENED
    COMPLETED="COMPLETED"
    SCORE="SCORE"
    NUMBER_PEER_GRADED="NUMBER_PEER_GRADED"

class QUERY_TYPE:
    SECTION="SECTION"
    PROBLEM="PROBLEM"
