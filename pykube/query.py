import json

from collections import namedtuple

from six import string_types
from six.moves.urllib.parse import urlencode

from .exceptions import ObjectDoesNotExist


all_ = object()
everything = object()
now = object()


class BaseQuery(object):

    def __init__(self, api, api_obj_class, namespace=None):
        self.api = api
        self.api_obj_class = api_obj_class
        self.namespace = namespace
        self.selector = everything
        self.field_selector = everything

    def all(self):
        return self._clone()

    def filter(self, namespace=None, selector=None, field_selector=None):
        clone = self._clone()
        if namespace is not None:
            clone.namespace = namespace
        if selector is not None:
            clone.selector = selector
        if field_selector is not None:
            clone.field_selector = field_selector
        return clone

    def _clone(self, cls=None):
        if cls is None:
            cls = self.__class__
        clone = cls(self.api, self.api_obj_class, namespace=self.namespace)
        clone.selector = self.selector
        clone.field_selector = self.field_selector
        return clone

    def _build_api_url(self, params=None):
        if params is None:
            params = {}
        if self.selector is not everything:
            params["labelSelector"] = as_selector(self.selector)
        if self.field_selector is not everything:
            params["fieldSelector"] = as_selector(self.field_selector)
        query_string = urlencode(params)
        return "{}{}".format(self.api_obj_class.endpoint, "?{}".format(query_string) if query_string else "")


class Query(BaseQuery):

    def get_by_name(self, name):
        kwargs = {
            "url": "{}/{}".format(self.api_obj_class.endpoint, name),
            "namespace": self.namespace,
        }
        if self.api_obj_class.base:
            kwargs["base"] = self.api_obj_class.base
        if self.api_obj_class.version:
            kwargs["version"] = self.api_obj_class.version
        r = self.api.get(**kwargs)
        if not r.ok:
            if r.status_code == 404:
                raise ObjectDoesNotExist("{} does not exist.".format(name))
            self.api.raise_for_status(r)
        return self.api_obj_class(self.api, r.json())

    def get(self, *args, **kwargs):
        if "name" in kwargs:
            return self.get_by_name(kwargs["name"])
        clone = self.filter(*args, **kwargs)
        num = len(clone)
        if num == 1:
            return clone.query_cache["objects"][0]
        if not num:
            raise ObjectDoesNotExist("get() returned zero objects")
        raise ValueError("get() more than one object; use filter")

    def get_or_none(self, *args, **kwargs):
        try:
            return self.get(*args, **kwargs)
        except ObjectDoesNotExist:
            return None

    def watch(self, since=None):
        query = self._clone(WatchQuery)
        if since is now:
            query.resource_version = self.response["metadata"]["resourceVersion"]
        elif since is not None:
            query.resource_version = since
        return query

    def execute(self):
        kwargs = {"url": self._build_api_url()}
        if self.api_obj_class.base:
            kwargs["base"] = self.api_obj_class.base
        if self.api_obj_class.version:
            kwargs["version"] = self.api_obj_class.version
        if self.namespace is not None and self.namespace is not all_:
            kwargs["namespace"] = self.namespace
        r = self.api.get(**kwargs)
        r.raise_for_status()
        return r

    def iterator(self):
        """
        Execute the API request and return an iterator over the objects. This
        method does not use the query cache.
        """
        for obj in (self.execute().json().get("items") or []):
            yield self.api_obj_class(self.api, obj)

    @property
    def query_cache(self):
        if not hasattr(self, "_query_cache"):
            cache = {"objects": []}
            cache["response"] = self.execute().json()
            for obj in (cache["response"].get("items") or []):
                cache["objects"].append(self.api_obj_class(self.api, obj))
            self._query_cache = cache
        return self._query_cache

    def __len__(self):
        return len(self.query_cache["objects"])

    def __iter__(self):
        return iter(self.query_cache["objects"])

    @property
    def response(self):
        return self.query_cache["response"]


class WatchQuery(BaseQuery):

    def __init__(self, *args, **kwargs):
        self.resource_version = kwargs.pop("resource_version", None)
        super(WatchQuery, self).__init__(*args, **kwargs)

    def object_stream(self):
        params = {"watch": "true"}
        if self.resource_version is not None:
            params["resourceVersion"] = self.resource_version
        flag = 1
        import datetime
        while True:
            if flag == 1:
                print("%s first watch connect" % datetime.datetime.now().strftime('%Y-%m-%d/%H:%M:%S'))
            elif flag > 1:
                print("%s watch reconnect" % datetime.datetime.now().strftime('%Y-%m-%d/%H:%M:%S'))

            try:
                kwargs = {
                    "url": self._build_api_url(params=params),
                    "stream": True,
                }
                if self.namespace is not all_:
                    kwargs["namespace"] = self.namespace
                if self.api_obj_class.version:
                    kwargs["version"] = self.api_obj_class.version
                r = self.api.get(**kwargs)
                self.api.raise_for_status(r)
                WatchEvent = namedtuple("WatchEvent", "type object")
                for line in r.iter_lines():
                    we = json.loads(line.decode("utf-8"))
                    obj = we['object']
                    if 'metadata' in obj and 'resourceVersion' in obj['metadata']:
                        self.resource_version = obj['metadata']['resourceVersion']
                    yield WatchEvent(type=we["type"], object=self.api_obj_class(self.api, we["object"]))
            finally:
                params['metadata.resourceVersion'] = self.resource_version
                flag += 1

    def __iter__(self):
        return iter(self.object_stream())


def as_selector(value):
    if isinstance(value, string_types):
        return value
    s = []
    for k, v in value.items():
        bits = k.split("__")
        assert len(bits) <= 2, "too many __ in selector"
        if len(bits) == 1:
            label = bits[0]
            op = "eq"
        else:
            label = bits[0]
            op = bits[1]
        # map operator to selector
        if op == "eq":
            s.append("{}={}".format(label, v))
        elif op == "neq":
            s.append("{} != {}".format(label, v))
        elif op == "in":
            s.append("{} in ({})".format(label, ",".join(v)))
        elif op == "notin":
            s.append("{} notin ({})".format(label, ",".join(v)))
        else:
            raise ValueError("{} is not a valid comparison operator".format(op))
    return ",".join(s)
