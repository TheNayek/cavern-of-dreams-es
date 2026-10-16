"""Focused regression tests for cloned AssetBundle preload registration."""

import copy
import importlib.util
import os
import unittest


TOOLS = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("build_es", os.path.join(TOOLS, "build_es.py"))
build_es = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_es)


class FakeObject:
    def __init__(self, type_name, tree=None):
        self.type = type("Type", (), {"name": type_name})()
        self.tree = copy.deepcopy(tree)
        self.saved = []

    def read_typetree(self):
        return copy.deepcopy(self.tree)

    def save_typetree(self, data):
        self.saved.append(copy.deepcopy(data))
        self.tree = copy.deepcopy(data)


class FakeSerializedFile:
    def __init__(self, preload, container):
        self.objects = {
            1: FakeObject("AssetBundle", {"m_PreloadTable": preload, "m_Container": container})
        }

    @property
    def bundle(self):
        return self.objects[1]


def pp(path_id, file_id=0):
    return {"m_FileID": file_id, "m_PathID": path_id}


def entry(path, index, size, asset):
    return [path, {"preloadIndex": index, "preloadSize": size, "asset": asset}]


class RegisterPreloadTests(unittest.TestCase):
    def test_string_table_copies_four_dependencies_and_rewrites_only_self(self):
        preload = [pp(10), pp(11, 2), pp(12), pp(13, 3), pp(20)]
        container = [entry("source", 0, 4, pp(12))]
        sf = FakeSerializedFile(preload, container)

        build_es.register_in_container(sf, [("clone", -100, 12)])

        self.assertEqual(sf.bundle.saved[-1]["m_PreloadTable"], preload + [pp(10), pp(11, 2), pp(-100), pp(13, 3)])
        self.assertEqual(sf.bundle.tree["m_Container"], container + [entry("clone", 5, 4, pp(-100))])
        self.assertEqual(sf.bundle.saved[-1]["m_Container"][0], container[0])

    def test_locale_copies_two_object_segment(self):
        preload = [pp(30), pp(31), pp(40)]
        sf = FakeSerializedFile(preload, [entry("locale-en", 0, 2, pp(31))])

        build_es.register_in_container(sf, [("locale-es", -200, 31)])

        self.assertEqual(sf.bundle.tree["m_PreloadTable"], preload + [pp(30), pp(-200)])
        self.assertEqual(sf.bundle.tree["m_Container"][-1], entry("locale-es", 3, 2, pp(-200)))

    def test_multiple_clones_keep_each_distinct_shared_data_dependency(self):
        preload = [pp(1), pp(2), pp(3), pp(4), pp(5), pp(6)]
        container = [entry("a-en", 0, 3, pp(3)), entry("b-en", 3, 3, pp(6))]
        sf = FakeSerializedFile(preload, container)

        build_es.register_in_container(sf, [("a-es", -10, 3), ("b-es", -20, 6)])

        self.assertEqual(sf.bundle.tree["m_PreloadTable"], preload + [pp(1), pp(2), pp(-10), pp(4), pp(5), pp(-20)])
        self.assertEqual(sf.bundle.tree["m_Container"][2:], [entry("a-es", 6, 3, pp(-10)), entry("b-es", 9, 3, pp(-20))])
        self.assertEqual(sf.bundle.tree["m_Container"][:2], container)

    def test_missing_source_is_rejected_without_saving_bundle(self):
        sf = FakeSerializedFile([pp(1)], [entry("known", 0, 1, pp(1))])
        with self.assertRaises((KeyError, ValueError, RuntimeError)):
            build_es.register_in_container(sf, [("clone", -2, 999)])
        self.assertEqual(sf.bundle.saved, [])

    def test_invalid_source_range_is_rejected_without_saving_bundle(self):
        sf = FakeSerializedFile([pp(1)], [entry("broken", 0, 2, pp(1))])
        with self.assertRaises((IndexError, ValueError, RuntimeError)):
            build_es.register_in_container(sf, [("clone", -2, 1)])
        self.assertEqual(sf.bundle.saved, [])


if __name__ == "__main__":
    unittest.main()
