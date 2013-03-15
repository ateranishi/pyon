#!/usr/bin/env python

__author__ = 'Thomas R. Lennan, Michael Meisinger'
__license__ = 'Apache 2.0'


import couchdb
from couchdb.client import ViewResults, Row
from couchdb.http import PreconditionFailed, ResourceConflict, ResourceNotFound
from uuid import uuid4

from pyon.datastore.couchdb.couch_common import END_MARKER
from pyon.datastore.couchdb.base_store import CouchDataStore

from pyon.core.bootstrap import get_obj_registry, CFG
from pyon.core.exception import BadRequest, Conflict, NotFound
from pyon.core.object import IonObjectBase, IonObjectSerializer, IonObjectDeserializer
from pyon.datastore.datastore import DataStore
from pyon.datastore.couchdb.views import get_couchdb_view_designs
from pyon.ion.identifier import create_unique_association_id
from pyon.ion.resource import CommonResourceLifeCycleSM
from pyon.util.log import log
from pyon.util.arg_check import validate_is_instance
from pyon.util.containers import get_ion_ts


class CouchPyonDataStore(CouchDataStore):
    """
    Pyon specialization of CouchDB datastore.
    """
    def __init__(self, datastore_name=None, profile=None, config=None):
        log.debug('__init__(datastore_name=%s, profile=%s, config=%s)', datastore_name, profile, config)
        if not config:
            config = CFG.get_safe("server.couchdb")

        super(CouchPyonDataStore, self).__init__(datastore_name=datastore_name, config=config, newlog=log)

        # Datastore specialization (views)
        self.profile = profile or DataStore.DS_PROFILE.BASIC

        # IonObject Serializers
        self._io_serializer = IonObjectSerializer()
        self._io_deserializer = IonObjectDeserializer(obj_registry=get_obj_registry())

    def create_datastore(self, datastore_name="", create_indexes=True, profile=None):
        super(CouchPyonDataStore, self).create_datastore(datastore_name=datastore_name)

        if create_indexes:
            profile = profile or self.profile
            log.info('Creating indexes for datastore %s with profile=%s' % (datastore_name, profile))
            self._define_views(datastore_name, profile)

    # -------------------------------------------------------------------------
    # Couch document operations

    def create(self, obj, object_id=None, attachments=None, datastore_name=""):
        """
        Converts ion objects to python dictionary before persisting them using the optional
        suggested identifier and creates attachments to the object.
        Returns an identifier and revision number of the object
        """
        if not isinstance(obj, IonObjectBase):
            raise BadRequest("Obj param is not instance of IonObjectBase")

        return self.create_doc(self._ion_object_to_persistence_dict(obj),
                               object_id=object_id, datastore_name=datastore_name,
                               attachments=attachments)

    def create_mult(self, objects, object_ids=None):
        if any([not isinstance(obj, IonObjectBase) for obj in objects]):
                raise BadRequest("Obj param is not instance of IonObjectBase")

        return self.create_doc_mult([self._ion_object_to_persistence_dict(obj) for obj in objects], object_ids)


    def update(self, obj, datastore_name=""):
        if not isinstance(obj, IonObjectBase):
            raise BadRequest("Obj param is not instance of IonObjectBase")

        return self.update_doc(self._ion_object_to_persistence_dict(obj))

    def update_mult(self, objects):
        if any([not isinstance(obj, IonObjectBase) for obj in objects]):
            raise BadRequest("Obj param is not instance of IonObjectBase")

        return self.update_doc_mult([self._ion_object_to_persistence_dict(obj) for obj in objects])


    def read(self, object_id, rev_id="", datastore_name=""):
        if not isinstance(object_id, str):
            raise BadRequest("Object id param is not string")

        doc = self.read_doc(object_id, rev_id, datastore_name)
        obj = self._persistence_dict_to_ion_object(doc)

        return obj

    def read_mult(self, object_ids, datastore_name=""):
        if any([not isinstance(object_id, str) for object_id in object_ids]):
            raise BadRequest("Object ids are not string: %s" % str(object_ids))

        docs = self.read_doc_mult(object_ids, datastore_name)
        obj_list = [self._persistence_dict_to_ion_object(doc) for doc in docs]

        return obj_list


    def delete(self, obj, datastore_name="", del_associations=False):
        if not isinstance(obj, IonObjectBase) and not isinstance(obj, str):
            raise BadRequest("Obj param is not instance of IonObjectBase or string id")
        if type(obj) is str:
            self.delete_doc(obj, datastore_name=datastore_name, del_associations=del_associations)
        else:
            if '_id' not in obj:
                raise BadRequest("Doc must have '_id'")
            if '_rev' not in obj:
                raise BadRequest("Doc must have '_rev'")
            self.delete_doc(self._ion_object_to_persistence_dict(obj),
                            datastore_name=datastore_name, del_associations=del_associations)

    def delete_doc(self, doc, datastore_name="", del_associations=False):
        doc_id = doc if type(doc) is str else doc["_id"]
        if del_associations:
            assoc_ids = self.find_associations(anyside=doc_id, id_only=True)
            self.delete_doc_mult(assoc_ids)
            log.debug("Deleted %n associations for object %s", len(assoc_ids), doc_id)

        elif self._is_in_association(doc_id, datastore_name):
            log.warn("Deleting object %s that still has associations" % doc_id)

        super(CouchPyonDataStore, self).delete_doc(doc, datastore_name=datastore_name)

    def delete_mult(self, object_ids, datastore_name=None):
        return self.delete_doc_mult(object_ids, datastore_name)


    # -------------------------------------------------------------------------
    # Association operations

    def create_association(self, subject=None, predicate=None, obj=None, assoc_type=None):
        """
        Create an association between two IonObjects with a given predicate
        """
        #if assoc_type:
        #if assoc_type:
        #    raise BadRequest("assoc_type deprecated")
        if not (subject and predicate and obj):
            raise BadRequest("Association must have all elements set")
        if type(subject) is str:
            subject_id = subject
            subject = self.read(subject_id)
            subject_type = subject._get_type()
        else:
            if "_id" not in subject or "_rev" not in subject:
                raise BadRequest("Subject id or rev not available")
            subject_id = subject._id
            subject_type = subject._get_type()

        if type(obj) is str:
            object_id = obj
            obj = self.read(object_id)
            object_type = obj._get_type()
        else:
            if "_id" not in obj or "_rev" not in obj:
                raise BadRequest("Object id or rev not available")
            object_id = obj._id
            object_type = obj._get_type()

        # Check that subject and object type are permitted by association definition
        # Note: Need import here, so that import orders are not screwed up
        from pyon.core.registry import getextends
        from pyon.ion.resource import Predicates
        from pyon.core.bootstrap import IonObject

        try:
            pt = Predicates.get(predicate)
        except AttributeError:
            raise BadRequest("Predicate unknown %s" % predicate)
        if not subject_type in pt['domain']:
            found_st = False
            for domt in pt['domain']:
                if subject_type in getextends(domt):
                    found_st = True
                    break
            if not found_st:
                raise BadRequest("Illegal subject type %s for predicate %s" % (subject_type, predicate))
        if not object_type in pt['range']:
            found_ot = False
            for rant in pt['range']:
                if object_type in getextends(rant):
                    found_ot = True
                    break
            if not found_ot:
                raise BadRequest("Illegal object type %s for predicate %s" % (object_type, predicate))

        # Finally, ensure this isn't a duplicate
        assoc_list = self.find_associations(subject, predicate, obj, id_only=False)
        if len(assoc_list) != 0:
            assoc = assoc_list[0]
            raise BadRequest("Association between %s and %s with predicate %s already exists" % (subject, obj, predicate))

        assoc = IonObject("Association",
            s=subject_id, st=subject_type,
            p=predicate,
            o=object_id, ot=object_type,
            ts=get_ion_ts())
        self._count(_create_assoc=1)
        return self.create(assoc, create_unique_association_id())

    def delete_association(self, association=''):
        """
        Delete an association between two IonObjects
        @param association  Association object, association id or 3-list of [subject, predicate, object]
        """
        if type(association) in (list, tuple) and len(association) == 3:
            subject, predicate, obj = association
            assoc_id_list = self.find_associations(subject=subject, predicate=predicate, obj=obj, id_only=True)
            success = True
            for aid in assoc_id_list:
                success = success and self.delete(aid)
            self._count(_delete_assoc=1)
            return success
        else:
            self._count(_delete_assoc=1)
            return self.delete(association)


    # -------------------------------------------------------------------------
    # View operations

    def _is_in_association(self, obj_id, datastore_name=""):
        log.debug("_is_in_association(%s)", obj_id)
        if not obj_id:
            raise BadRequest("Must provide object id")
        ds, datastore_name = self._get_datastore(datastore_name)

        assoc_ids = self.find_associations(anyside=obj_id, id_only=True, limit=1)
        if assoc_ids:
            log.debug("Object found as object in associations: %s", assoc_ids)
            return True

        return False

    def find_objects_mult(self, subjects, id_only=False):
        """
        Returns a list of associations for a given list of subjects
        """
        ds, datastore_name = self._get_datastore()
        validate_is_instance(subjects, list, 'subjects is not a list of resource_ids')
        view_args = dict(keys=subjects, include_docs=True)
        results = self.query_view(self._get_view_name("association", "by_bulk"), view_args)
        ids = [i['value'] for i in results]
        assocs = [i['doc'] for i in results]
        self._count(find_assocs_mult_call=1, find_assocs_mult_obj=len(ids))
        if id_only:
            return ids, assocs
        else:
            return self.read_mult(ids), assocs

    def find_subjects_mult(self, objects, id_only=False):
        """
        Returns a list of associations for a given list of objects
        """
        ds, datastore_name = self._get_datastore()
        validate_is_instance(objects, list, 'objects is not a list of resource_ids')
        view_args = dict(keys=objects, include_docs=True)
        results = self.query_view(self._get_view_name("association", "by_subject_bulk"), view_args)
        ids = [i['value'] for i in results]
        assocs = [i['doc'] for i in results]
        self._count(find_assocs_mult_call=1, find_assocs_mult_obj=len(ids))
        if id_only:
            return ids, assocs
        else:
            return self.read_mult(ids), assocs

    def find_objects(self, subject, predicate=None, object_type=None, id_only=False, **kwargs):
        log.debug("find_objects(subject=%s, predicate=%s, object_type=%s, id_only=%s", subject, predicate, object_type, id_only)

        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        if not subject:
            raise BadRequest("Must provide subject")
        if object_type and not predicate:
            raise BadRequest("Cannot provide object type without a predictate")

        ds, datastore_name = self._get_datastore()

        if type(subject) is str:
            subject_id = subject
        else:
            if "_id" not in subject:
                raise BadRequest("Object id not available in subject")
            else:
                subject_id = subject._id

        view_args = self._get_view_args(kwargs)
        view = ds.view(self._get_view_name("association", "by_sub"), **view_args)
        key = [subject_id]
        if predicate:
            key.append(predicate)
            if object_type:
                key.append(object_type)
        endkey = self._get_endkey(key)
        rows = view[key:endkey]

        obj_assocs = [self._persistence_dict_to_ion_object(row['value']) for row in rows]
        obj_ids = [assoc.o for assoc in obj_assocs]
        self._count(find_objects_call=1, find_objects_obj=len(obj_assocs))

        log.debug("find_objects() found %s objects", len(obj_ids))
        if id_only:
            return (obj_ids, obj_assocs)

        obj_list = self.read_mult(obj_ids)
        return (obj_list, obj_assocs)

    def find_subjects(self, subject_type=None, predicate=None, obj=None, id_only=False, **kwargs):

        log.debug("find_subjects(subject_type=%s, predicate=%s, object=%s, id_only=%s", subject_type, predicate, obj, id_only)

        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        if not obj:
            raise BadRequest("Must provide object")
        if subject_type and not predicate:
            raise BadRequest("Cannot provide subject type without a predicate")

        ds, datastore_name = self._get_datastore()

        if type(obj) is str:
            object_id = obj
        else:
            if "_id" not in obj:
                raise BadRequest("Object id not available in object")
            else:
                object_id = obj._id

        view_args = self._get_view_args(kwargs)
        view = ds.view(self._get_view_name("association", "by_obj"), **view_args)
        key = [object_id]
        if predicate:
            key.append(predicate)
            if subject_type:
                key.append(subject_type)
        endkey = self._get_endkey(key)
        rows = view[key:endkey]

        sub_assocs = [self._persistence_dict_to_ion_object(row['value']) for row in rows]
        sub_ids = [assoc.s for assoc in sub_assocs]
        self._count(find_subjects_call=1, find_subjects_obj=len(sub_assocs))

        log.debug("find_subjects() found %s subjects", len(sub_ids))
        if id_only:
            return (sub_ids, sub_assocs)

        sub_list = self.read_mult(sub_ids)
        return (sub_list, sub_assocs)

    def find_associations(self, subject=None, predicate=None, obj=None, assoc_type=None, id_only=True, anyside=None, **kwargs):
        log.debug("find_associations(subject=%s, predicate=%s, object=%s, anyside=%s)", subject, predicate, obj, anyside)
        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        if not (subject or obj or predicate or anyside):
            raise BadRequest("Illegal parameters: No S/P/O or anyside")
        #if assoc_type:
        #    raise BadRequest("Illegal parameters: assoc_type deprecated")
        if anyside and (subject or obj):
            raise BadRequest("Illegal parameters: anyside cannot be combined with S/O")
        if anyside and predicate and type(anyside) in (list, tuple):
            raise BadRequest("Illegal parameters: anyside list cannot be combined with P")

        if subject:
            if type(subject) is str:
                subject_id = subject
            else:
                if "_id" not in subject:
                    raise BadRequest("Object id not available in subject")
                else:
                    subject_id = subject._id
        if obj:
            if type(obj) is str:
                object_id = obj
            else:
                if "_id" not in obj:
                    raise BadRequest("Object id not available in object")
                else:
                    object_id = obj._id
        if anyside:
            if type(anyside) is str:
                anyside_ids = [anyside]
            elif type(anyside) in (list, tuple):
                if not all([type(o) in (str, list, tuple) for o in anyside]):
                    raise BadRequest("List of object ids or (object id, predicate) expected")
                anyside_ids = anyside
            else:
                if "_id" not in anyside:
                    raise BadRequest("Object id not available in anyside")
                else:
                    anyside_ids = [anyside._id]

        ds, datastore_name = self._get_datastore()
        view_args = self._get_view_args(kwargs)

        if subject and obj:
            view = ds.view(self._get_view_name("association", "by_match"), **view_args)
            key = [subject_id, object_id]
            if predicate:
                key.append(predicate)
            endkey = self._get_endkey(key)
            rows = view[key:endkey]
        elif subject:
            view = ds.view(self._get_view_name("association", "by_sub"), **view_args)
            key = [subject_id]
            if predicate:
                key.append(predicate)
            endkey = self._get_endkey(key)
            rows = view[key:endkey]
        elif obj:
            view = ds.view(self._get_view_name("association", "by_obj"), **view_args)
            key = [object_id]
            if predicate:
                key.append(predicate)
            endkey = self._get_endkey(key)
            rows = view[key:endkey]
        elif anyside:
            if predicate:
                view = ds.view(self._get_view_name("association", "by_idpred"), **view_args)
                key = [anyside, predicate]
                endkey = self._get_endkey(key)
                rows = view[key:endkey]
            elif type(anyside_ids[0]) is str:
                rows = ds.view(self._get_view_name("association", "by_id"), keys=anyside_ids, **view_args)
            else:
                rows = ds.view(self._get_view_name("association", "by_idpred"), keys=anyside_ids, **view_args)
        elif predicate:
            view = ds.view(self._get_view_name("association", "by_pred"), **view_args)
            key = [predicate]
            endkey = self._get_endkey(key)
            rows = view[key:endkey]
        else:
            raise BadRequest("Illegal arguments")

        if id_only:
            assocs = [row.id for row in rows]
        else:
            assocs = [self._persistence_dict_to_ion_object(row['value']) for row in rows]
        log.debug("find_associations() found %s associations", len(assocs))
        self._count(find_assocs_call=1, find_assocs_obj=len(assocs))
        return assocs

    def find_resources(self, restype="", lcstate="", name="", id_only=True):
        return self.find_resources_ext(restype=restype, lcstate=lcstate, name=name, id_only=id_only)

    def find_resources_ext(self, restype="", lcstate="", name="",
                           keyword=None, nested_type=None,
                           attr_name=None, attr_value=None, alt_id=None, alt_id_ns=None,
                           limit=None, skip=None, descending=None, id_only=True):
        filter_kwargs = self._get_view_args(dict(limit=limit, skip=skip, descending=descending))
        if name:
            if lcstate:
                raise BadRequest("find by name does not support lcstate")
            return self.find_res_by_name(name, restype, id_only, filter=filter_kwargs)
        elif keyword:
            return self.find_res_by_keyword(keyword, restype, id_only, filter=filter_kwargs)
        elif alt_id or alt_id_ns:
            return self.find_res_by_alternative_id(alt_id, alt_id_ns, id_only, filter=filter_kwargs)
        elif nested_type:
            return self.find_res_by_nested_type(nested_type, restype, id_only, filter=filter_kwargs)
        elif restype and attr_name:
            return self.find_res_by_attribute(restype, attr_name, attr_value, id_only=id_only, filter=filter_kwargs)
        elif restype and lcstate:
            return self.find_res_by_lcstate(lcstate, restype, id_only, filter=filter_kwargs)
        elif restype:
            return self.find_res_by_type(restype, lcstate, id_only, filter=filter_kwargs)
        elif lcstate:
            return self.find_res_by_lcstate(lcstate, restype, id_only, filter=filter_kwargs)
        elif not restype and not lcstate and not name:
            return self.find_res_by_type(None, None, id_only, filter=filter_kwargs)

    def _prepare_find_return(self, rows, res_assocs=None, id_only=True, **kwargs):
        if id_only:
            res_ids = [row.id for row in rows]
            return (res_ids, res_assocs)
        else:
            res_docs = [self._persistence_dict_to_ion_object(row.doc) for row in rows]
            return (res_docs, res_assocs)

    def find_res_by_type(self, restype, lcstate=None, id_only=False, filter=None):
        log.debug("find_res_by_type(restype=%s, lcstate=%s)", restype, lcstate)
        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        if lcstate:
            raise BadRequest('lcstate not supported anymore in find_res_by_type')
        filter = filter if filter is not None else {}
        ds, datastore_name = self._get_datastore()
        view = ds.view(self._get_view_name("resource", "by_type"), include_docs=(not id_only), **filter)
        if restype:
            key = [restype]
            endkey = self._get_endkey(key)
            rows = view[key:endkey]   # Range query
        else:
            # Returns ALL documents, only limited by filter
            rows = view

        res_assocs = [dict(type=row['key'][0], name=row['key'][1], id=row.id) for row in rows]
        log.debug("find_res_by_type() found %s objects", len(res_assocs))
        self._count(find_res_by_type_call=1, find_res_by_type_obj=len(res_assocs))
        return self._prepare_find_return(rows, res_assocs, id_only=id_only)

    def find_res_by_lcstate(self, lcstate, restype=None, id_only=False, filter=None):
        log.debug("find_res_by_lcstate(lcstate=%s, restype=%s)", lcstate, restype)
        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        if '_' in lcstate:
            log.warn("Search for compound lcstate restricted to maturity: %s", lcstate)
            lcstate,_ = lcstate.split("_", 1)
        filter = filter if filter is not None else {}
        ds, datastore_name = self._get_datastore()
        view = ds.view(self._get_view_name("resource", "by_lcstate"), include_docs=(not id_only), **filter)
        key = [1, lcstate] if lcstate in CommonResourceLifeCycleSM.AVAILABILITY else [0, lcstate]
        if restype:
            key.append(restype)
        endkey = self._get_endkey(key)
        rows = view[key:endkey]   # Range query

        res_assocs = [dict(lcstate=row['key'][1], type=row['key'][2], name=row['key'][3], id=row.id) for row in rows]
        log.debug("find_res_by_lcstate() found %s objects", len(res_assocs))
        self._count(find_res_by_lcstate_call=1, find_res_by_lcstate_obj=len(res_assocs))
        return self._prepare_find_return(rows, res_assocs, id_only=id_only)

    def find_res_by_name(self, name, restype=None, id_only=False, filter=None):
        log.debug("find_res_by_name(name=%s, restype=%s)", name, restype)
        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        filter = filter if filter is not None else {}
        ds, datastore_name = self._get_datastore()
        view = ds.view(self._get_view_name("resource", "by_name"), include_docs=(not id_only), **filter)
        key = [name]
        if restype:
            key.append(restype)
        endkey = self._get_endkey(key)
        rows = view[key:endkey]   # Range query

        res_assocs = [dict(name=row['key'][0], type=row['key'][1], id=row.id) for row in rows]
        log.debug("find_res_by_name() found %s objects", len(res_assocs))
        self._count(find_res_by_name_call=1, find_res_by_name_obj=len(res_assocs))
        return self._prepare_find_return(rows, res_assocs, id_only=id_only)

    def find_res_by_keyword(self, keyword, restype=None, id_only=False, filter=None):
        log.debug("find_res_by_keyword(keyword=%s, restype=%s)", keyword, restype)
        if not keyword or type(keyword) is not str:
            raise BadRequest('Argument keyword illegal')
        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        filter = filter if filter is not None else {}
        ds, datastore_name = self._get_datastore()
        view = ds.view(self._get_view_name("resource", "by_keyword"), include_docs=(not id_only), **filter)
        key = [keyword]
        if restype:
            key.append(restype)
        endkey = self._get_endkey(key)
        rows = view[key:endkey]

        res_assocs = [dict(keyword=row['key'][0], type=row['key'][1], id=row.id) for row in rows]
        log.debug("find_res_by_keyword() found %s objects", len(res_assocs))
        self._count(find_res_by_kw_call=1, find_res_by_kw_obj=len(res_assocs))
        return self._prepare_find_return(rows, res_assocs, id_only=id_only)

    def find_res_by_nested_type(self, nested_type, restype=None, id_only=False, filter=None):
        log.debug("find_res_by_nested_type(nested_type=%s, restype=%s)", nested_type, restype)
        if not nested_type or type(nested_type) is not str:
            raise BadRequest('Argument nested_type illegal')
        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        filter = filter if filter is not None else {}
        ds, datastore_name = self._get_datastore()
        view = ds.view(self._get_view_name("resource", "by_nestedtype"), include_docs=(not id_only), **filter)
        key = [nested_type]
        if restype:
            key.append(restype)
        endkey = self._get_endkey(key)
        rows = view[key:endkey]

        res_assocs = [dict(nested_type=row['key'][0], type=row['key'][1], id=row.id) for row in rows]
        log.debug("find_res_by_nested_type() found %s objects", len(res_assocs))
        self._count(find_res_by_nested_call=1, find_res_by_nested_obj=len(res_assocs))
        return self._prepare_find_return(rows, res_assocs, id_only=id_only)

    def find_res_by_attribute(self, restype, attr_name, attr_value=None, id_only=False, filter=None):
        log.debug("find_res_by_attribute(restype=%s, attr_name=%s, attr_value=%s)", restype, attr_name, attr_value)
        if not attr_name or type(attr_name) is not str:
            raise BadRequest('Argument attr_name illegal')
        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        filter = filter if filter is not None else {}
        ds, datastore_name = self._get_datastore()
        view = ds.view(self._get_view_name("resource", "by_attribute"), include_docs=(not id_only), **filter)
        key = [restype, attr_name]
        if attr_value:
            key.append(attr_value)
        endkey = self._get_endkey(key)
        rows = view[key:endkey]

        res_assocs = [dict(type=row['key'][0], attr_name=row['key'][1], attr_value=row['key'][2], id=row.id) for row in rows]
        log.debug("find_res_by_attribute() found %s objects", len(res_assocs))
        self._count(find_res_by_attribute_call=1, find_res_by_attribute_obj=len(res_assocs))
        return self._prepare_find_return(rows, res_assocs, id_only=id_only)

    def find_res_by_alternative_id(self, alt_id=None, alt_id_ns=None, id_only=False, filter=None):
        log.debug("find_res_by_alternative_id(restype=%s, alt_id_ns=%s)", alt_id, alt_id_ns)
        if alt_id and type(alt_id) is not str:
            raise BadRequest('Argument alt_id illegal')
        if alt_id_ns and type(alt_id_ns) is not str:
            raise BadRequest('Argument alt_id_ns illegal')
        if type(id_only) is not bool:
            raise BadRequest('id_only must be type bool, not %s' % type(id_only))
        filter = filter if filter is not None else {}
        ds, datastore_name = self._get_datastore()
        view = ds.view(self._get_view_name("resource", "by_altid"), include_docs=(not id_only), **filter)
        key = []
        if alt_id:
            key.append(alt_id)
            if alt_id_ns is not None:
                key.append(alt_id_ns)

        endkey = self._get_endkey(key)
        rows = view[key:endkey]

        if alt_id_ns and not alt_id:
            res_assocs = [dict(alt_id=row['key'][0], alt_id_ns=row['key'][1], id=row.id) for row in rows if row['key'][1] == alt_id_ns]
        else:
            res_assocs = [dict(alt_id=row['key'][0], alt_id_ns=row['key'][1], id=row.id) for row in rows]
        log.debug("find_res_by_alternative_id() found %s objects", len(res_assocs))
        self._count(find_res_by_altid_call=1, find_res_by_altid_obj=len(res_assocs))
        if id_only:
            res_ids = [row['id'] for row in res_assocs]
            return (res_ids, res_assocs)
        else:
            if alt_id_ns and not alt_id:
                res_docs = [self._persistence_dict_to_ion_object(row.doc) for row in rows if row['key'][1] == alt_id_ns]
            else:
                res_docs = [self._persistence_dict_to_ion_object(row.doc) for row in rows]
            return (res_docs, res_assocs)

    def find_res_by_view(self, design_name, view_name, key=None, keys=None, start_key=None, end_key=None,
                     id_only=True, **kwargs):
        # TODO: Refactor common code out of above find functions
        pass

    def find_by_view(self, design_name, view_name, key=None, keys=None, start_key=None, end_key=None,
                           id_only=True, convert_doc=True, convert_value=True, **kwargs):
        """
        Generic find function using a defined index
        @param design_name  design document
        @param view_name  view name
        @param key  specific key to find
        @param keys  list of keys to find
        @param start_key  find range start value
        @param end_key  find range end value
        @param id_only  if True, the 4th element of each triple is the document
        @param convert_doc  if True, make IonObject out of doc
        @param convert_value  if True, make IonObject out of value
        @retval Returns a list of 4-tuples: (document id, index key, index value, document)
        """
        res_rows = self.find_docs_by_view(design_name=design_name, view_name=view_name, key=key, keys=keys,
                                          start_key=start_key, end_key=end_key, id_only=id_only, **kwargs)

        res_rows = [(rid, key,
                     self._persistence_dict_to_ion_object(value) if convert_value and isinstance(value, dict) else value,
                     self._persistence_dict_to_ion_object(doc) if convert_doc and isinstance(doc, dict) else doc)
                    for rid,key,value,doc in res_rows]

        log.debug("find_by_view() found %s objects" % (len(res_rows)))
        return res_rows


    def _ion_object_to_persistence_dict(self, ion_object):
        if ion_object is None: return None

        obj_dict = self._io_serializer.serialize(ion_object)
        return obj_dict

    def _persistence_dict_to_ion_object(self, obj_dict):
        if obj_dict is None: return None

        ion_object = self._io_deserializer.deserialize(obj_dict)
        return ion_object

    def query_view(self, view_name='', opts={}, datastore_name=''):
        '''
        query_view is a straight through method for querying a view in CouchDB. query_view provides us the interface
        to the view structure in couch, in lieu of implementing a method for every type of query we could want, we
        now have the capability for clients to make queries to couch in a straight-through manner.
        '''
        ds, datastore_name = self._get_datastore(datastore_name)

        # Actually obtain the results and place them in rows
        rows = ds.view(view_name, **opts)

        # Parse the results and convert the results into ionobjects and python types.
        result = self._parse_results(rows)

        return result

    def custom_query(self, map_fun, reduce_fun=None, datastore_name='', **options):
        '''
        custom_query sets up a temporary view in couchdb, the map_fun is a string consisting
        of the javascript map function

        Warning: Please note that temporary views are not suitable for use in production,
        as they are really slow for any database with more than a few dozen documents.
        You can use a temporary view to experiment with view functions, but switch to a
        permanent view before using them in an application.
        '''
        ds, datastore_name = self._get_datastore(datastore_name)
        res = ds.query(map_fun, reduce_fun, **options)

        return self._parse_results(res)

    def _parse_results(self, doc):
        ''' Parses a complex object and organizes it into basic types
        '''
        ret = {}

        #-------------------------------
        # Handle ViewResults type (CouchDB type)
        #-------------------------------
        # \_ Ignore the meta data and parse the rows only
        if isinstance(doc, ViewResults):
            try:
                ret = self._parse_results(doc.rows)
            except ResourceNotFound as e:
                raise BadRequest('The desired resource does not exist.')

            return ret

        #-------------------------------
        # Handle A Row (CouchDB type)
        #-------------------------------
        # \_ Split it into a dict with a key and a value
        #    Recursively parse down through the structure.
        if isinstance(doc, Row):
            if 'id' in doc:
                ret['id'] = doc['id']
            ret['key'] = self._parse_results(doc['key'])
            ret['value'] = self._parse_results(doc['value'])
            if 'doc' in doc:
                ret['doc'] = self._parse_results(doc['doc'])
            return ret

        #-------------------------------
        # Handling a list
        #-------------------------------
        # \_ Break it apart and parse each element in the list

        if isinstance(doc, list):
            ret = []
            for element in doc:
                ret.append(self._parse_results(element))
            return ret
        #-------------------------------
        # Handle a dic
        #-------------------------------
        # \_ Check to make sure it's not an IonObject
        # \_ Parse the key value structure for other objects
        if isinstance(doc, dict):
            if '_id' in doc:
                # IonObject
                return self._persistence_dict_to_ion_object(doc)

            for key, value in doc.iteritems():
                ret[key] = self._parse_results(value)
            return ret

        #-------------------------------
        # Primitive type
        #-------------------------------
        return doc

    def _count(self, datastore=None, **kwargs):
        datastore = datastore or self.datastore_name
        self._stats.count(namespace=datastore, **kwargs)
