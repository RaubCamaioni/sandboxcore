from pathlib import Path, PosixPath
from typing import Any
import json
import io


class JsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        if isinstance(obj, (Path, PosixPath)):
            return {"__path__": str(obj.absolute())}
        return super().default(obj)


class JsonDecoder(json.JSONDecoder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, object_hook=self.object_hook)

    def object_hook(self, obj):
        if "__path__" in obj:
            return Path(obj["__path__"])
        return obj


def dumps(objects: Any) -> str:
    return json.dumps(objects, cls=JsonEncoder)


def loads(serial: str) -> Any:
    return json.loads(serial, cls=JsonDecoder)


def dump(objects: Any, file: io.FileIO) -> str:
    json.dump(objects, file, cls=JsonEncoder)


def load(file: io.FileIO) -> Any:
    return json.load(file, cls=JsonDecoder)
