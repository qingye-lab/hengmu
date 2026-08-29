import importlib.util
import urllib.request


def run_extension(descriptor, task):
    urllib.request.urlretrieve(descriptor["url"], "/tmp/extension.py")
    spec = importlib.util.spec_from_file_location("extension", "/tmp/extension.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run(task, descriptor.get("permissions", ["*"]))
