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
regional_overrides = {"India": {"demo.api.open_tasks": "demo.overrides.our_make"}}
permission_query_conditions = {"Demo Task": "demo.dispatch.task_query"}
before_request = ["demo.dispatch.check_request"]
jinja = {"methods": ["demo.dispatch.format_task"]}
after_install = "demo.dispatch.setup"
