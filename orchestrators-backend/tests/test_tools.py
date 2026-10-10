import json
import unittest
from types import SimpleNamespace

from pydantic import BaseModel

from orchestration import Toolbox


class SavePicture(BaseModel):
    project_id: str
    note: str | None = None


def toolbox_with_save():
    box = Toolbox()
    saved = []

    @box.tool("save_picture", "Store the strategy picture of a project.", SavePicture)
    def save_picture(args: SavePicture) -> dict:
        saved.append(args)
        return {"ok": True, "project_id": args.project_id}

    return box, saved


class ToolboxTests(unittest.TestCase):
    def test_declaration_is_a_gemini_function_tool(self):
        box, _ = toolbox_with_save()
        (decl,) = box.declarations()
        self.assertEqual(decl["type"], "function")
        self.assertEqual(decl["name"], "save_picture")
        params = decl["parameters"]
        self.assertEqual(params["required"], ["project_id"])
        self.assertEqual(params["properties"]["note"]["type"], ["string", "null"])
        self.assertNotIn("title", params)

    def test_call_validates_args_and_returns_a_function_result(self):
        box, saved = toolbox_with_save()
        step = SimpleNamespace(id="c1", name="save_picture", arguments={"project_id": "p"})
        result = box.call(step)
        self.assertEqual(result["type"], "function_result")
        self.assertEqual(result["call_id"], "c1")
        self.assertFalse(result["is_error"])
        self.assertEqual(json.loads(result["result"]), {"ok": True, "project_id": "p"})
        self.assertEqual(saved[0].project_id, "p")

    def test_failures_go_back_to_the_model(self):
        box, saved = toolbox_with_save()
        bad_args = box.call({"id": "c2", "name": "save_picture", "arguments": {}})
        unknown = box.call({"id": "c3", "name": "nope", "arguments": {}})
        self.assertTrue(bad_args["is_error"])
        self.assertIn("ValidationError", bad_args["result"])
        self.assertTrue(unknown["is_error"])
        self.assertIn("save_picture", unknown["result"])  # lists what exists
        self.assertEqual(saved, [])

    def test_names_are_unique(self):
        box, _ = toolbox_with_save()
        with self.assertRaises(ValueError):
            box.tool("save_picture", "again", SavePicture)(lambda args: None)


if __name__ == "__main__":
    unittest.main()
