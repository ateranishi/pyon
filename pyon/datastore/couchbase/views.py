#!/usr/bin/env python

__author__ = 'Michael Meisinger, Seman Said'


# NOTE: CANNOT import DataStore here!!!

COUCHDB_CONFIGS = {
    "OBJECTS":{
        'views': ['object','association','attachment']
    },
    "RESOURCES":{
        'views': ['resource','directory','association','attachment']
    },
    "EVENTS":{
        'views': ['event']
    },
    "STATE":{
        'views': []
    },
    "SCIDATA":{
        'views': ['datasets','manifest']
    },
    "EXAMPLES":{
        'views':['posts']
    },
    "BASIC":{
        'views': []
    },
    "FILESYSTEM": {
        'views': ['catalog']
    },
    }

# Defines all the available CouchDB views and their map/reduce functions.
# Views are associated to datastore based on profile.
COUCHDB_VIEWS = {
    # -------------------------------------------------------------------------
    # Views for ION Resource objects
    # Resources all have a type, life cycle state and name
    # Note: adding additional entries to the index such as name leads to a sort by name but prevents range queries
    'resource':{
        # Find resource by exact type, ordered by name (important for pagination)
        'by_type':{
            'map':"""
function(doc) {
  if (doc.type_ && doc.lcstate != undefined && doc.lcstate != 'RETIRED' && doc.name != undefined) {
    name = String(doc.name).substring(0,200);
    emit([doc.type_, name], null);
  }
}""",
            },
        # Find resource by lcstate (maturity)
        # The following is a more sophisticated index. It does two things for each Resource object:
        # 1: It emits an index value prefixed by 0 for the lcstate maturity
        # 2: It emits an index value prefixed by 0 for the lcstate availability
        # The results are ordered by type_ first then name (important for pagination)
        'by_lcstate':{
            'map':"""
function(doc) {
  if (doc.type_ && doc.lcstate != undefined && doc.availability != undefined && doc.name != undefined) {
    name = String(doc.name).substring(0,200);
    emit([0, doc.lcstate, doc.type_, name], null);
    emit([1, doc.availability, doc.type_, name], null);
  }
}""",
            },
        # Find by name, ordered by type_ (important for pagination)
        'by_name':{
            'map':"""
function(doc) {
  if (doc.type_ && doc.lcstate != undefined && doc.lcstate != 'RETIRED' && doc.name != undefined) {
    emit([doc.name, doc.type_], null);
  }
}""",
            },
        # Find by alternative ID (e.g. as used in preload)
        'by_altid':{
            'map':"""
function(doc) {
  if (doc.type_ && doc.alt_ids && doc.lcstate != 'RETIRED') {
    for (var i = 0; i < doc.alt_ids.length; i++ ) {
      altid = doc.alt_ids[i];
      parts = altid.split(":");
      if (parts.length == 2) {
        emit([parts[1], parts[0]], null);
      } else {
        emit([altid, "_"], null);
      }
    }
  } else if (doc.type_ && doc.uirefid && doc.lcstate != 'RETIRED') {
    emit([doc.uirefid, "UIREFID"], null);
  }
}""",
            },
        # Find by keyword then res type (one entry per keyword in a resource)
        'by_keyword':{
            'map':"""
function(doc) {
  if (doc.type_ && doc.lcstate != 'RETIRED' && doc.keywords != undefined) {
    for (var i = 0; i < doc.keywords.length; i++ ) {
      emit([doc.keywords[i], doc.type_], null);
    }
  }
}""",
            },
        # Find by name of nested type, then res type (one record per nested ION object type)
        'by_nestedtype':{
            'map':"""
function(doc) {
  if (doc.type_ && doc.lcstate != 'RETIRED') {
    for (var attr in doc) {
      if (doc[attr] != undefined && doc[attr].type_) {
        emit([doc[attr].type_, doc.type_], null);
      }
    }
  }
}""",
            },
        # Find by attribute only for special resources.
        # This is a special case treatment. Add emits below as needed for access elsewhere in the code.
        'by_attribute':{
            'map':"""
function(doc) {
  if (doc.type_ && doc.lcstate != 'RETIRED') {
    if (doc.type_ == "UserInfo" && doc.contact != undefined && doc.contact.email != undefined) {
      emit([doc.type_, "contact.email", doc.contact.email], null);
    }
  }
}""",
            },
        },

    # -------------------------------------------------------------------------
    # Pure ION object related views
    # Every object has a type and ID
    'object':{
        'by_type':{
            'map':"""
function(doc) {
  if (doc.type_ != undefined) {
    emit([doc.type_], null);
  }
}""",
            },
        },

    # -------------------------------------------------------------------------
    # Attachment objects
    'attachment':{
        # Attachment for an object, ordered by create timestamp
        # Note: the keywords list is part of the index so that it can be checked
        # before retrieving attachment objects
        'by_resource':{
            'map':"""
function(doc) {
  if (doc.type_ == "Attachment") {
    emit([doc.object_id, doc.ts_created, doc.keywords], null);
  }
}""",
            },

        },

    # -------------------------------------------------------------------------
    # Association (triple) related views
    'association':{
        # Subject to object lookup (for range queries)
        #Todo Not a good way to get all the data.
        'by_doc':{
            'map':"""
function(doc, meta) {
    emit(meta.id, null);
}""",
            },
        'by_sub':{
            'map':"""
function(doc) {
  if (doc.type_ == "Association" &&! doc.retired) {
    emit([doc.s, doc.p, doc.ot, doc.o], doc);
  }
}""",
            },
        # Object to subject lookup (for range queries)
        'by_obj':{
            'map':"""
function(doc) {
  if (doc.type_ == "Association" &&! doc.retired) {
    emit([doc.o, doc.p, doc.st, doc.s], doc);
  }
}""",
            },
        # For matching association lookup (for range queries)
        'by_match':{
            'map':"""
function(doc) {
  if (doc.type_ == "Association" &&! doc.retired) {
    emit([doc.s, doc.o, doc.p], doc);
  }
}""",
            },
        # For undirected association lookup with predicate (range queries)
        'by_idpred':{
            'map':"""
function(doc) {
  if (doc.type_ == "Association" &&! doc.retired) {
    emit([doc.s, doc.p], doc);
    emit([doc.o, doc.p], doc);
  }
}""",
            },
        # For undirected association lookup with id only (multi key queries)
        'by_id':{
            'map':"""
function(doc) {
  if (doc.type_ == "Association" &&! doc.retired) {
    emit(doc.s, doc);
    emit(doc.o, doc);
  }
}""",
            },
        # By predicate then subject then object
        'by_pred':{
            'map':"""
function(doc) {
  if (doc.type_ == "Association" &&! doc.retired) {
    emit([doc.p, doc.s, doc.o], doc);
  }
}""",
            },
        # Subject to object lookup (for multi key queries)
        'by_bulk':{
            'map':"""
function(doc) {
  if(doc.type_ == "Association" &&! doc.retired) {
    emit(doc.s, doc.o);
  }
}""",
            },
        'by_subject_bulk':{
            'map':"""
function(doc) {
  if(doc.type_ == "Association" &&! doc.retired) {
    emit(doc.o, doc.s);
  }
}""",
            }
    },

    # -------------------------------------------------------------------------
    # Directory related objects
    # DirEntry objects are the elements of the directory tree
    'directory':{
        'by_path':{
            'map':"""
function(doc) {
  if (doc.type_ == "DirEntry") {
    levels = doc.parent.split('/');
    levels.splice(0, 1);
    if (doc.parent == "/") levels.splice(0, 1);
    levels.push(doc.key);
    emit([doc.org, levels], doc);
  }
}""",
            },
        'by_key':{
            'map':"""
function(doc) {
  if (doc.type_ == "DirEntry") {
    emit([doc.org, doc.key, doc.parent], doc);
  }
}""",
            },
        'by_parent':{
            'map':"""
function(doc) {
  if (doc.type_ == "DirEntry") {
    emit([doc.org, doc.parent, doc.key], doc);
  }
}""",
            },
        'by_attribute':{
            'map':"""
function(doc) {
  if (doc.type_ == "DirEntry") {
    for (var attr in doc.attributes) {
      attval = doc.attributes[attr];
      if (attval != undefined && attval.length > 0) {
        attval = String(attval).substring(0,200);
      }
      emit([doc.org, attr, attval, doc.parent], doc);
    }
  }
}""",
            },
        },

    # -------------------------------------------------------------------------
    # Event related objects
    'event':{
        'by_time':{
            'map':"""
function(doc) {
  if (doc.origin) {
    emit([doc.ts_created]);
  }
}""",
            },
        'by_type':{
            'map':"""
function(doc) {
  if (doc.origin) {
    emit([doc.type_, doc.ts_created]);
  }
}""",
            },
        'by_origin':{
            'map':"""
function(doc) {
  if (doc.origin) {
    emit([doc.origin, doc.ts_created]);
  }
}""",
            },
        'by_origintype':{
            'map':"""
function(doc) {
  if (doc.origin) {
    emit([doc.origin, doc.type_, doc.ts_created]);
  }
}""",
            },
        },


    # -------------------------------------------------------------------------
    'posts' : {
        "dataset_by_id": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogPost\") { emit([doc.post_id,0],doc._id);}\n\telse if(doc.type_==\"BlogComment\") { emit([doc.ref_id,1],doc._id);}\n}"
        },
        "posts_by_id": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogPost\") { emit(doc.post_id,doc._id);}}"
        },
        "posts_by_title": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogPost\") { emit(doc.title,doc._id);}}"
        },
        "posts_by_updated": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogPost\") { emit(doc.updated,doc._id);}}"
        },
        "posts_by_author": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogPost\") { emit(doc.author.name,doc._id);}}"
        },
        "comments_by_post_id": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogComment\") { emit(doc.ref_id,doc._id);}}"
        },
        "comments_by_author": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogComment\") { emit(doc.author.name,doc._id);}}"
        },
        "comments_by_updated": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogComment\") { emit(doc.updated,doc._id);}}"
        },
        "posts_join_comments": {
            "map": "function(doc)\n{\tif(doc.type_==\"BlogPost\") { emit([doc.post_id,0],doc._id);}\n\telse if(doc.type_==\"BlogComment\") { emit([doc.ref_id,1],doc._id);}\n}"
        },
        "posts_by_author_date": {
            "map": "function(doc) {\n  if(doc.type_==\"BlogPost\")\n    emit([doc.author.name,doc.updated,doc.post_id], doc.post_id);\n  else if(doc.type==\"BlogComment\")\n    emit([doc.author.name,doc.updated,doc.ref_id], doc.ref_id);\n}"
        }
    },

    # -------------------------------------------------------------------------
    'datasets': {
        # Bounds
        # Map: https://gist.github.com/1781675#file_maps.js
        # Reduce: https://gist.github.com/1781675#file_reduce_bounds.js
        "bounds": {
            #"map": "\n/*\n * Author: Luke Campbell <lcampbell@asascience.com>\n * Description: Utility and map functions to map the DataContainer's bounding elements.\n */\n\n/*\n * Traverses an \"identifiable\" in a document to see if it contains a CoordinateAxis\n */\nfunction traverse(identifiable) {\n    if(identifiable.type_==\"CoordinateAxis\")\n    {\n        return identifiable.bounds_id;\n    }\n    else return null;\n}\n/*\n * Gets the CoordinateAxis objects and their bounds_ids\n */\nfunction get_bounds(doc)\n{\n    identifiables = doc.identifiables;\n    var bounds = [];\n    for(var i in identifiables)\n    {\n        var bounds_id = traverse(identifiables[i]);\n        if(bounds_id)\n            bounds.push(bounds_id);\n    }\n    return bounds;\n}\n\n/* Data map */\nfunction (doc) {\n    if(doc.type_ == \"StreamGranuleContainer\"){\n        var bounds = get_bounds(doc);\n        for(var b in bounds)\n        {\n            var key = bounds[b];\n            var s = String(key)\n            var pack = {};\n            pack[key] = doc.identifiables[key].value_pair;\n\n            emit([doc.stream_resource_id,1], pack);\n        }\n    }\n}\n\n",
            "map" : """
function (doc) {
    if(doc.type_ == "StreamGranuleContainer"){
        function get_bounds(doc)
        {
            identifiables = doc.identifiables;
            var bounds = [];
            for(var i in identifiables)
            {
                var bounds_id = traverse(identifiables[i]);
                if(bounds_id)
                    bounds.push(bounds_id);
            }
            return bounds;
        }

        function traverse(identifiable) {
            if(identifiable.type_=="CoordinateAxis")
            {
                return identifiable.bounds_id;
            }
            else return null;
        }

        var bounds = get_bounds(doc);
        for(var b in bounds)
        {
            var key = bounds[b];
            var s = String(key)
            var pack = {};
            pack[key] = doc.identifiables[key].value_pair;

            emit([doc.stream_resource_id,1], pack);
        }
    }
}
""",
            ##"reduce": "\n\nfunction value_in(value,ary) {\n    for(var i in ary)\n    {\n        if(value == ary[i])\n            return i;\n    }\n}\nfunction get_keys(obj){\n    var keys = [];\n    for(var key in obj)\n        keys.push(key);\n    return keys;\n}\nfunction print_dic(obj) {\n    for(var key in obj)\n    {\n        debug(key);\n        debug(obj[key]);\n    }\n}\n\nfunction(keys,values,rereduce) {\n    var res_keys = [];\n    var results = values[0];\n        \n    /* Not a rereduce so populate the results dictionary */\n\n\n    for(var i in values)\n    {\n\n        var keys = get_keys(values[i]);\n        for(var j in keys)\n        {\n            var key = keys[j];\n\n            if(! value_in(key, res_keys))\n            {\n                res_keys.push(key);\n                results[key] = values[i][key];\n                continue;\n            }\n            var value = values[i][key];\n            results[key][0] = Math.min(value[0], results[key][0]);\n            results[key][1] = Math.max(value[1], results[key][1]);\n        }\n    }\n    return results;\n\n    \n    \n}"
            "reduce" : """
function(keys,values,rereduce) {
    var res_keys = [];
    var results = values[0];

    /* Not a rereduce so populate the results dictionary */
    function value_in(value,ary) {
        for(var i in ary)
        {
            if(value == ary[i])
                return i;
        }
    }
    function get_keys(obj){
        var keys = [];
        for(var key in obj)
            keys.push(key);
        return keys;
    }
    function print_dic(obj) {
        for(var key in obj)
        {
            debug(key);
            debug(obj[key]);
        }
    }

    for(var i in values)
    {

        var keys = get_keys(values[i]);
        for(var j in keys)
        {
            var key = keys[j];

            if(! value_in(key, res_keys))
            {
                res_keys.push(key);
                results[key] = values[i][key];
                continue;
            }
            var value = values[i][key];
            results[key][0] = Math.min(value[0], results[key][0]);
            results[key][1] = Math.max(value[1], results[key][1]);
        }
    }
    return results;
}
"""
        },
        # Stream join granule
        # Map: https://gist.github.com/1781675#file_map_stream_join_granule.js
        "stream_join_granule": {
            "map": "function (doc) {\n    if(doc.type_ == \"StreamDefinitionContainer\")\n        emit([doc.stream_resource_id,0],doc._id);\n    else if(doc.type_ == \"StreamGranuleContainer\")\n        emit([doc.stream_resource_id,1], doc._id);\n}\n"
        },
        # Cannonical reference to Stream join granule
        "dataset_by_id": {
            "map": "function (doc) {\n    if(doc.type_ == \"StreamDefinitionContainer\")\n        emit([doc.stream_resource_id,0],doc._id);\n    else if(doc.type_ == \"StreamGranuleContainer\")\n        emit([doc.stream_resource_id,1], doc._id);\n}\n"
        },
        # https://gist.github.com/1781675#file_by_time.js
        # This view only works for granules built using the constructor API or one that uses 'time_bounds' specifically.
        "dataset_by_latest": {
            ##"map": "/********************************\n * Author: Luke Campbell\n * Description: simple map to order by time\n ********************************/\n \n// If the doc has a time_bounds display it\nfunction get_time(doc) {\n    if(doc.identifiables.time_bounds) {\n        emit([doc.stream_resource_id,doc.identifiables.time_bounds.value_pair[0]],doc._id);\n    }   \n}\nfunction(doc) {\n  get_time(doc);\n}"
            "map": """
/********************************
 * Author: Luke Campbell
 * Description: simple map to order by time
 ********************************/
function(doc) {
    // If the doc has a time_bounds display it
    function get_time(doc) {
        if(doc.identifiables.time_bounds) {
            emit([doc.stream_resource_id,doc.identifiables.time_bounds.value_pair[0]],doc._id);
        }
    }
    get_time(doc);
}
"""
        }

    },


    # -------------------------------------------------------------------------
    'manifest': {
        'by_dataset' : {
            'map' : 'function(doc) { var i = Number(doc.ts_create); emit([doc.dataset_id, i], doc._id); }'
        }
    },

    # -------------------------------------------------------------------------
    'catalog': {
        'file_by_name': {
            "map": "\nfunction(doc) { \n\n    emit([doc.name + doc.extension, doc.owner_id, doc.group_id, doc.permissions, doc.modified_date, doc.created_date], doc._id);\n}\n\n        \n"
        },
        "file_by_created_date": {
            "map": "function(doc) {\n  emit(doc.created_date, doc._id);\n}"
        },
        "file_by_modified_date": {
            "map": "function(doc) {\n  emit(doc.modified_date, doc._id);\n}"
        },
        "file_by_owner": {
            "map": "function(doc) {\n  emit([doc.owner_id, doc.group_id, doc.name], doc._id);\n}"
        },
        }
}

def get_couch_view_designs(config):
    store_config = COUCHDB_CONFIGS[config]
    views = store_config['views']
    res_views = {}
    for view in views:
        res_views[view] = COUCHDB_VIEWS[view]
    return res_views