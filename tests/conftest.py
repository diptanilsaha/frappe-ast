"""A tiny synthetic bench with one app that exercises every analyzer."""

import json
import subprocess
from pathlib import Path

import pytest

from frappe_ast.indexer import Indexer
from frappe_ast.store import Store
from frappe_ast.workspace import Bench

HOOKS = """
app_name = "demo"
closing_doctypes = ["Demo Task", "Demo Note"]
doc_events = {
	"*": {"validate": "demo.api.validate_any"},
	tuple(closing_doctypes): {"on_submit": ["demo.api.on_submit_any", "demo.api.missing_handler"]},
}
doc_events["Demo Note"] = {"on_update": "demo.api.validate_any"}
override_whitelisted_methods = {"frappe.client.get_count": "demo.api.get_count"}
required_apps = ["frappe/missing_app"]
doctype_js = {"Demo Note": "public/js/note.js"}
scheduler_events = {"daily": ["demo.api.daily"]}
"""

API = """
import frappe
from frappe import _
from frappe.utils import cint
from demo.demo.doctype.demo_task.demo_task import close_task as close
from typing import overload


@frappe.whitelist()
def get_count(doctype):
	return frappe.db.count(doctype)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def open_tasks(status):
	frappe.db.set_value("Demo Task", "T-1", "status", status)
	return frappe.db.sql(f"select name from `tabDemo Task` where status = '{status}'")


def validate_any(doc, method=None):
	close(doc.name)
	frappe.throw("Plain message")


def on_submit_any(doc, method=None):
	for row in doc.items:
		frappe.get_doc("Demo Note", row.note)


def api_version(kind):
	from frappe.api import ApiVersion

	match kind:
		case ApiVersion.V1:
			return 1


def daily():
	frappe.enqueue("demo.api.validate_any")
	frappe.publish_realtime("task_done", {"ok": 1})


@overload
def make(x: int) -> int: ...


@frappe.whitelist()
def make(x):
	return x


def not_exposed():
	pass


def install():
	fields = [{"fieldname": "priority", "fieldtype": "Data"}]
	create_custom_fields({"Demo Task": fields})
"""

CONTROLLER = """
import frappe
from frappe.model.document import Document


class DemoTask(Document):
	def validate(self):
		self.set_title()

	def set_title(self):
		pass

	@frappe.whitelist()
	def finish(self):
		frappe.db.commit()

	def private_helper(self):
		pass


@frappe.whitelist()
def close_task(name):
	frappe.get_doc("Demo Task", name).finish()
"""

FORM_JS = """
frappe.ui.form.on("Demo Task", {
	refresh(frm) {
		frm.call("finish");
		frm.call("private_helper");
		frm.call({ method: "close_task", args: { name: frm.doc.name } });
		frm.call({ method: "no_such_function" });
		frappe.call({ method: "demo.api.open_tasks", args: {} });
		frappe.xcall("demo.api.not_exposed");
		frappe.call("demo.api.gone");
		frm.trigger("recalculate");
		frm.events.helper(frm);
	},
	status(frm) {},
	recalculate(frm) {},
	helper(frm) {},
	ghost_field(frm) {},
});
frappe.listview_settings["Demo Task"] = { add_fields: ["status"] };

demo.TaskController = class TaskController extends demo.BaseController {
	status(doc) {}
	helper_not_an_event() {}
};
cur_frm.script_manager.make(demo.TaskController);
cur_frm.cscript.custom_refresh = function (doc) {};

frappe.ui.form.on("Demo Task", {
	setup(frm) {
		frm.set_value("statsu", "Open");
		frm.set_query("note", "items", () => ({}));
		frm.set_query("nope", "items", () => ({}));
		frm.add_fetch("note", "title", "title");
		frm.doc.progress = 1;
		if (frm.doc.progress && frm.doc.ghost) {}
		frappe.ui.form.make_control({ df: { fieldname: "picker", fieldtype: "HTML" } });
		frm.toggle_display("picker", true);
		frappe.realtime.on("task_done", () => {});
	},
});

frappe.ui.form.on("Demo Task", {
	onload(frm) {
		frappe.call({ method: "finish", doc: frm.doc });
		frappe.call({ method: "authorize", doc: connected_app });
		open_url_post("/api/method/demo.api.make", { x: 1 });
		window.open("/api/method/demo.api.make?x=" + frm.doc.name);
		window.location.href = `/api/method/demo.api.make?x=${frm.doc.name}`;
		fetch("/api/method/demo.api.open_tasks");
	},
});
"""

DESK_BASE_JS = """
demo.BaseController = class BaseController extends frappe.ui.form.Controller {
	refresh(doc) {}
	status(doc) {}
};
"""

REPORT_PY = """
class OpenTasksReport:
	@classmethod
	def execute(cls, filters=None):
		return [], []


execute = OpenTasksReport.execute
"""
REPORT_JS = """
frappe.query_reports["Open Taskz"] = {
	filters: [{ fieldname: "note", fieldtype: "Link", options: "Demo Note" }],
};
frappe.set_route("query-report", "Open Tasks");
"""
REPORT_JSON = {
	"doctype": "Report",
	"name": "Open Tasks",
	"ref_doctype": "Demo Task",
	"report_type": "Script Report",
	"is_standard": "Yes",
	"module": "Demo",
}
CARD_JSON = {
	"doctype": "Number Card",
	"name": "Open Task Count",
	"document_type": "Demo Task",
	"filters_json": '[["Demo Task","status","=","Open"],["Demo Task","colour","=","x"]]',
}
NOTE_ITEM_JSON = {
	"doctype": "DocType",
	"name": "Demo Note",
	"module": "Demo",
	"istable": 1,
	"fields": [{"fieldname": "note", "fieldtype": "Data"}, {"fieldname": "title", "fieldtype": "Data"}],
}

VUE = """<template>
  <div>
    <TaskList :tasks="tasks.data" @select="openTask" />
    <Button @click="save()">Save</Button>
  </div>
</template>

<script setup lang="ts">
import { Button, createResource, createListResource, useCall } from 'frappe-ui'
import TaskList from './TaskList.vue'

const tasks = createListResource({ doctype: 'Demo Task', fields: ['name'] })
const counts = createResource({ url: 'demo.api.get_count' })
const posted = useCall({ url: '/api/v2/method/demo.api.open_tasks', method: 'POST' })

function openTask(name: string) {}
function save() {}
</script>
"""

# frappe-ui data calls as apps write them: handles, doc methods, constants, client methods, fields.
DATA_TS = """
import { call, createResource, createListResource, useDoctype, useDoc } from 'frappe-ui'
import { API } from './api'
export const COUNT_URL = 'demo.api.get_count'
const tasks = useDoctype('Demo Task')

export function finishAll() {
	tasks.runDocMethod.submit({ name: 'T-1', method: 'finish' })
	tasks.runDocMethod.submit({ name: 'T-1', method: 'private_helper' })
	tasks.runMethod.submit({ method: 'close_task' })
	useDoc({ doctype: 'Demo Task', name: 'T-1', methods: { done: 'finish', gone: { name: 'vanished' } } })
	createResource({ url: COUNT_URL })
	call(API.open, {})
	call('frappe.client.set_value', { doctype: 'Demo Task', name: 'T-1', fieldname: { status: 'Closed', priority: 'High' } })
	createListResource({
		doctype: 'Demo Task',
		fields: ['name', 'status', 'statsu', 'items.note', 'count(name) as n'],
		filters: { owner: 'x', color: 'red' },
	})
}
"""
API_TS = "export const API = { open: 'demo.api.open_tasks' } as const\n"
FUI_TYPES = "export const buttonProps = { label: String, theme: String }\nexport interface DialogProps { open?: boolean; title: string }\n"
FUI_DIALOG = """<template><div><slot /></div></template>
<script setup lang="ts">
import type { DialogProps } from '../Button/types'
defineProps<DialogProps>()
defineEmits<{ close: [] }>()
</script>
"""

FUI_BUTTON = """<template><button><slot /></button></template>
<script lang="ts">
import { defineComponent } from 'vue'
import { buttonProps } from './types'
export default defineComponent({ name: 'Button', props: buttonProps, setup() {} })
</script>
"""

TASK_JSON = {
	"doctype": "DocType",
	"name": "Demo Task",
	"module": "Demo",
	"is_submittable": 1,
	"search_fields": "status",
	"sort_field": "creation",
	"fields": [
		{"fieldname": "status", "fieldtype": "Select", "options": "Open\nClosed", "reqd": 1},
		{"fieldname": "note", "fieldtype": "Link", "options": "Demo Note", "depends_on": "eval:doc.status"},
		{"fieldname": "items", "fieldtype": "Table", "options": "Demo Note"},
		{"fieldname": "cost", "fieldtype": "Currency", "permlevel": 1, "set_only_once": 1, "default": "0"},
	],
	"permissions": [
		{"role": "System Manager", "read": 1, "write": 1},
		{"role": "Demo User", "read": 1, "create": 1, "if_owner": 1},
		{"role": "System Manager", "permlevel": 1, "read": 1, "write": 1},
	],
	"links": [{"link_doctype": "Demo Note", "link_fieldname": "task", "group": "Notes"}],
	"actions": [{"label": "Close", "action": "demo.api.close", "action_type": "Server Action"}],
	"states": [{"title": "Open", "color": "Blue"}],
}


@pytest.fixture(scope="session")
def bench_root(tmp_path_factory) -> Path:
	root = tmp_path_factory.mktemp("bench")
	app = root / "apps" / "demo"
	pkg = app / "demo"
	files = {
		"sites/apps.txt": "demo\n",
		"apps/demo/demo/__init__.py": "",
		"apps/demo/demo/hooks.py": HOOKS,
		"apps/demo/demo/modules.txt": "Demo\n",
		"apps/demo/demo/api.py": API,
		"apps/demo/demo/demo/doctype/demo_task/demo_task.py": CONTROLLER,
		"apps/demo/demo/demo/doctype/demo_task/demo_task.js": FORM_JS,
		"apps/demo/demo/demo/doctype/demo_task/demo_task.json": json.dumps(TASK_JSON),
		"apps/demo/demo/demo/doctype/demo_note/demo_note.json": json.dumps(NOTE_ITEM_JSON),
		"apps/demo/demo/public/js/base_controller.js": DESK_BASE_JS,
		"apps/demo/demo/demo/report/open_tasks/open_tasks.json": json.dumps(REPORT_JSON),
		"apps/demo/demo/demo/report/open_tasks/open_tasks.py": REPORT_PY,
		"apps/demo/demo/demo/report/open_tasks/open_tasks.js": REPORT_JS,
		"apps/demo/demo/demo/number_card/open_task_count/open_task_count.json": json.dumps(CARD_JSON),
		"apps/demo/frontend/src/pages/Tasks.vue": VUE,
		"apps/demo/frontend/src/data/tasks.ts": DATA_TS,
		"apps/demo/frontend/src/data/api.ts": API_TS,
		"apps/demo/demo/fixtures/custom_field.json": json.dumps([{"dt": "Demo Task", "fieldname": "color"}]),
		"apps/demo/frontend/package.json": json.dumps({"dependencies": {"frappe-ui": "^9.9.0"}}),
		# yarn's copy of the library; its src is indexed as frappe-ui@9.9.9.
		"apps/demo/frontend/node_modules/frappe-ui/package.json": json.dumps(
			{"name": "frappe-ui", "version": "9.9.9"}
		),
		"apps/demo/frontend/node_modules/frappe-ui/src/components/Button/Button.vue": FUI_BUTTON,
		"apps/demo/frontend/node_modules/frappe-ui/src/components/Button/types.ts": FUI_TYPES,
		"apps/demo/frontend/node_modules/frappe-ui/src/components/Button/Button.test.ts": "call('x.y.z')\n",
		"apps/demo/frontend/node_modules/frappe-ui/src/components/Dialog/Dialog.vue": FUI_DIALOG,
		"apps/demo/frontend/node_modules/frappe-ui/src/resources/resources.js": "export function createResource(options, vm) {}\n",
		"apps/demo/frontend/node_modules/frappe-ui/node_modules/dep/index.js": "export const skipped = 1\n",
	}
	for rel, text in files.items():
		path = root / rel
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(text)
	assert pkg.is_dir()
	_git(app, "init", "-q")
	_git(app, "add", ".")
	_git(app, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
	return root


@pytest.fixture(scope="session")
def store(bench_root, tmp_path_factory) -> Store:
	store = Store(tmp_path_factory.mktemp("db") / "index.sqlite")
	bench = Bench(bench_root)
	Indexer(bench, store, jobs=1).run(bench.apps(), log=lambda _: None)
	return store


def _git(repo: Path, *args: str):
	subprocess.run(["git", "-C", str(repo), *args], check=True)
