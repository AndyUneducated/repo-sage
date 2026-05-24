from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AddRequest(_message.Message):
    __slots__ = ("id", "vector")
    ID_FIELD_NUMBER: _ClassVar[int]
    VECTOR_FIELD_NUMBER: _ClassVar[int]
    id: str
    vector: _containers.RepeatedScalarFieldContainer[float]
    def __init__(self, id: _Optional[str] = ..., vector: _Optional[_Iterable[float]] = ...) -> None: ...

class AddResponse(_message.Message):
    __slots__ = ("ok", "size")
    OK_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    ok: bool
    size: int
    def __init__(self, ok: bool = ..., size: _Optional[int] = ...) -> None: ...

class BulkLoadResponse(_message.Message):
    __slots__ = ("inserted", "size")
    INSERTED_FIELD_NUMBER: _ClassVar[int]
    SIZE_FIELD_NUMBER: _ClassVar[int]
    inserted: int
    size: int
    def __init__(self, inserted: _Optional[int] = ..., size: _Optional[int] = ...) -> None: ...

class SearchRequest(_message.Message):
    __slots__ = ("vector", "top_k", "ef_search")
    VECTOR_FIELD_NUMBER: _ClassVar[int]
    TOP_K_FIELD_NUMBER: _ClassVar[int]
    EF_SEARCH_FIELD_NUMBER: _ClassVar[int]
    vector: _containers.RepeatedScalarFieldContainer[float]
    top_k: int
    ef_search: int
    def __init__(self, vector: _Optional[_Iterable[float]] = ..., top_k: _Optional[int] = ..., ef_search: _Optional[int] = ...) -> None: ...

class SearchHit(_message.Message):
    __slots__ = ("id", "distance")
    ID_FIELD_NUMBER: _ClassVar[int]
    DISTANCE_FIELD_NUMBER: _ClassVar[int]
    id: str
    distance: float
    def __init__(self, id: _Optional[str] = ..., distance: _Optional[float] = ...) -> None: ...

class SearchResponse(_message.Message):
    __slots__ = ("hits",)
    HITS_FIELD_NUMBER: _ClassVar[int]
    hits: _containers.RepeatedCompositeFieldContainer[SearchHit]
    def __init__(self, hits: _Optional[_Iterable[_Union[SearchHit, _Mapping]]] = ...) -> None: ...

class StatsRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class StatsResponse(_message.Message):
    __slots__ = ("size", "dim", "model", "m", "ef_construction", "ef_search")
    SIZE_FIELD_NUMBER: _ClassVar[int]
    DIM_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    M_FIELD_NUMBER: _ClassVar[int]
    EF_CONSTRUCTION_FIELD_NUMBER: _ClassVar[int]
    EF_SEARCH_FIELD_NUMBER: _ClassVar[int]
    size: int
    dim: int
    model: str
    m: int
    ef_construction: int
    ef_search: int
    def __init__(self, size: _Optional[int] = ..., dim: _Optional[int] = ..., model: _Optional[str] = ..., m: _Optional[int] = ..., ef_construction: _Optional[int] = ..., ef_search: _Optional[int] = ...) -> None: ...
